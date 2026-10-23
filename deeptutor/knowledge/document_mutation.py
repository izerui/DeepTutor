"""Coordinate raw snapshots and compensation with native cross-process KB ownership."""

import asyncio
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
import threading
import time
from weakref import WeakValueDictionary

from deeptutor.services.rag.pipelines.lightrag.write_lock import (
    WriteOwnershipBusy,
    owns_write_ownership,
    write_ownership,
)

_locks = WeakValueDictionary()
_guard = threading.Lock()
_worker_ownership = threading.local()


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
    kb_dir = Path(base_dir) / name
    if (
        getattr(_worker_ownership, "key", None) == str(kb_dir.resolve())
        or owns_write_ownership(kb_dir)
    ):
        yield
        return
    with _lock(base_dir, name):
        while True:
            ownership = write_ownership(kb_dir)
            try:
                ownership.__enter__()
                break
            except WriteOwnershipBusy:
                time.sleep(0.01)
        try:
            yield
        finally:
            ownership.__exit__(None, None, None)


@asynccontextmanager
async def async_document_mutation(base_dir, name):
    """Wait without blocking the loop or occupying workers needed by the owner."""
    kb_dir = Path(base_dir) / name
    # Native publication already holds the file lock. Do not wait behind a local
    # contender that is itself waiting for that same file lock.
    if owns_write_ownership(kb_dir):
        yield
        return
    lock = _lock(base_dir, name)
    while not lock.acquire(blocking=False):
        await asyncio.sleep(0.01)
    try:
        while True:
            ownership = write_ownership(kb_dir)
            try:
                ownership.__enter__()
                break
            except WriteOwnershipBusy:
                await asyncio.sleep(0.01)
        try:
            yield
        finally:
            ownership.__exit__(None, None, None)
    finally:
        lock.release()


async def run_document_worker(base_dir, kb_name, function, /, *args, **kwargs):
    """Admit async mutations before dispatch, leaving executor capacity for the owner."""
    key = str((Path(base_dir) / kb_name).resolve())

    def admitted():
        # Only this worker may reuse the admission; child tasks/threads cannot inherit it.
        previous = getattr(_worker_ownership, "key", None)
        _worker_ownership.key = key
        try:
            return function(*args, **kwargs)
        finally:
            _worker_ownership.key = previous

    async with async_document_mutation(base_dir, kb_name):
        return await finish_document_worker(admitted)


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


async def publish_source_documents(base_dir, kb_name, writes, removals=()):
    """Publish prepared source bytes and required records before entering native indexing."""
    def publish():
        from deeptutor.knowledge import registry
        from deeptutor.knowledge.add_documents import remove_raw_document

        kb_dir = Path(base_dir) / kb_name
        raw = (kb_dir / "raw").resolve()
        targets = {}
        for path in (*writes, *removals):
            target = Path(path).resolve()
            if target == raw or raw not in target.parents:
                raise ValueError(f"Source path escapes the knowledge base: {path}")
            targets[path] = target
        # Backups are taken only after admission, so compensation cannot restore
        # bytes older than another successful writer's publication.
        metadata = kb_dir / "metadata.json"
        backups = {
            target: target.read_bytes() if target.exists() else None
            for target in {*targets.values(), metadata}
        }
        removed_count = sum(backups[targets[path]] is not None for path in removals)
        try:
            for path, content in writes.items():
                target = targets[path]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            for path in removals:
                remove_raw_document(kb_dir, targets[path])
            registry.refresh_documents(base_dir, kb_name)
        except BaseException:
            for target, content in backups.items():
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(content)
            raise
        return removed_count

    return await run_document_worker(base_dir, kb_name, publish)
