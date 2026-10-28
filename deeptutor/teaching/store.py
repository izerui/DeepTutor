"""System-level persistence for teacher mastery templates.

Lives in ``<SYSTEM_ROOT>/teaching.sqlite3``, outside every account workspace:
templates, their immutable published revisions, and who they are assigned to.
A student's learning state never comes here — it stays in their own
``mastery.sqlite3``.

Journal mode: DELETE with FULL synchronous, matching the existing KB and book
registries. The illustrative brief set WAL, but the accepted D2 shared
transitions require on-disk DELETE journals for multi-database atomicity.

Read path: a read on a missing DB returns empty/None without creating the
file. Existing-DB reads use a read-only connection (``mode=ro``). Schema
creation is deferred to the first write, mirroring the book and KB registries.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from deeptutor.teaching.models import (
    ChangeEntry,
    MasteryAssignment,
    MasteryTemplate,
    TemplateOutline,
    TemplateRevision,
    TemplateSources,
)

#: A plain Lock on purpose: an RLock would let a nested writer in, only for it
#: to wait 30 s on SQLite's own RESERVED lock. Nesting is refused instead.
_write_lock = threading.Lock()
#: ``material_lock`` is set while this thread holds
#: :meth:`TeachingStore.material_lock`; ``writer`` while it holds any teaching
#: writer (:func:`_write` or :func:`_joint_write`).
_held = threading.local()


def _refuse_nested_writer() -> None:
    """Fail fast where a nested teaching writer would wait on itself forever."""
    if getattr(_held, "material_lock", False):
        # The lock is this very writer; a nested write would wait on itself.
        raise RuntimeError("Teaching store writes are not allowed inside material_lock().")
    if getattr(_held, "writer", False):
        raise RuntimeError("Teaching store writers cannot be nested in one thread.")


_DB_FILENAME = "teaching.sqlite3"
_JOINT_SCHEMAS = ("main", "kb", "books")

# No journal_mode PRAGMA here: SQLite defaults to DELETE, matching the
# existing registry-compatible contract (see deeptutor/knowledge/registry.py
# and deeptutor/book/registry.py).  The brief's illustrative WAL was
# overridden by the root storage-coordination note.
_SCHEMA = """\
CREATE TABLE IF NOT EXISTS mastery_templates (
    id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    current_revision INTEGER NOT NULL DEFAULT 0,
    draft_path_id TEXT NOT NULL DEFAULT '',
    draft_workspace_id TEXT NOT NULL DEFAULT '',
    kp_aliases_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mastery_templates_owner
    ON mastery_templates(owner_user_id, updated_at DESC);
-- One live template per draft path; an archived one frees the draft.
CREATE UNIQUE INDEX IF NOT EXISTS uq_mastery_templates_live_draft
    ON mastery_templates(owner_user_id, draft_workspace_id, draft_path_id)
    WHERE draft_path_id != '' AND status != 'archived';

CREATE TABLE IF NOT EXISTS mastery_template_revisions (
    template_id TEXT NOT NULL REFERENCES mastery_templates(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    outline_json TEXT NOT NULL,
    sources_json TEXT NOT NULL,
    change_log_json TEXT NOT NULL DEFAULT '{}',
    published_by TEXT NOT NULL,
    published_at REAL NOT NULL,
    PRIMARY KEY (template_id, revision)
);

CREATE TABLE IF NOT EXISTS mastery_assignments (
    template_id TEXT NOT NULL REFERENCES mastery_templates(id) ON DELETE CASCADE,
    student_user_id TEXT NOT NULL,
    assigned_by TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    instance_path_id TEXT NOT NULL DEFAULT '',
    applied_revision INTEGER NOT NULL DEFAULT 0,
    assigned_at REAL NOT NULL,
    revoked_at REAL,
    PRIMARY KEY (template_id, student_user_id)
);
CREATE INDEX IF NOT EXISTS idx_mastery_assignments_student
    ON mastery_assignments(student_user_id, status);
"""


#: Tests point this at a temporary file (``tests/conftest.py``); ``None`` means
#: ``<SYSTEM_ROOT>/teaching.sqlite3``.
DB_PATH_OVERRIDE: Path | None = None


def teaching_db_path() -> Path:
    """Read ``SYSTEM_ROOT`` per call so a redirected root (tests) is honoured."""
    if DB_PATH_OVERRIDE is not None:
        return DB_PATH_OVERRIDE
    from deeptutor.multi_user import paths

    return paths.SYSTEM_ROOT / _DB_FILENAME


def _open(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    """Open a connection; run schema creation only on writable opens."""
    conn = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro" if readonly else path,
        uri=readonly, timeout=30, isolation_level=None,
    )
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if not readonly:
            conn.executescript(_SCHEMA)
        return conn
    except BaseException:
        conn.close()
        raise


@contextmanager
def _read(path: Path) -> Iterator[sqlite3.Connection | None]:
    """Read-only access; yields ``None`` when the DB file does not exist."""
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
def _write(path: Path) -> Iterator[sqlite3.Connection]:
    """Exclusive writer: creates the DB and runs schema on first use."""
    _refuse_nested_writer()
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


@contextmanager
def _joint_write(path: Path) -> Iterator[sqlite3.Connection]:
    """One crash-atomic writer over the teaching, KB and book databases.

    The teaching DB is ``main``; the KB registry is attached as ``kb`` and the
    book registry as ``books``. Locks are taken in the established order
    teaching -> KB -> Book (the same order as ``material_lock`` followed by
    plan D's shared writer), both the in-process ones and SQLite's own:
    ``BEGIN IMMEDIATE`` reserves ``main`` first, then the attached files in
    attach order. Multi-file atomicity needs on-disk rollback journals, so a
    database that is not in DELETE mode is refused after locking, before any
    write. Nothing here awaits; callers keep the block short.

    Same shape as ``deeptutor.book.sharing.shared_write`` (attach, lock order,
    DELETE-journal check, writer flag): a change to one must be checked
    against the other. They stay separate on purpose: main database, lock
    set, error types and recovery hooks all differ.
    """
    from deeptutor.book import registry as books
    from deeptutor.knowledge import registry as kbs

    _refuse_nested_writer()
    if getattr(books._writer_state, "active", False):
        raise books.BookRegistryError("Cannot enter the teaching writer from an active book writer.")
    teaching_path = path.resolve()
    kb_path, book_path = kbs._db_path().resolve(), books._db_path().resolve()
    if len({teaching_path, kb_path, book_path}) != 3:
        raise RuntimeError("Teaching, KB and book databases must be distinct files.")
    with _write_lock, kbs._write_lock, books._write_lock:
        # Create schemas one at a time, before any database has a writer.
        for target in (teaching_path, kb_path, book_path):
            target.parent.mkdir(parents=True, exist_ok=True)
        _open(teaching_path).close()
        kbs._open(kb_path).close()
        books._open(book_path).close()
        conn = sqlite3.connect(teaching_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        books._writer_state.active = True
        _held.writer = True
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("ATTACH DATABASE ? AS kb", (str(kb_path),))
            conn.execute("ATTACH DATABASE ? AS books", (str(book_path),))
            for schema in _JOINT_SCHEMAS:
                conn.execute(f"PRAGMA {schema}.synchronous = FULL")
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Checked under the lock: nobody can switch modes meanwhile.
                for schema in _JOINT_SCHEMAS:
                    mode = conn.execute(f"PRAGMA {schema}.journal_mode").fetchone()[0]
                    if str(mode).lower() != "delete":
                        raise RuntimeError(
                            f"Joint teaching transfers require DELETE journals ({schema}: {mode})."
                        )
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        finally:
            _held.writer = False
            books._writer_state.active = False
            conn.close()


class TeachingStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else teaching_db_path()

    # ---- rows -------------------------------------------------------------

    @staticmethod
    def _template(row: sqlite3.Row) -> MasteryTemplate:
        return MasteryTemplate(
            id=row["id"],
            owner_user_id=row["owner_user_id"],
            title=row["title"],
            description=row["description"],
            status=row["status"],
            current_revision=int(row["current_revision"]),
            draft_path_id=row["draft_path_id"],
            draft_workspace_id=row["draft_workspace_id"],
            kp_aliases=json.loads(row["kp_aliases_json"] or "{}"),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    @staticmethod
    def _assignment(row: sqlite3.Row) -> MasteryAssignment:
        return MasteryAssignment(
            template_id=row["template_id"],
            student_user_id=row["student_user_id"],
            assigned_by=row["assigned_by"],
            status=row["status"],
            instance_path_id=row["instance_path_id"],
            applied_revision=int(row["applied_revision"]),
            assigned_at=float(row["assigned_at"]),
            revoked_at=float(row["revoked_at"]) if row["revoked_at"] is not None else None,
        )

    @staticmethod
    def _revision(row: sqlite3.Row) -> TemplateRevision:
        raw_log = json.loads(row["change_log_json"] or "{}")
        change_log = {
            kp_id: ChangeEntry.model_validate(entry) for kp_id, entry in raw_log.items()
        }
        return TemplateRevision(
            template_id=row["template_id"],
            revision=int(row["revision"]),
            outline=TemplateOutline.model_validate_json(row["outline_json"]),
            sources=TemplateSources.model_validate_json(row["sources_json"]),
            change_log=change_log,
            published_by=row["published_by"],
            published_at=float(row["published_at"]),
        )

    # ---- templates --------------------------------------------------------

    def create_template(
        self,
        *,
        owner_user_id: str,
        title: str,
        description: str,
        draft_path_id: str,
        draft_workspace_id: str,
    ) -> MasteryTemplate:
        template_id = f"tpl_{uuid.uuid4().hex[:16]}"
        now = time.time()
        with _write(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO mastery_templates (
                    id, owner_user_id, title, description, status, current_revision,
                    draft_path_id, draft_workspace_id, kp_aliases_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'draft', 0, ?, ?, '{}', ?, ?)
                """,
                (
                    template_id,
                    owner_user_id,
                    title[:200],
                    description[:500],
                    draft_path_id,
                    draft_workspace_id,
                    now,
                    now,
                ),
            )
        created = self.get_template(template_id)
        assert created is not None
        return created

    def get_template(self, template_id: str) -> MasteryTemplate | None:
        with _read(self.db_path) as conn:
            if conn is None:
                return None
            row = conn.execute(
                "SELECT * FROM mastery_templates WHERE id = ?", (template_id,)
            ).fetchone()
        return self._template(row) if row is not None else None

    def find_by_draft(
        self, owner_user_id: str, draft_path_id: str, draft_workspace_id: str
    ) -> MasteryTemplate | None:
        with _read(self.db_path) as conn:
            if conn is None:
                return None
            row = conn.execute(
                """
                SELECT * FROM mastery_templates
                WHERE owner_user_id = ? AND draft_path_id = ? AND draft_workspace_id = ?
                AND status != 'archived'
                """,
                (owner_user_id, draft_path_id, draft_workspace_id),
            ).fetchone()
        return self._template(row) if row is not None else None

    def list_templates(self, *, owner_user_id: str | None = None) -> list[MasteryTemplate]:
        with _read(self.db_path) as conn:
            if conn is None:
                return []
            if owner_user_id is None:
                rows = conn.execute(
                    "SELECT * FROM mastery_templates ORDER BY updated_at DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM mastery_templates WHERE owner_user_id = ? "
                    "ORDER BY updated_at DESC",
                    (owner_user_id,),
                ).fetchall()
        return [self._template(row) for row in rows]

    def update_template(
        self,
        template_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> MasteryTemplate:
        set_clauses: list[str] = []
        params: list[object] = []
        for column, value in (("title", title), ("description", description), ("status", status)):
            if value is not None:
                set_clauses.append(f"{column} = ?")
                params.append(value)
        set_clauses.append("updated_at = ?")
        params.append(time.time())
        with _write(self.db_path) as conn:
            conn.execute(
                f"UPDATE mastery_templates SET {', '.join(set_clauses)} WHERE id = ?",  # nosec B608 - fixed column names
                (*params, template_id),
            )
        updated = self.get_template(template_id)
        if updated is None:
            raise KeyError(template_id)
        return updated

    def delete_template(self, template_id: str) -> None:
        with _write(self.db_path) as conn:
            conn.execute("DELETE FROM mastery_templates WHERE id = ?", (template_id,))

    def transfer_owner(self, from_user_id: str, to_user_id: str) -> int:
        """Hand every template of a deleted account to its successor.

        The draft lived in the deleted account's workspace, so it is detached:
        the successor can keep assigning, revoking and archiving, but a new
        revision needs a new draft.

        The templates' active KB and book references (including those kept
        for older applied revisions) change owner in the same transaction, so
        they can never disagree with the template's owner. Targets, document
        scope and revoked rows are left exactly as they are: revoked access is
        history, not something a hand-over renews. References of any other
        referrer are not touched, whoever created them.
        """
        from deeptutor.teaching import books as book_access
        from deeptutor.teaching import kb as kb_access

        if from_user_id == to_user_id:
            return 0
        # Most deleted accounts own no template: skip locking all three
        # databases. A deleted account creates no more templates, so nothing
        # can appear between this read and the write.
        with _read(self.db_path) as conn:
            if conn is None or conn.execute(
                "SELECT 1 FROM mastery_templates WHERE owner_user_id = ? LIMIT 1",
                (from_user_id,),
            ).fetchone() is None:
                return 0
        with _joint_write(self.db_path) as conn:
            template_ids = [
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM main.mastery_templates WHERE owner_user_id = ?",
                    (from_user_id,),
                )
            ]
            conn.execute(
                """
                UPDATE main.mastery_templates
                SET owner_user_id = ?, draft_path_id = '', draft_workspace_id = '',
                    updated_at = ?
                WHERE owner_user_id = ?
                """,
                (to_user_id, time.time(), from_user_id),
            )
            for table, kind in (
                ("kb.kb_references", kb_access.REFERRER_KIND),
                ("books.book_references", book_access.REFERRER_KIND),
            ):
                conn.executemany(
                    f"UPDATE {table} SET created_by = ? "  # nosec B608 - fixed tables
                    "WHERE referrer_kind = ? AND referrer_id = ?",
                    [(to_user_id, kind, template_id) for template_id in template_ids],
                )
        return len(template_ids)

    @contextmanager
    def material_lock(self) -> Iterator[None]:
        """Serialize "read template state, then rewrite its material references".

        Holds this store's ``BEGIN IMMEDIATE`` writer, so it excludes every
        other teaching write (``add_revision``, ``update_template``,
        ``record_instance``, ``revoke``...) in every worker process until the
        references are written. Reads stay possible; writing to this store
        inside the block raises instead of deadlocking.
        """
        with _write(self.db_path):
            _held.material_lock = True
            try:
                yield
            finally:
                _held.material_lock = False

    # ---- revisions --------------------------------------------------------

    def add_revision(
        self, revision: TemplateRevision, *, aliases: dict[str, str], title: str
    ) -> MasteryTemplate:
        """Append the next immutable revision and make it current, atomically."""
        with _write(self.db_path) as conn:
            row = conn.execute(
                "SELECT current_revision, status FROM mastery_templates WHERE id = ?",
                (revision.template_id,),
            ).fetchone()
            if row is None:
                raise KeyError(revision.template_id)
            if row["status"] == "archived":
                # An archive that won the race stays archived (spec 5.3).
                raise ValueError(f"{revision.template_id} is archived")
            if revision.revision != int(row["current_revision"]) + 1:
                raise ValueError(
                    f"Revision {revision.revision} does not follow "
                    f"{int(row['current_revision'])} for {revision.template_id}"
                )
            conn.execute(
                """
                INSERT INTO mastery_template_revisions (
                    template_id, revision, outline_json, sources_json,
                    change_log_json, published_by, published_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision.template_id,
                    revision.revision,
                    revision.outline.model_dump_json(),
                    revision.sources.model_dump_json(),
                    json.dumps(
                        {key: entry.model_dump() for key, entry in revision.change_log.items()},
                        ensure_ascii=False,
                    ),
                    revision.published_by,
                    revision.published_at,
                ),
            )
            conn.execute(
                """
                UPDATE mastery_templates
                SET current_revision = ?, status = 'published', kp_aliases_json = ?,
                    title = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    revision.revision,
                    json.dumps(aliases, ensure_ascii=False),
                    title[:200],
                    time.time(),
                    revision.template_id,
                ),
            )
        updated = self.get_template(revision.template_id)
        assert updated is not None
        return updated

    def get_revision(self, template_id: str, revision: int) -> TemplateRevision | None:
        with _read(self.db_path) as conn:
            if conn is None:
                return None
            row = conn.execute(
                "SELECT * FROM mastery_template_revisions WHERE template_id = ? AND revision = ?",
                (template_id, revision),
            ).fetchone()
        return self._revision(row) if row is not None else None

    def revisions_between(
        self, template_id: str, *, after: int, upto: int
    ) -> list[TemplateRevision]:
        with _read(self.db_path) as conn:
            if conn is None:
                return []
            rows = conn.execute(
                """
                SELECT * FROM mastery_template_revisions
                WHERE template_id = ? AND revision > ? AND revision <= ?
                ORDER BY revision
                """,
                (template_id, after, upto),
            ).fetchall()
        return [self._revision(row) for row in rows]

    # ---- assignments ------------------------------------------------------

    def assign(
        self, template_id: str, student_ids: list[str], *, assigned_by: str
    ) -> list[MasteryAssignment]:
        """Activate each student; a revoked row comes back with its instance."""
        now = time.time()
        with _write(self.db_path) as conn:
            for student_id in dict.fromkeys(student_ids):
                conn.execute(
                    """
                    INSERT INTO mastery_assignments (
                        template_id, student_user_id, assigned_by, status, assigned_at
                    ) VALUES (?, ?, ?, 'active', ?)
                    ON CONFLICT(template_id, student_user_id) DO UPDATE SET
                        status = 'active',
                        assigned_by = excluded.assigned_by,
                        revoked_at = NULL
                    """,
                    (template_id, student_id, assigned_by, now),
                )
        return self.list_assignments(template_id)

    def revoke(self, template_id: str, student_id: str) -> MasteryAssignment | None:
        with _write(self.db_path) as conn:
            conn.execute(
                """
                UPDATE mastery_assignments SET status = 'revoked', revoked_at = ?
                WHERE template_id = ? AND student_user_id = ? AND status = 'active'
                """,
                (time.time(), template_id, student_id),
            )
        return self.get_assignment(template_id, student_id)

    def get_assignment(self, template_id: str, student_id: str) -> MasteryAssignment | None:
        with _read(self.db_path) as conn:
            if conn is None:
                return None
            row = conn.execute(
                "SELECT * FROM mastery_assignments WHERE template_id = ? AND student_user_id = ?",
                (template_id, student_id),
            ).fetchone()
        return self._assignment(row) if row is not None else None

    def list_assignments(self, template_id: str) -> list[MasteryAssignment]:
        with _read(self.db_path) as conn:
            if conn is None:
                return []
            rows = conn.execute(
                "SELECT * FROM mastery_assignments WHERE template_id = ? ORDER BY assigned_at",
                (template_id,),
            ).fetchall()
        return [self._assignment(row) for row in rows]

    def assignments_for_student(self, student_id: str) -> list[MasteryAssignment]:
        with _read(self.db_path) as conn:
            if conn is None:
                return []
            rows = conn.execute(
                "SELECT * FROM mastery_assignments WHERE student_user_id = ? ORDER BY assigned_at",
                (student_id,),
            ).fetchall()
        return [self._assignment(row) for row in rows]

    def record_instance(
        self,
        template_id: str,
        student_id: str,
        *,
        instance_path_id: str,
        applied_revision: int,
    ) -> None:
        with _write(self.db_path) as conn:
            conn.execute(
                """
                UPDATE mastery_assignments
                SET instance_path_id = ?, applied_revision = ?
                WHERE template_id = ? AND student_user_id = ?
                  AND applied_revision < ?
                """,
                (instance_path_id, applied_revision, template_id, student_id, applied_revision),
            )

    def has_assignments(self, template_id: str) -> bool:
        with _read(self.db_path) as conn:
            if conn is None:
                return False
            row = conn.execute(
                "SELECT 1 FROM mastery_assignments WHERE template_id = ? LIMIT 1",
                (template_id,),
            ).fetchone()
        return row is not None

    def count_active(self, template_id: str) -> int:
        with _read(self.db_path) as conn:
            if conn is None:
                return 0
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM mastery_assignments "
                "WHERE template_id = ? AND status = 'active'",
                (template_id,),
            ).fetchone()
        return int(row["n"])

    def revoke_all_for_student(self, student_id: str) -> int:
        with _write(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE mastery_assignments SET status = 'revoked', revoked_at = ?
                WHERE student_user_id = ? AND status = 'active'
                """,
                (time.time(), student_id),
            )
        return int(cursor.rowcount)
