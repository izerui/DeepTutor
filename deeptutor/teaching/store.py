"""System-level persistence for teacher mastery templates.

Lives in ``<SYSTEM_ROOT>/teaching.sqlite3``, outside every account workspace:
templates, their immutable published revisions, and who they are assigned to.
A student's learning state never comes here — it stays in their own
``mastery.sqlite3``.

Journal mode: DELETE with FULL synchronous, matching the existing KB and book
registries. The illustrative brief set WAL, but the accepted D2 shared
transitions require on-disk DELETE journals for multi-database atomicity.
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
    MasteryAssignment,
    MasteryTemplate,
    TemplateOutline,
    TemplateRevision,
    TemplateSources,
)

_schema_lock = threading.Lock()
_DB_FILENAME = "teaching.sqlite3"

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


class TeachingStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else teaching_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with _schema_lock, self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

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
        return TemplateRevision(
            template_id=row["template_id"],
            revision=int(row["revision"]),
            outline=TemplateOutline.model_validate_json(row["outline_json"]),
            sources=TemplateSources.model_validate_json(row["sources_json"]),
            change_log=json.loads(row["change_log_json"] or "{}"),
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
        with self._write() as conn:
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
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mastery_templates WHERE id = ?", (template_id,)
            ).fetchone()
        return self._template(row) if row is not None else None

    def find_by_draft(
        self, owner_user_id: str, draft_path_id: str, draft_workspace_id: str
    ) -> MasteryTemplate | None:
        with self._connect() as conn:
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
        with self._connect() as conn:
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
        assignments: list[str] = []
        params: list[object] = []
        for column, value in (("title", title), ("description", description), ("status", status)):
            if value is not None:
                assignments.append(f"{column} = ?")
                params.append(value)
        assignments.append("updated_at = ?")
        params.append(time.time())
        with self._write() as conn:
            conn.execute(
                f"UPDATE mastery_templates SET {', '.join(assignments)} WHERE id = ?",  # nosec B608 - fixed column names
                (*params, template_id),
            )
        updated = self.get_template(template_id)
        if updated is None:
            raise KeyError(template_id)
        return updated

    def delete_template(self, template_id: str) -> None:
        with self._write() as conn:
            conn.execute("DELETE FROM mastery_templates WHERE id = ?", (template_id,))

    def transfer_owner(self, from_user_id: str, to_user_id: str) -> int:
        """Hand every template of a deleted account to its successor.

        The draft lived in the deleted account's workspace, so it is detached:
        the successor can keep assigning, revoking and archiving, but a new
        revision needs a new draft.
        """
        with self._write() as conn:
            cursor = conn.execute(
                """
                UPDATE mastery_templates
                SET owner_user_id = ?, draft_path_id = '', draft_workspace_id = '',
                    updated_at = ?
                WHERE owner_user_id = ?
                """,
                (to_user_id, time.time(), from_user_id),
            )
        return int(cursor.rowcount)

    # ---- revisions --------------------------------------------------------

    def add_revision(
        self, revision: TemplateRevision, *, aliases: dict[str, str], title: str
    ) -> MasteryTemplate:
        """Append the next immutable revision and make it current, atomically."""
        with self._write() as conn:
            row = conn.execute(
                "SELECT current_revision FROM mastery_templates WHERE id = ?",
                (revision.template_id,),
            ).fetchone()
            if row is None:
                raise KeyError(revision.template_id)
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
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mastery_template_revisions WHERE template_id = ? AND revision = ?",
                (template_id, revision),
            ).fetchone()
        return self._revision(row) if row is not None else None

    def revisions_between(
        self, template_id: str, *, after: int, upto: int
    ) -> list[TemplateRevision]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM mastery_template_revisions
                WHERE template_id = ? AND revision > ? AND revision <= ?
                ORDER BY revision ASC
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
        with self._write() as conn:
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
        with self._write() as conn:
            conn.execute(
                """
                UPDATE mastery_assignments SET status = 'revoked', revoked_at = ?
                WHERE template_id = ? AND student_user_id = ? AND status = 'active'
                """,
                (time.time(), template_id, student_id),
            )
        return self.get_assignment(template_id, student_id)

    def get_assignment(self, template_id: str, student_id: str) -> MasteryAssignment | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mastery_assignments WHERE template_id = ? AND student_user_id = ?",
                (template_id, student_id),
            ).fetchone()
        return self._assignment(row) if row is not None else None

    def list_assignments(self, template_id: str) -> list[MasteryAssignment]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM mastery_assignments WHERE template_id = ? ORDER BY assigned_at",
                (template_id,),
            ).fetchall()
        return [self._assignment(row) for row in rows]

    def assignments_for_student(self, student_id: str) -> list[MasteryAssignment]:
        with self._connect() as conn:
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
        with self._write() as conn:
            conn.execute(
                """
                UPDATE mastery_assignments
                SET instance_path_id = ?, applied_revision = ?
                WHERE template_id = ? AND student_user_id = ?
                """,
                (instance_path_id, applied_revision, template_id, student_id),
            )

    def has_assignments(self, template_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM mastery_assignments WHERE template_id = ? LIMIT 1",
                (template_id,),
            ).fetchone()
        return row is not None

    def count_active(self, template_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM mastery_assignments "
                "WHERE template_id = ? AND status = 'active'",
                (template_id,),
            ).fetchone()
        return int(row["n"])

    def revoke_all_for_student(self, student_id: str) -> int:
        with self._write() as conn:
            cursor = conn.execute(
                """
                UPDATE mastery_assignments SET status = 'revoked', revoked_at = ?
                WHERE student_user_id = ? AND status = 'active'
                """,
                (time.time(), student_id),
            )
        return int(cursor.rowcount)
