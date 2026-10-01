"""Lossless in-place NTFS compression of explicitly previewed, aged run files."""
import os
import stat
import subprocess
import time
import uuid
import json
from pathlib import Path

_plans={}


def disk_bytes(path):
    if os.name!='nt':return path.stat().st_size
    import ctypes
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    fn=api.GetCompressedFileSizeW;fn.argtypes=[ctypes.c_wchar_p,ctypes.POINTER(ctypes.c_uint32)];fn.restype=ctypes.c_uint32
    high=ctypes.c_uint32();ctypes.set_last_error(0);low=fn(str(path),ctypes.byref(high))
    if low==0xffffffff and ctypes.get_last_error():raise OSError(ctypes.get_last_error(),'Cannot measure stored file size')
    return (high.value<<32)|low


def identity(path):
    s=path.stat();return (s.st_size,s.st_mtime_ns)


def safe_files(folder,root):
    if folder.is_symlink() or getattr(folder.lstat(),'st_file_attributes',0)&0x400:return
    for base,dirs,files in os.walk(folder,followlinks=False):
        dirs[:]=[d for d in dirs if not (Path(base)/d).is_symlink() and not getattr((Path(base)/d).lstat(),'st_file_attributes',0)&0x400]
        for name in files:
            p=Path(base)/name
            if p.is_symlink() or getattr(p.lstat(),'st_file_attributes',0)&0x400:continue
            resolved=p.resolve()
            if root not in resolved.parents:continue
            if stat.S_ISREG(p.stat().st_mode):yield p


def preview(results,days):
    days=int(days)
    if not 1<=days<=36500:raise ValueError('Choose an age of at least 1 day')
    root=(Path(results)/'backtests').resolve();cutoff=time.time()-days*86400
    runs=[];planned=[]
    if root.is_dir():
        for run in sorted(root.iterdir()):
            if not run.is_dir() or run.is_symlink() or getattr(run.lstat(),'st_file_attributes',0)&0x400 or not (run/'run_metadata.json').is_file():continue
            files=list(safe_files(run,root))
            if not files or max(p.stat().st_mtime for p in files)>cutoff:continue
            pending=[p for p in files if p.stat().st_size and not getattr(p.stat(),'st_file_attributes',0)&(0x800|0x4000)]
            if not pending:continue
            records=[dict(path=str(p),identity=identity(p)) for p in pending]
            planned.extend(records);runs.append(dict(run=run.name,files=len(pending),logical_bytes=sum(p.stat().st_size for p in pending)))
    token=uuid.uuid4().hex
    _plans[token]=dict(root=str(root),files=planned,created=time.time(),days=days)
    while len(_plans)>8:_plans.pop(next(iter(_plans)))
    return dict(token=token,days=days,runs=runs,file_count=len(planned),logical_bytes=sum(r['logical_bytes'] for r in runs),supported=os.name=='nt')


def _compress(paths):
    # Explicit files only: no recursive shell command, deletion, move or archive.
    exe=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32'/'compact.exe'
    r=subprocess.run([str(exe),'/C','/I','/Q','/A',*map(str,paths)],capture_output=True,timeout=180,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if r.returncode:raise RuntimeError('Windows compression failed. Check folder permissions and NTFS support; original files remain in place.')


def apply(results,token,progress):
    if os.name!='nt':raise ValueError('This function requires Windows NTFS file compression')
    plan=_plans.pop(token,None)
    root=(Path(results)/'backtests').resolve()
    if not plan or time.time()-plan['created']>1800 or plan['root']!=str(root):raise ValueError('Preview expired; preview older runs again')
    files=plan['files'];total=len(files);done=0;compressed=0;skipped=0;saved=0;errors=[]
    for offset in range(0,total,24):
        batch=[];before={}
        for record in files[offset:offset+24]:
            p=Path(record['path'])
            try:
                if p.resolve()!=p or root not in p.resolve().parents or identity(p)!=tuple(record['identity']):raise ValueError('File changed since preview')
                if any(getattr(parent.lstat(),'st_file_attributes',0)&0x400 for parent in [p,*p.parents] if parent!=root and root in parent.parents):raise ValueError('Linked path skipped')
                before[str(p)]=disk_bytes(p);batch.append(p)
            except (OSError,ValueError):skipped+=1
        progress(stage='COMPRESSING',current=done,total=total,message=f'Compressing batch; {done}/{total} files processed')
        try:
            if batch:_compress(batch)
        except (OSError,RuntimeError,subprocess.TimeoutExpired) as exc:errors.append(str(exc))
        for p in batch:
            try:
                if getattr(p.stat(),'st_file_attributes',0)&0x800:
                    compressed+=1;saved+=max(0,before[str(p)]-disk_bytes(p))
                else:errors.append('Not compressed: '+p.name+' (check filesystem support or permissions)')
            except OSError as exc:errors.append(str(exc))
        done=min(offset+24,total);progress(current=done,total=total,message=f'{done}/{total} processed; {compressed} compressed; {skipped} changed/unavailable skipped')
    result=dict(processed=done,compressed=compressed,skipped=skipped,bytes_saved=saved,errors=errors,status='partial' if errors or skipped else 'completed',timestamp=time.time(),days=plan['days'])
    progress(stage='SAVING AUDIT',current=done,total=total)
    with (Path(results)/'result_compression_audit.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(dict(operation=token,**result))+'\n')
    return result
