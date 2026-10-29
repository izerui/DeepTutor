"""System-level persistence for classes.

Lives in ``<SYSTEM_ROOT>/classes.sqlite3``, outside every account: classes,
their teachers and students, the class invite link and teacher invitations.
Same storage contract as the teaching store: DELETE journal with FULL
synchronous, ``BEGIN IMMEDIATE`` writers that never nest, read-only
connections for reads, and no file created by a read.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import threading

_write_lock = threading.Lock()
_held = threading.local()

_DB_FILENAME = "classes.sqlite3"

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS classes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_by TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS class_teachers (
    class_id TEXT NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    added_by TEXT NOT NULL,
    added_at REAL NOT NULL,
    PRIMARY KEY (class_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_class_teachers_user ON class_teachers(user_id);

CREATE TABLE IF NOT EXISTS class_members (
    class_id TEXT NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    student_id TEXT NOT NULL,
    joined_via TEXT NOT NULL,
    added_by TEXT NOT NULL,
    joined_at REAL NOT NULL,
    PRIMARY KEY (class_id, student_id)
);
CREATE INDEX IF NOT EXISTS idx_class_members_student ON class_members(student_id);

CREATE TABLE IF NOT EXISTS class_invite_links (
    class_id TEXT PRIMARY KEY REFERENCES classes(id) ON DELETE CASCADE,
    token TEXT NOT NULL UNIQUE,
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL
);

CREATE TABLE IF NOT EXISTS class_invitations (
    id TEXT PRIMARY KEY,
    class_id TEXT NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    student_id TEXT NOT NULL,
    invited_by TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    responded_at REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_class_invitations_pending
    ON class_invitations(class_id, student_id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_class_invitations_student
    ON class_invitations(student_id, status);
"""

#: Tests may point this at a temporary file; ``None`` means ``<SYSTEM_ROOT>/classes.sqlite3``.
DB_PATH_OVERRIDE: Path | None = None


def classes_db_path() -> Path:
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
    path = classes_db_path()
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
        raise RuntimeError("Class store writers cannot be nested in one thread.")
    path = classes_db_path()
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


__all__ = ["DB_PATH_OVERRIDE", "classes_db_path", "read", "write"]
