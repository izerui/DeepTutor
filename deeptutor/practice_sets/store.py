"""System-level persistence for teacher practice sets (spec 2026-10-29 §8.1).

Lives in ``<SYSTEM_ROOT>/practice_sets.sqlite3``, outside every account:

* ``practice_sets``: the teacher's set and its editable draft;
* ``practice_set_revisions``: each published version, frozen;
* ``practice_submissions``: one row per submission with per-question
  results and the score. The answers themselves are written to the student's
  own question bank, never here (§8.4: teachers see results, not answers).

Same storage contract as the class store: DELETE journal with FULL
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

_DB_FILENAME = "practice_sets.sqlite3"

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS practice_sets (
    id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'archived')),
    current_revision INTEGER NOT NULL DEFAULT 0,
    draft_questions_json TEXT NOT NULL DEFAULT '[]',
    due_at REAL,
    reveal TEXT NOT NULL DEFAULT 'after_submit' CHECK (reveal IN ('after_submit', 'after_due')),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_practice_sets_owner ON practice_sets(owner_user_id);

CREATE TABLE IF NOT EXISTS practice_set_revisions (
    set_id TEXT NOT NULL REFERENCES practice_sets(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    questions_json TEXT NOT NULL,
    published_at REAL NOT NULL,
    PRIMARY KEY (set_id, revision)
);

CREATE TABLE IF NOT EXISTS practice_submissions (
    id TEXT PRIMARY KEY,
    set_id TEXT NOT NULL REFERENCES practice_sets(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    student_id TEXT NOT NULL,
    started_at REAL,
    submitted_at REAL NOT NULL,
    late INTEGER NOT NULL DEFAULT 0,
    score REAL NOT NULL,
    max_score REAL NOT NULL,
    -- [{question_id, result, points}]: results only, never the answer text.
    results_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_practice_submissions_set ON practice_submissions(set_id, student_id);
CREATE INDEX IF NOT EXISTS idx_practice_submissions_student ON practice_submissions(student_id);
"""

#: Tests may point this at a temporary file; ``None`` means ``<SYSTEM_ROOT>/practice_sets.sqlite3``.
DB_PATH_OVERRIDE: Path | None = None


def practice_db_path() -> Path:
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
    path = practice_db_path()
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
        raise RuntimeError("Practice set writers cannot be nested in one thread.")
    path = practice_db_path()
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


__all__ = ["DB_PATH_OVERRIDE", "practice_db_path", "read", "write"]
