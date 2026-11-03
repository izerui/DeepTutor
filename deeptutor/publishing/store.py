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
    -- ``group``: a department, a grade or both (spec 2026-11-03 batch 2 §0).
    target_type TEXT NOT NULL CHECK (target_type IN ('user', 'class', 'group')),
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


def _marker(path: Path) -> Path:
    """Left next to the database once it exists, so a lost database is noticed."""
    return path.with_name(path.name + ".created")


class ScopeStoreMissing(RuntimeError):
    """The database existed once and is gone: unreadable, never "no scopes"."""


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
            _allow_groups(conn)
        return conn
    except BaseException:
        conn.close()
        raise


def _allow_groups(conn: sqlite3.Connection) -> None:
    """Databases made before groups existed only allowed user and class targets."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'publish_targets'"
    ).fetchone()
    if row is None or "'group'" in (row[0] or ""):
        return
    conn.executescript("""
BEGIN IMMEDIATE;
ALTER TABLE publish_targets RENAME TO publish_targets_before_groups;
CREATE TABLE publish_targets (
    kind TEXT NOT NULL,
    item_id TEXT NOT NULL,
    target_type TEXT NOT NULL CHECK (target_type IN ('user', 'class', 'group')),
    target_id TEXT NOT NULL,
    PRIMARY KEY (kind, item_id, target_type, target_id),
    FOREIGN KEY (kind, item_id) REFERENCES publish_items(kind, item_id) ON DELETE CASCADE
);
INSERT INTO publish_targets SELECT * FROM publish_targets_before_groups;
DROP TABLE publish_targets_before_groups;
CREATE INDEX IF NOT EXISTS idx_publish_targets_target ON publish_targets(target_type, target_id);
COMMIT;
""")


@contextmanager
def read() -> Iterator[sqlite3.Connection | None]:
    """Read-only access; yields ``None`` when the database does not exist yet.

    A database that existed and is missing now raises: treating it as "never
    scoped" would open every scoped item to everyone (readers fail closed).
    """
    path = scopes_db_path()
    if not path.exists():
        if _marker(path).exists():
            raise ScopeStoreMissing(f"The publish scope database {path} is missing.")
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
        if not path.exists() and _marker(path).exists():
            # Recreating it empty would quietly unscope every other item.
            raise ScopeStoreMissing(
                f"The publish scope database {path} is missing. Restore it from a backup, "
                f"or delete {_marker(path).name} to start with no scopes (every public "
                "book and knowledge base then reaches everyone)."
            )
        conn = _open(path)
        _marker(path).touch(exist_ok=True)
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


__all__ = ["DB_PATH_OVERRIDE", "ScopeStoreMissing", "read", "scopes_db_path", "write"]
