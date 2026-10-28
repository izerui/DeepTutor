"""Process-local caches of parsed files that are re-validated on every use.

An entry is served only while the file's identity and stat fingerprint are
unchanged, so another process's commit is seen by the very next read. Content
read within ``RACY_WINDOW_NS`` of its last change (the later of mtime and
ctime) is never trusted afterwards ("racy" in git's sense): a coarse timestamp
tick could otherwise hide a second same-size write. ``st_ctime_ns`` cannot be
set by callers, so ``utime`` cannot mask a rewrite either.

A file whose committed state can live in a sidecar (an SQLite ``-wal``) is
only cached while no such sidecar exists; see ``FileSnapshotCache.load``.

Known limits (accepted, not detected):

- The path is stat'ed through symlinks. Re-pointing a symlink at a different
  file is seen (new ``st_dev``/``st_ino``), but replacing a symlink in a
  parent directory so the same inode is reached by another route is not a
  change at all, and re-pointing back to a previously cached inode serves it.
- On NFS and similar network filesystems, ``stat`` may come from the
  client's attribute cache, so another host's write can stay invisible for
  the attribute-cache lifetime (``actimeo``) regardless of this cache.
- The racy window compares the server's file timestamps with this host's
  clock. Clock skew larger than ``RACY_WINDOW_NS`` between them (or a clock
  stepped backwards) can make a freshly written file look settled.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
import threading
import time
from typing import Any, Callable, Iterable

RACY_WINDOW_NS = 2_000_000_000


def _fingerprint(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode,
    )


class FileSnapshotCache:
    def __init__(self) -> None:
        self._entries: dict[str, tuple[tuple[int, ...], Any]] = {}
        self._lock = threading.Lock()

    def load(
        self,
        path: Path,
        loader: Callable[[Path], Any],
        *,
        sidecars: Iterable[str] = (),
    ) -> Any:
        """Return ``loader(path)``, reusing a value read from an identical file.

        ``sidecars`` name suffixes (e.g. ``"-wal"``) of companion files that
        may hold committed content without changing ``path`` itself. While
        any exists, nothing is cached and every call reads afresh. Only the
        main file is fingerprinted, so this cache is sound for SQLite files
        only in rollback (``DELETE``) journal mode, where every commit rewrites
        the database file before it becomes visible.

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
        if any(Path(f"{path}{suffix}").exists() for suffix in sidecars):
            self.discard(path)
            return loader(path)
        fingerprint = _fingerprint(info)
        with self._lock:
            cached = self._entries.get(key)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        value = loader(path)
        changed_ns = max(info.st_mtime_ns, info.st_ctime_ns)
        stable = stat.S_ISREG(info.st_mode) and (
            time.time_ns() - changed_ns > RACY_WINDOW_NS
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

    def retain(self, paths: Iterable[Path]) -> None:
        """Drop every entry whose path is not in ``paths``."""
        keep = {str(path) for path in paths}
        with self._lock:
            for key in [key for key in self._entries if key not in keep]:
                del self._entries[key]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
