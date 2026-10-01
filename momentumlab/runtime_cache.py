"""Bounded transient caches. File identity changes miss immediately; no disk deletion."""
from collections import OrderedDict
from pathlib import Path
import weakref
import threading
import time
_CACHES=weakref.WeakSet()


def file_identity(path):
    p=Path(path).resolve()
    try:
        s=p.stat();return (str(p),s.st_mtime_ns,s.st_ctime_ns,s.st_size,s.st_ino)
    except OSError:return (str(p),None)


class RuntimeCache:
    def __init__(self,max_bytes=32*1024*1024,max_entries=24,ttl=120):
        self.max_bytes=max_bytes;self.max_entries=max_entries;self.ttl=ttl
        self._items=OrderedDict();self._lock=threading.RLock();self._timer=None
        _CACHES.add(self)

    def get(self,key,default=None):
        with self._lock:
            self._expire()
            item=self._items.get(key)
            if item is None:return default
            self._items.move_to_end(key);return item[2]

    def _expire(self):
        now=time.monotonic()
        for key,(expiry,_,_) in list(self._items.items()):
            if expiry<=now:self._items.pop(key,None)

    def _sweep(self):
        with self._lock:
            self._expire();self._timer=None
            self._schedule()

    def _schedule(self):
        if self._timer is None and self._items:
            delay=max(.01,min(v[0] for v in self._items.values())-time.monotonic())
            self._timer=threading.Timer(delay,self._sweep);self._timer.daemon=True;self._timer.start()

    def put(self,key,value,size):
        with self._lock:
            self._expire();self._items.pop(key,None)
            if size>self.max_bytes:return
            while self._items and (len(self._items)>=self.max_entries or sum(v[1] for v in self._items.values())+size>self.max_bytes):
                self._items.popitem(last=False)
            self._items[key]=(time.monotonic()+self.ttl,size,value);self._schedule()

    def clear(self):
        with self._lock:
            self._items.clear()
            if self._timer:self._timer.cancel();self._timer=None

    def items(self):
        with self._lock:
            self._expire();return [(k,v[2]) for k,v in self._items.items()]

    def __setitem__(self,key,value):
        # Compatibility with the existing one-market cache's (timestamp, market) value.
        size=sum(int(v.memory_usage(deep=True).sum()) for v in value[1].values() if hasattr(v,'memory_usage'))
        self.put(key,value,size)


SMALL_FILES=RuntimeCache()
STATUS=RuntimeCache(max_bytes=1024*1024,max_entries=48,ttl=300)


def read_small_or_direct(path,reader):
    identity=file_identity(path)
    # Avoid creating a second resident copy of large historical price files.
    if identity[-1] is None or Path(path).stat().st_size>2*1024*1024:return reader(path,low_memory=False)
    cached=SMALL_FILES.get(identity)
    if cached is not None:return cached.copy(deep=True)
    frame=reader(path,low_memory=False)
    if identity==file_identity(path):SMALL_FILES.put(identity,frame.copy(deep=True),int(frame.memory_usage(deep=True).sum()))
    return frame


def clear_small_caches():
    for cache in list(_CACHES):cache.clear()
