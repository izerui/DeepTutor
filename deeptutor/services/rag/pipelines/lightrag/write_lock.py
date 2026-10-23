"""One nonblocking writer per knowledge base, shared across worker processes."""

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import errno
import hashlib
import os
from pathlib import Path
import threading

from .indexing_policy import IndexingPolicyError


class WriteOwnershipBusy(IndexingPolicyError):
    """Another writer holds the KB; admission may retry without occupying a worker."""


@dataclass
class _Ownership:
    key: str
    pid: int
    thread: int
    task: object
    active: bool = True


_ownerships: ContextVar[tuple[_Ownership, ...]] = ContextVar("kb_write_ownerships", default=())


def _current_task() -> asyncio.Task | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def owns_write_ownership(kb_dir: Path) -> bool:
    """Allow nested document work only in the actual owner, not inherited child contexts."""
    key = str(Path(kb_dir).resolve())
    return any(
        owner.active
        and owner.key == key
        and owner.pid == os.getpid()
        and owner.thread == threading.get_ident()
        and owner.task is _current_task()
        for owner in _ownerships.get()
    )


@contextmanager
def write_ownership(kb_dir: Path) -> Iterator[None]:
    """Hold exclusive nonblocking write ownership for a knowledge base.

    Yields:
        None while the cross-process indexing lock is held.

    Raises:
        IndexingPolicyError: The target uses a symlink or another writer owns it."""
    kb_dir = Path(kb_dir)
    if kb_dir.is_symlink():
        raise IndexingPolicyError("A writable knowledge-base directory cannot be a symbolic link.")
    lock_dir = kb_dir.parent / ".lightrag-locks"
    if lock_dir.is_symlink():
        raise IndexingPolicyError("The indexing lock directory cannot be a symbolic link.")
    lock_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(str(kb_dir.resolve()).encode("utf-8")).hexdigest()
    lock_path = lock_dir / f"{key}.lock"
    if lock_path.is_symlink():
        raise IndexingPolicyError("The indexing lock cannot be a symbolic link.")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "r+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}:
                raise
            raise WriteOwnershipBusy(
                "Another indexing operation is active for this knowledge base; resubmit after it finishes."
            ) from exc
        owner = _Ownership(
            str(kb_dir.resolve()), os.getpid(), threading.get_ident(), _current_task()
        )
        token = _ownerships.set((*_ownerships.get(), owner))
        try:
            yield
        finally:
            owner.active = False
            _ownerships.reset(token)
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
