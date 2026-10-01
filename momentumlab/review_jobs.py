"""One observable local bulk operation; all application mutations remain serialized."""
import copy
import threading
import traceback
import uuid
from datetime import datetime, timezone

_lock=threading.RLock()
_job=dict(status='idle')

def snapshot():
    with _lock:return copy.deepcopy(_job)

def start(operation, mutation_lock, *, kind='review', metadata=None):
    with _lock:
        if _job['status']=='running':raise ValueError('A bulk integrity operation is already running.')
        _job.clear();_job.update(id=uuid.uuid4().hex,kind=kind,metadata=metadata or {},status='running',stage='QUEUED',current=0,total=None,started_at=datetime.now(timezone.utc).isoformat())
    def progress(**values):
        with _lock:_job.update(values,updated_at=datetime.now(timezone.utc).isoformat())
    def run():
        try:
            with mutation_lock:result=operation(progress)
            progress(status='completed',result=result,stage='COMPLETED',finished_at=datetime.now(timezone.utc).isoformat())
        except Exception as exc:progress(status='failed',error=str(exc),traceback=traceback.format_exc(),stage='FAILED',finished_at=datetime.now(timezone.utc).isoformat())
    threading.Thread(target=run,daemon=True).start()
    return snapshot()
