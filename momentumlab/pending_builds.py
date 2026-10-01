"""Retained full-build candidates. Review never redirects production research."""
import hashlib
import json
import shutil
import uuid
from pathlib import Path
from datetime import datetime, timezone
from .data_integrity import (DATA_LOCK, staged_dataset, validate_dataset, require_valid,
                             publish_dataset, recover_publication, audit_event)
from .serialization import dumps


def identities(paths):
    result = {}
    for key, value in paths.items():
        if key == 'root':
            continue
        path = Path(value)
        if not path.is_file():
            result[key] = None
            continue
        h = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(block)
        result[key] = h.hexdigest()
    return result


def targets(paths):
    from .completeness import evidence_paths
    evidence={key:str(path) for key,path in evidence_paths(paths).items() if path.is_file()}
    return {**paths, **evidence, 'review_acknowledgments': str(Path(paths['root'])/'completeness_acknowledgments.json')}


def write(state, root):
    path = Path(root)/'build.json'
    temp = path.with_suffix('.tmp')
    state['updated_at'] = datetime.now(timezone.utc).isoformat()
    temp.write_text(dumps(state, indent=2), encoding='utf-8')
    temp.replace(path)


def create(paths, universe, request):
    build_id = uuid.uuid4().hex
    root = Path(paths['root'])/'pending_builds'/build_id
    root.mkdir(parents=True)
    work = {'root': str(root)}
    source = targets(paths)
    filenames = {}
    for key, value in source.items():
        if key == 'root':
            continue
        name = 'completeness_acknowledgments.json' if key == 'review_acknowledgments' else key+'_'+Path(value).name
        filenames[key] = name
        work[key] = str(root/name)
        if Path(value).is_file():
            shutil.copy2(value, root/name)
    state = dict(id=build_id, universe=universe, status='DOWNLOADING', download_complete=False,
                 request=request, files=filenames, baseline=identities(source),
                 created_at=datetime.now(timezone.utc).isoformat())
    write(state, root)
    return state, work


def load(paths, universe, build_id):
    if len(build_id) != 32 or any(c not in '0123456789abcdef' for c in build_id):
        raise ValueError('Invalid retained build ID.')
    root = Path(paths['root'])/'pending_builds'/build_id
    state = json.loads((root/'build.json').read_text(encoding='utf-8'))
    if state['universe'] != universe:
        raise ValueError('Retained build belongs to another universe.')
    work = {'root': str(root)}
    for key, name in state['files'].items():
        if Path(name).name != name:
            raise ValueError('Invalid retained build path.')
        work[key] = str(root/name)
    recover_publication(work)
    return state, work


def list_builds(paths, universe):
    rows = []
    for path in (Path(paths['root'])/'pending_builds').glob('*/build.json'):
        state = json.loads(path.read_text(encoding='utf-8'))
        if state['universe'] == universe:
            rows.append({k: state.get(k) for k in ('id','status','download_complete','created_at','updated_at','error')})
    return sorted(rows, key=lambda x:x['created_at'], reverse=True)


def review_paths(paths, universe, build_id):
    if not build_id:
        return paths
    state, work = load(paths, universe, build_id)
    if not state['download_complete']:
        raise ValueError('This build stopped during downloading; it is not ready for integrity review.')
    if state['status'] == 'PUBLISHED':
        raise ValueError('This build was published. Select Production to review current data.')
    return work


def publish(paths, universe, build_id, progress=lambda **kw:None):
    with DATA_LOCK:
        state, work = load(paths, universe, build_id)
        if state['status'] == 'PUBLISHED':
            raise ValueError('This build has already been published.')
        if not state['download_complete']:
            raise ValueError('Download did not complete; publication is blocked.')
        if identities(targets(paths)) != state['baseline']:
            raise ValueError('Production data or confirmations changed after this build started. Publication blocked to avoid overwriting newer work.')
        progress(stage='VALIDATING', message='Revalidating retained files and recorded confirmations before publication.')
        report = require_valid(validate_dataset(work, state['request']['end_date'], universe_id=universe, mode='full'))
        # Publish copies, retaining the reviewed candidate and its audit/export history.
        with staged_dataset(work) as copy:
            progress(stage='COMMITTING', message='Publishing validated files with rollback backup.')
            backup = publish_dataset(copy, targets(paths), report['manifest'])
        state.update(status='PUBLISHED', backup=backup, error=None)
        write(state, work['root'])
        audit_event(paths['root'], 'RETAINED_BUILD_PUBLISHED', universe, build_id=build_id, backup=backup,
                    review_history=str(Path(work['root'])/'integrity_audit.jsonl'))
        progress(stage='REFRESHING_REPORT', message='Refreshing production validation.')
        final = validate_dataset(paths, universe_id=universe, mode='full')
        return dict(ok=True, build_id=build_id, backup=backup, status=final['status'])
