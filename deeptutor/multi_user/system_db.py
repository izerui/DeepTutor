"""One small system-level SQLite database (spec 2026-11-02 §14).

The contract of the class, publish-scope and practice-set stores, shared by
the stores added since: DELETE journal with FULL synchronous, ``BEGIN
IMMEDIATE`` writers that never nest, read-only connections for reads, and no
file created by a read.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import threading


class SystemDb:
    def __init__(self, filename: str, schema: str, label: str) -> None:
        self.filename = filename
        self.schema = schema
        self.label = label
        #: Tests may point this at a temporary file; ``None`` means ``<SYSTEM_ROOT>/<filename>``.
        self.path_override: Path | None = None
        self._write_lock = threading.Lock()
        self._held = threading.local()

    def path(self) -> Path:
        if self.path_override is not None:
            return self.path_override
        from deeptutor.multi_user import paths

        return paths.SYSTEM_ROOT / self.filename

    def _open(self, path: Path, *, readonly: bool = False) -> sqlite3.Connection:
        conn = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro" if readonly else path,
            uri=readonly, timeout=30, isolation_level=None,
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            if not readonly:
                conn.execute("PRAGMA synchronous = FULL")
                conn.executescript(self.schema)
            return conn
        except BaseException:
            conn.close()
            raise

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection | None]:
        """Read-only access; yields ``None`` when the database does not exist yet."""
        path = self.path()
        if not path.exists():
            yield None
            return
        conn = self._open(path, readonly=True)
        try:
            conn.execute("BEGIN")
            yield conn
        finally:
            conn.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """Exclusive writer; creates the database and schema on first use."""
        if getattr(self._held, "writer", False):
            raise RuntimeError(f"{self.label} writers cannot be nested in one thread.")
        path = self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._write_lock:
            conn = self._open(path)
            self._held.writer = True
            try:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    yield conn
                    conn.execute("COMMIT")
                except BaseException:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    raise
            finally:
                self._held.writer = False
                conn.close()


__all__ = ["SystemDb"]
