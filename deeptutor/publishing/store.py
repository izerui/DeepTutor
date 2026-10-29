"""System-level persistence for publish scopes (spec 2026-10-29 §7).

Lives in ``<SYSTEM_ROOT>/publish_scopes.sqlite3``. An item appears in
``publish_items`` once its publisher sets a scope; until then the content
type's old rule applies (a public book or knowledge base reaches everyone, a
Mastery Path reaches the students it was assigned to). Same storage contract
as the class store: DELETE journal with FULL synchronous, ``BEGIN IMMEDIATE``
writers that never nest, read-only connections for reads, and no file
created by a read.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import threading

_write_lock = threading.Lock()
_held = threading.local()

_DB_FILENAME = "publish_scopes.sqlite3"

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS publish_items (
    kind TEXT NOT NULL,
    item_id TEXT NOT NULL,
    everyone INTEGER NOT NULL DEFAULT 0,
    updated_by TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (kind, item_id)
);

CREATE TABLE IF NOT EXISTS publish_targets (
    kind TEXT NOT NULL,
    item_id TEXT NOT NULL,
    target_type TEXT NOT NULL CHECK (target_type IN ('user', 'class')),
    target_id TEXT NOT NULL,
    PRIMARY KEY (kind, item_id, target_type, target_id),
    FOREIGN KEY (kind, item_id) REFERENCES publish_items(kind, item_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_publish_targets_target ON publish_targets(target_type, target_id);
"""

#: Tests may point this at a temporary file; ``None`` means ``<SYSTEM_ROOT>/publish_scopes.sqlite3``.
DB_PATH_OVERRIDE: Path | None = None


def scopes_db_path() -> Path:
    if DB_PATH_OVERRIDE is not None:
        return DB_PATH_OVERRIDE
    from deeptutor.multi_user import paths

    return paths.SYSTEM_ROOT / _DB_FILENAME


def _open(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro" if readonly else path,
        uri=readonly, timeout=30, isolation_level=None,
    )
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if not readonly:
            conn.execute("PRAGMA synchronous = FULL")
            conn.executescript(_SCHEMA)
        return conn
    except BaseException:
        conn.close()
        raise


@contextmanager
def read() -> Iterator[sqlite3.Connection | None]:
    """Read-only access; yields ``None`` when the database does not exist yet."""
    path = scopes_db_path()
    if not path.exists():
        yield None
        return
    conn = _open(path, readonly=True)
    try:
        conn.execute("BEGIN")
        yield conn
    finally:
        conn.close()


@contextmanager
def write() -> Iterator[sqlite3.Connection]:
    """Exclusive writer; creates the database and schema on first use."""
    if getattr(_held, "writer", False):
        raise RuntimeError("Publish scope writers cannot be nested in one thread.")
    path = scopes_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock:
        conn = _open(path)
        _held.writer = True
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
            _held.writer = False
            conn.close()


__all__ = ["DB_PATH_OVERRIDE", "read", "scopes_db_path", "write"]
