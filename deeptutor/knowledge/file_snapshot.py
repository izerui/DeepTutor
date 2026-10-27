"""Process-local caches of parsed files that are re-validated on every use.

An entry is served only while the file's identity and stat fingerprint are
unchanged, so another process's commit is seen by the very next read. Content
read within ``RACY_WINDOW_NS`` of its mtime is never trusted afterwards
("racy" in git's sense): a coarse timestamp tick could otherwise hide a second
same-size write. ``st_ctime_ns`` cannot be set by callers, so ``utime`` cannot
mask a rewrite either.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
import threading
import time
from typing import Any, Callable

RACY_WINDOW_NS = 2_000_000_000


def _fingerprint(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode,
    )


class FileSnapshotCache:
    def __init__(self) -> None:
        self._entries: dict[str, tuple[tuple[int, ...], Any]] = {}
        self._lock = threading.Lock()

    def load(self, path: Path, loader: Callable[[Path], Any]) -> Any:
        """Return ``loader(path)``, reusing a value read from an identical file.

        Errors (including ``FileNotFoundError`` from ``stat``) propagate and
        are never cached.
        """
        key = str(path)
        try:
            # Stat *before* reading: a concurrent write changes the fingerprint.
            info = path.stat()
        except OSError:
            self.discard(path)
            raise
        fingerprint = _fingerprint(info)
        with self._lock:
            cached = self._entries.get(key)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        value = loader(path)
        stable = stat.S_ISREG(info.st_mode) and (
            time.time_ns() - info.st_mtime_ns > RACY_WINDOW_NS
        )
        with self._lock:
            if stable:
                self._entries[key] = (fingerprint, value)
            else:
                self._entries.pop(key, None)
        return value

    def discard(self, path: Path) -> None:
        with self._lock:
            self._entries.pop(str(path), None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
