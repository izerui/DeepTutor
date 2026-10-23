"""Coordinate raw snapshots and compensated mutations within this process."""

import asyncio
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
import threading
from weakref import WeakValueDictionary

_locks = WeakValueDictionary()
_guard = threading.Lock()


class DocumentRecordingError(RuntimeError):
    """Required document persistence failed; optional bookkeeping cannot hide it."""


def _lock(base_dir, name):
    key = str((Path(base_dir) / name).resolve())
    with _guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


@contextmanager
def document_mutation(base_dir, name):
    """Use on workers, covering snapshot/publication through commit or compensation."""
    with _lock(base_dir, name):
        yield


@asynccontextmanager
async def async_document_mutation(base_dir, name):
    """Wait without blocking the loop or occupying workers needed by the owner."""
    lock = _lock(base_dir, name)
    while not lock.acquire(blocking=False):
        await asyncio.sleep(0.01)
    try:
        yield
    finally:
        lock.release()


async def finish_document_worker(function, *args):
    """Cancellation must not release ownership while a mutating worker still runs."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Repeated cancellation must not cancel the join or release the KB lock.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if not task.cancelled():
            task.exception()
        raise
