"""Tasks (spec 2026-11-03 batch 1 §3).

A teacher (or administrator) asks students to do one thing: study a Mastery
Path, answer a practice set, read chapters of a book, or a plain
instruction. The task reaches students through a publish scope (kind
``task``) and only ever names content its author published.

On the student side a task is *open*, *overdue*, *completed* or *closed*.
Completion is detected from what the student actually did and then kept
(R24). *Closed* is never stored: it
is whether the task, its content and the student's place in its scope still
hold right now, so coming back into scope restores it.

Practice sets and Mastery Paths a student received and no task names are
shown as tasks too (``practice_set:<id>`` / ``mastery_path:<template id>``),
so nothing assigned is listed twice (R23).
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import time
from typing import Any
import uuid

from deeptutor.multi_user.context import get_current_user
from deeptutor.multi_user.models import CurrentUser
from deeptutor.tasks.store import db, files_root

logger = logging.getLogger(__name__)

KINDS = ("mastery_path", "practice_set", "book", "note")
TITLE_MAX = 200
TEXT_MAX = 20_000
DUE_SOON_SECONDS = 24 * 3600


class TaskError(Exception):
    status = 400


class TaskNotFound(TaskError):
    status = 404


class TaskForbidden(TaskError):
    status = 403


class TaskConflict(TaskError):
    status = 409


def _now() -> float:
    return time.time()


def _open_at(row) -> float | None:
    """When a task opens; rows read before the column existed have none."""
    return row["open_at"] if "open_at" in row.keys() else None


def _role(user: CurrentUser) -> str:
    from deeptutor.multi_user.accounts import live_role

    return live_role(user)


def _accounts() -> dict[str, dict[str, Any]]:
    from deeptutor.multi_user.accounts import accounts

    return accounts()


# ── targets ────────────────────────────────────────────────


def _hand_tasks_to(conn, admin_id: str, teacher_id: str, task_ids: list[str]) -> int:
    """Inside a write: the administrator's tasks among *task_ids* go to an active teacher."""
    account = _accounts().get(teacher_id) or {}
    if account.get("role") != "teacher" or account.get("disabled"):
        raise TaskError("Choose an active teacher.")
    if not task_ids:
        return 0
    marks = ",".join("?" for _ in task_ids)
    return int(conn.execute(
        f"UPDATE tasks SET owner_user_id = ?, updated_at = ? WHERE owner_user_id = ? AND id IN ({marks})",  # nosec B608
        (teacher_id, _now(), admin_id, *task_ids),
    ).rowcount)


def _course_of(task_id: str) -> dict[str, str]:
    with db.read() as conn:
        if conn is None:
            return {"course_id": "", "course_title": ""}
        try:
            row = conn.execute(
                "SELECT c.id, c.title FROM course_items i JOIN courses c ON c.id = i.course_id "
                "WHERE i.task_id = ? ORDER BY c.created_at LIMIT 1",
                (task_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            row = None  # a task store from before courses
    return {"course_id": row["id"], "course_title": row["title"]} if row else {"course_id": "", "course_title": ""}


def _everyone_tasks() -> list[str]:
    """Tasks from before the market that still reach everyone (legacy data)."""
    from deeptutor.publishing import store as scope_store

    with scope_store.read() as conn:
        if conn is None:
            return []
        return [r[0] for r in conn.execute("SELECT item_id FROM publish_items WHERE kind = 'task' AND everyone = 1")]


def _in_a_course(task_id: str) -> bool:
    with db.read() as conn:
        if conn is None:
            return False
        try:
            return conn.execute("SELECT 1 FROM course_items WHERE task_id = ?", (task_id,)).fetchone() is not None
        except sqlite3.OperationalError:
            return False  # a task store from before courses


def _target_audience(kind: str, target_id: str) -> set[str] | None:
    """Students the referenced content reaches; ``None`` means everyone."""
    from deeptutor.publishing import scope as scopes

    if kind == "practice_set":
        scope = scopes.get_scope(scopes.PRACTICE_SET, target_id)
        return scopes.audience(scope, students_only=True) if scope else set()
    if kind == "mastery_path":
        scope = scopes.get_scope(scopes.MASTERY_PATH, target_id)
        if scope is not None:
            return scopes.audience(scope, students_only=True)
        from deeptutor.teaching.store import TeachingStore

        return {
            a.student_user_id for a in TeachingStore().list_assignments(target_id) if a.status == "active"
        }
    if kind == "book":
        from deeptutor.book import registry as book_registry

        record = book_registry.get_book(target_id)
        if record is None or record.visibility != "public":
            return set()
        scope = scopes.get_scope(scopes.BOOK, target_id)
        return None if scope is None else scopes.audience(scope, students_only=True)
    return None


def _target_title(kind: str, target_id: str) -> str:
    if kind == "practice_set":
        from deeptutor.practice_sets import store as practice_store

        with practice_store.read() as conn:
            row = conn.execute("SELECT title FROM practice_sets WHERE id = ?", (target_id,)).fetchone() if conn else None
        return row["title"] if row else ""
    if kind == "mastery_path":
        from deeptutor.teaching.store import TeachingStore

        template = TeachingStore().get_template(target_id)
        return template.title if template else ""
    if kind == "book":
        from deeptutor.book import registry as book_registry

        record = book_registry.get_book(target_id)
        return record.title if record else ""
    return ""


def _target_available_to(kind: str, target_id: str, student: CurrentUser) -> bool:
    """Whether the student can still open the referenced content."""
    if kind == "note":
        return True
    if kind == "practice_set":
        from deeptutor.practice_sets import store as practice_store
        from deeptutor.publishing import scope as scopes

        with practice_store.read() as conn:
            row = conn.execute("SELECT status FROM practice_sets WHERE id = ?", (target_id,)).fetchone() if conn else None
        scope = scopes.get_scope(scopes.PRACTICE_SET, target_id)
        return bool(row and row["status"] == "published" and scope and scopes._reaches(scope, student.id))
    if kind == "mastery_path":
        from deeptutor.teaching.store import TeachingStore

        teaching = TeachingStore()
        template = teaching.get_template(target_id)
        assignment = teaching.get_assignment(target_id, student.id)
        return bool(
            template and template.status == "published" and assignment and assignment.status == "active"
        )
    if kind == "book":
        from deeptutor.book import registry as book_registry

        record = book_registry.get_book(target_id)
        return bool(record and book_registry.can_read_book(record, student))
    return False


def _first_submission(set_id: str, student_id: str) -> float | None:
    """When the student first submitted a practice set: the moment it was done, and whether late."""
    from deeptutor.practice_sets import store as practice_store

    with practice_store.read() as conn:
        row = conn.execute(
            "SELECT MIN(submitted_at) FROM practice_submissions WHERE set_id = ? AND student_id = ?",
            (set_id, student_id),
        ).fetchone() if conn else None
    return float(row[0]) if row and row[0] else None


def _completed_now(kind: str, target_id: str, settings: dict[str, Any], student: CurrentUser) -> bool:
    """Whether what the student did meets the task (notes are stored, not detected)."""
    try:
        if kind == "practice_set":
            from deeptutor.practice_sets import store as practice_store

            with practice_store.read() as conn:
                return bool(conn and conn.execute(
                    "SELECT 1 FROM practice_submissions WHERE set_id = ? AND student_id = ? LIMIT 1",
                    (target_id, student.id),
                ).fetchone())
        if kind == "mastery_path":
            from deeptutor.learning import policy
            from deeptutor.learning.storage import LearningStore
            from deeptutor.teaching.instances import instance_path_id
            from deeptutor.teaching.store import TeachingStore

            assignment = TeachingStore().get_assignment(target_id, student.id)
            path_id = (assignment.instance_path_id if assignment else "") or instance_path_id(target_id)
            progress = LearningStore().load(path_id)
            if progress is None:
                return False
            counts = policy.map_summary(progress)["counts"]
            ratio = float(settings.get("ratio") or 1.0)
            return counts["total"] > 0 and counts["mastered"] >= ratio * counts["total"] - 1e-9
        if kind == "book":
            from deeptutor.multi_user.book_access import resolve_book

            pages = set(settings.get("page_ids") or [])
            if not pages:
                return False
            resolved = resolve_book(f"book:{target_id}")
            if resolved is None:
                return False
            progress = resolved.load_progress(target_id)
            return pages.issubset(set(progress.visited_page_ids))
    except Exception:
        logger.debug("Could not check completion of %s %s", kind, target_id, exc_info=True)
    return False


# ── service ────────────────────────────────────────────────


class TaskService:
    def __init__(self, actor: CurrentUser | None = None) -> None:
        self.actor = actor or get_current_user()

    # authorship

    def _require_author(self) -> str:
        role = _role(self.actor)
        if role not in ("teacher", "admin"):
            raise TaskForbidden("Only teachers and administrators assign tasks.")
        return role

    def _owned(self, conn, task_id: str):
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone() if conn else None
        if row is None or row["owner_user_id"] != self.actor.id:
            raise TaskNotFound("Task not found.")
        return row

    @staticmethod
    def _summary(row) -> dict[str, Any]:
        settings = json.loads(row["settings_json"] or "{}")
        return {
            "id": row["id"],
            "kind": row["kind"],
            "title": row["title"],
            "description": row["description"],
            "target_id": row["target_id"],
            "target_title": _target_title(row["kind"], row["target_id"]) if row["target_id"] else "",
            "settings": settings,
            "due_at": row["due_at"],
            "open_at": _open_at(row),
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "published_at": row["published_at"],
            # Platform batch 2 §5: tasks live in courses; the first one holding it.
            **_course_of(row["id"]),
        }

    def _check_target(self, kind: str, target_id: str, settings: dict[str, Any]) -> dict[str, Any]:
        """The author may only name content they published; returns cleaned settings."""
        if kind == "practice_set":
            from deeptutor.practice_sets import store as practice_store

            with practice_store.read() as conn:
                row = conn.execute(
                    "SELECT owner_user_id, status FROM practice_sets WHERE id = ?", (target_id,)
                ).fetchone() if conn else None
            if row is None or row["owner_user_id"] != self.actor.id or row["status"] != "published":
                raise TaskError("Choose a practice set you published.")
            return {}
        if kind == "mastery_path":
            from deeptutor.teaching.store import TeachingStore

            template = TeachingStore().get_template(target_id)
            if template is None or template.owner_user_id != self.actor.id or template.status != "published":
                raise TaskError("Choose a Mastery Path you published.")
            ratio = float(settings.get("ratio") or 1.0)
            if not 0 < ratio <= 1:
                raise TaskError("The share of mastered points must be between 1% and 100%.")
            return {"ratio": ratio}
        if kind == "book":
            from deeptutor.book import registry as book_registry
            from deeptutor.multi_user.book_access import engine_for

            record = book_registry.get_book(target_id)
            if record is None or not book_registry.can_read_book(record, self.actor):
                raise TaskError("Choose a book you can read.")
            spine = engine_for(record).load_spine(target_id)
            chapters = {c.id: c for c in (spine.chapters if spine else [])}
            chosen = [c for c in dict.fromkeys(settings.get("chapter_ids") or []) if c in chapters]
            if not chosen:
                raise TaskError("Choose at least one chapter.")
            pages = [p for c in chosen for p in chapters[c].page_ids]
            if not pages:
                raise TaskError("The chosen chapters have no pages yet.")
            return {
                "chapter_ids": chosen,
                "chapter_titles": [chapters[c].title for c in chosen],
                "page_ids": pages,
            }
        return {}

    def create(self, kind: str, title: str) -> dict[str, Any]:
        self._require_author()
        if kind not in KINDS:
            raise TaskError("Unknown kind of task.")
        title = str(title or "").strip()
        if not title or len(title) > TITLE_MAX:
            raise TaskError(f"A title must contain 1 to {TITLE_MAX} characters.")
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        now = _now()
        with db.write() as conn:
            conn.execute(
                "INSERT INTO tasks (id, owner_user_id, kind, title, settings_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, self.actor.id, kind, title, "{}", now, now),
            )
        return self.get(task_id)

    def get(self, task_id: str) -> dict[str, Any]:
        self._require_author()
        with db.read() as conn:
            return self._summary(self._owned(conn, task_id))

    def list_mine(self) -> list[dict[str, Any]]:
        self._require_author()
        with db.read() as conn:
            if conn is None:
                return []
            rows = conn.execute(
                "SELECT * FROM tasks WHERE owner_user_id = ? ORDER BY updated_at DESC", (self.actor.id,)
            ).fetchall()
        out = []
        for row in rows:
            summary = self._summary(row)
            progress = self._progress(row) if row["status"] != "draft" else {"total": 0, "completed": 0}
            out.append({
                **summary, **progress,
                "target_available": self._target_usable(row),
            })
        return out

    def _target_usable(self, row) -> bool:
        if row["kind"] == "note":
            return True
        return _target_audience(row["kind"], row["target_id"]) != set()

    def update(self, task_id: str, **fields: Any) -> dict[str, Any]:
        """A draft changes freely; a published task keeps its kind and content (§3.3)."""
        self._require_author()
        with db.read() as conn:
            row = self._owned(conn, task_id)
        if row["status"] == "archived":
            raise TaskConflict("This task was withdrawn.")
        published = row["status"] == "published"
        sets: dict[str, Any] = {}
        if fields.get("title") is not None:
            title = str(fields["title"]).strip()
            if not title or len(title) > TITLE_MAX:
                raise TaskError(f"A title must contain 1 to {TITLE_MAX} characters.")
            sets["title"] = title
        if fields.get("description") is not None:
            sets["description"] = str(fields["description"])[:TEXT_MAX]
        if "due_at" in fields:
            due = fields["due_at"]
            sets["due_at"] = float(due) if due else None
        if "open_at" in fields:
            opens = float(fields["open_at"]) if fields["open_at"] else None
            before = _open_at(row)
            # Open now: published with no opening time, or one already passed.
            is_open = published and (before is None or before <= _now())
            if is_open and opens is not None and opens > _now():
                # §6: once open, it stays open — nobody who saw it loses it.
                raise TaskConflict("This task is already open; its opening time can only move earlier.")
            sets["open_at"] = opens
        due_after = sets.get("due_at", row["due_at"])
        open_after = sets.get("open_at", _open_at(row))
        if due_after and open_after and open_after >= due_after:
            raise TaskError("A task must open before its deadline.")
        if fields.get("target_id") is not None or fields.get("settings") is not None:
            if published:
                raise TaskConflict("A published task keeps what it asks for; withdraw it and assign a new one.")
            target = str(fields.get("target_id") if fields.get("target_id") is not None else row["target_id"])
            settings = fields.get("settings")
            if settings is None:
                settings = json.loads(row["settings_json"] or "{}")
            if row["kind"] == "note":
                target = ""
            sets["target_id"] = target
            sets["settings_json"] = json.dumps(settings, ensure_ascii=False)
        if not sets:
            return self.get(task_id)
        sets["updated_at"] = _now()
        columns = ", ".join(f"{k} = ?" for k in sets)
        with db.write() as conn:
            self._owned(conn, task_id)
            conn.execute(f"UPDATE tasks SET {columns} WHERE id = ?", (*sets.values(), task_id))  # nosec B608
        if published and "due_at" in sets and sets["due_at"] != row["due_at"]:
            self._notify_open(row, "task_due_changed", {"due_at": sets["due_at"]})
        return self.get(task_id)

    def hand_over(self, task_id: str, teacher_id: str) -> dict[str, Any]:
        """An administrator gives a task they hold to a teacher (traceability #5)."""
        if _role(self.actor) != "admin":
            raise TaskError("Only administrators hand tasks over.")
        with db.write() as conn:
            self._owned(conn, task_id)
            _hand_tasks_to(conn, self.actor.id, teacher_id, [task_id])
        from deeptutor.notifications.service import notify

        notify([teacher_id], "tasks_transferred", {"count": 1}, link=f"/tasks?task={task_id}",
               dedupe_key=f"task_handed_over:{task_id}:{teacher_id}")
        return {"id": task_id, "owner_user_id": teacher_id}

    def publish(self, task_id: str, *, quiet: bool = False) -> dict[str, Any]:
        """Check what it names, settle its receivers (§3.2), then publish and notify.

        A task that opens later is announced when it opens (batch 2 §6); a
        course announces its tasks once, as the course (*quiet*).
        """
        from deeptutor.publishing import scope as scopes

        self._require_author()
        with db.read() as conn:
            row = self._owned(conn, task_id)
        if row["status"] != "draft":
            raise TaskConflict("This task was already published.")
        if not _in_a_course(task_id):
            # Learning platform: tasks go out as part of a course.
            raise TaskError("Add the task to a course; it is published with the course.")
        kind = row["kind"]
        settings = self._check_target(kind, row["target_id"], json.loads(row["settings_json"] or "{}")) \
            if kind not in ("note",) else {}
        had_scope = scopes.get_scope(scopes.TASK, task_id) is not None
        scopes.default_scope_on_publish(scopes.TASK, task_id)
        scope = scopes.get_scope(scopes.TASK, task_id)
        try:
            receivers = scopes.audience(scope, students_only=True, owner_id=self.actor.id) if scope else set()
            reach = _target_audience(kind, row["target_id"])
            if reach is not None and receivers - reach:
                raise TaskError(
                    "Some of these students cannot open what this task asks for. Widen who receives it first."
                )
        except Exception:
            if not had_scope:
                scopes.forget(scopes.TASK, task_id)
            raise
        now = _now()
        with db.write() as conn:
            self._owned(conn, task_id)
            conn.execute(
                "UPDATE tasks SET status = 'published', settings_json = ?, published_at = ?, updated_at = ? "
                "WHERE id = ?",
                (json.dumps(settings, ensure_ascii=False), now, now, task_id),
            )
        opens = _open_at(row)
        if not quiet and not (opens and opens > now):
            from deeptutor.notifications.service import notify

            notify(
                receivers, "task_published",
                {"title": row["title"], "teacher": self.actor.username, "due_at": row["due_at"]},
                link=f"/start?task={task_id}", dedupe_key=f"task_published:{task_id}",
            )
        return self.get(task_id)

    def archive(self, task_id: str) -> dict[str, Any]:
        """Withdraw: the author, or (D10) an administrator for anyone's published task."""
        self._require_author()
        with db.write() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None or not (
                row["owner_user_id"] == self.actor.id
                or (_role(self.actor) == "admin" and row["status"] == "published")
            ):
                raise TaskNotFound("Task not found.")
            conn.execute(
                "UPDATE tasks SET status = 'archived', updated_at = ? WHERE id = ?", (_now(), task_id)
            )
        if row["owner_user_id"] != self.actor.id:
            return {**self._summary(row), "status": "archived"}
        return self.get(task_id)

    def delete(self, task_id: str) -> None:
        """Only a never-published task is deleted (R19); otherwise an administrator withdraws."""
        self._require_author()
        with db.read() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone() if conn else None
        if row is not None and row["owner_user_id"] != self.actor.id:
            self.archive(task_id)
            return
        with db.write() as conn:
            row = self._owned(conn, task_id)
            if row["status"] != "draft":
                raise TaskConflict("Published tasks cannot be deleted; withdraw them instead.")
            conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        from deeptutor.publishing.scope import TASK, forget

        forget(TASK, task_id)

    # the author's view of their students

    def _receivers(self, row) -> set[str]:
        from deeptutor.publishing import scope as scopes

        scope = scopes.get_scope(scopes.TASK, row["id"])
        return scopes.audience(scope, students_only=True, owner_id=row["owner_user_id"]) if scope else set()

    def _progress(self, row) -> dict[str, int]:
        receivers = self._receivers(row)
        with db.read() as conn:
            done = {
                r[0] for r in conn.execute(
                    "SELECT student_id FROM task_completions WHERE task_key = ?", (row["id"],)
                )
            } if conn else set()
        return {"total": len(receivers), "completed": len(receivers & done)}

    def detail(self, task_id: str) -> dict[str, Any]:
        """Each receiver's state."""
        self._require_author()
        with db.read() as conn:
            row = self._owned(conn, task_id)
            completions = {
                r["student_id"]: r for r in conn.execute(
                    "SELECT * FROM task_completions WHERE task_key = ?", (task_id,)
                )
            }
        receivers = self._receivers(row)
        accounts = _accounts()
        students = []
        for student_id in sorted(receivers | set(completions),
                                 key=lambda s: (accounts.get(s) or {}).get("username", s)):
            account = accounts.get(student_id)
            if account is None:
                continue
            done = completions.get(student_id)
            students.append({
                "id": student_id,
                "username": account["username"],
                "in_scope": student_id in receivers,
                "completed": bool(done),
                "completed_at": done["completed_at"] if done else None,
                "late": bool(done and done["late"]),
                "self_reported": bool(done and done["self_reported"]),
            })
        return {**self._summary(row), "students": students}

    def _notify_open(self, row, kind: str, params: dict[str, Any]) -> None:
        """Tell the receivers who have not completed the task yet."""
        from deeptutor.notifications.service import notify

        receivers = self._receivers(row)
        with db.read() as conn:
            done = {
                r[0] for r in conn.execute(
                    "SELECT student_id FROM task_completions WHERE task_key = ?", (row["id"],)
                )
            } if conn else set()
        notify(
            receivers - done, kind, {"title": row["title"], **params},
            link=f"/start?task={row['id']}", dedupe_key=f"{kind}:{row['id']}:{params.get('due_at')}",
        )


# ── the student's side ─────────────────────────────────────


class StudentTasks:
    """What reaches the current student, with its live state."""

    def __init__(self, actor: CurrentUser | None = None) -> None:
        self.actor = actor or get_current_user()

    def _is_student(self) -> bool:
        """A learner here: a student, or anyone who joined something (platform batch 2 §6)."""
        if _role(self.actor) == "student":
            return True
        from deeptutor.catalog.enrollment import joined_by

        return bool(joined_by(self.actor.id))

    def _task_rows(self) -> list:
        """Published or withdrawn tasks that reach the student, or that they worked on."""
        from deeptutor.publishing import scope as scopes

        # A task reaches who joined it (through its course) or was named; only those are read.
        from deeptutor.catalog.enrollment import joined_by

        wanted = [item_id for kind, item_id in joined_by(self.actor.id) if kind == scopes.TASK] + \
            scopes.named_items(scopes.TASK, self.actor.id) + _everyone_tasks()
        with db.read() as conn:
            if conn is None:
                return []
            mine = {
                r[0] for r in conn.execute(
                    "SELECT task_key FROM task_completions WHERE student_id = ?", (self.actor.id,),
                )
            }
            ids = sorted(set(wanted) | mine)
            rows = [
                row for start in range(0, len(ids), 500)
                for row in conn.execute(
                    f"SELECT * FROM tasks WHERE status != 'draft' AND id IN ({','.join('?' for _ in ids[start:start + 500])})",  # nosec B608
                    ids[start:start + 500],
                )
            ]
        student = _role(self.actor) == "student"
        out = []
        for row in rows:
            scope = scopes.get_scope(scopes.TASK, row["id"])
            reaches = bool(scope) and row["owner_user_id"] != self.actor.id and (
                (student and scopes._reaches(scope, self.actor.id)) or self.actor.id in scope.members
            )
            if reaches or row["id"] in mine:
                out.append((row, reaches))
        return out

    def _completion(self, key: str):
        with db.read() as conn:
            return conn.execute(
                "SELECT * FROM task_completions WHERE task_key = ? AND student_id = ?", (key, self.actor.id)
            ).fetchone() if conn else None

    def _record_completion(
        self, key: str, due_at: float | None, *, self_reported: bool = False, at: float | None = None,
    ) -> None:
        # ``at``: when it was actually done, if known (a practice set's first submission);
        # otherwise when it is noticed.
        now = at or _now()
        with db.write() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO task_completions VALUES (?, ?, ?, ?, ?)",
                (key, self.actor.id, now, int(bool(due_at and now > due_at)), int(self_reported)),
            )

    def _entry(self, *, key: str, kind: str, title: str, description: str, target_id: str,
               settings: dict[str, Any], due_at: float | None, teacher_id: str, open_: bool,
               task_row=None, open_at: float | None = None) -> dict[str, Any]:
        from deeptutor.publishing.provenance import provenance

        now = _now()
        if open_ and open_at and now < open_at:
            # §6: before it opens, only that it exists and when it opens.
            return {
                "key": key, "task_id": task_row["id"] if task_row is not None else "", "kind": kind,
                "title": title, "description": "", "target_id": "", "target_title": "", "settings": {},
                "due_at": due_at, "open_at": open_at, "state": "not_open", "completed_at": None, "late": False,
                "provenance": provenance(teacher_id, self.actor), "teacher_id": teacher_id,
            }
        if open_ and open_at and task_row is not None and open_at > float(task_row["published_at"] or 0):
            from deeptutor.notifications.service import notify

            notify(
                [self.actor.id], "task_opened", {"title": title, "due_at": due_at},
                link=f"/start?task={key}", dedupe_key=f"task_opened:{key}",
            )
        completion = self._completion(key)
        if completion is None and open_ and kind != "note":
            if _completed_now(kind, target_id, settings, self.actor):
                self._record_completion(
                    key, due_at, at=_first_submission(target_id, self.actor.id) if kind == "practice_set" else None,
                )
                completion = self._completion(key)
        if completion is not None:
            state = "completed"
        elif not open_:
            state = "closed"
        elif due_at and now > due_at:
            state = "overdue"
        else:
            state = "open"
        entry = {
            "key": key,
            "task_id": task_row["id"] if task_row is not None else "",
            "kind": kind,
            "title": title,
            "description": description,
            "target_id": target_id,
            "target_title": _target_title(kind, target_id) if target_id else "",
            "settings": settings,
            "due_at": due_at,
            "open_at": open_at,
            "state": state,
            "completed_at": completion["completed_at"] if completion else None,
            "late": bool(completion and completion["late"]),
            "provenance": provenance(teacher_id, self.actor),
            # Who set it (batch 3 §5.3: a teacher's "overdue" counts only their own tasks).
            "teacher_id": teacher_id,
        }
        if state != "completed" and state != "closed" and due_at and 0 < due_at - now <= DUE_SOON_SECONDS:
            from deeptutor.notifications.service import notify

            # Spec §5.1: no scheduler; whoever looks first triggers the reminder.
            notify(
                [self.actor.id], "task_due_soon", {"title": title, "due_at": due_at},
                link=f"/start?task={key}", dedupe_key=f"task_due_soon:{key}:{due_at}",
            )
        return entry

    def sweep(self) -> None:
        """Time-based notifications without a scheduler (batch 2 §9).

        Called when the student's bell asks for its unread count: a task that
        opened, or one due within a day and not completed, is announced once.
        Cheap on purpose — no completion detection, only stored completions.
        """
        if not self._is_student():
            return
        from deeptutor.notifications.service import notify

        now = _now()
        for row, reaches in self._task_rows():
            if row["status"] != "published" or not reaches:
                continue
            opens, due = _open_at(row), row["due_at"]
            if opens and now < opens:
                continue
            if opens and opens > float(row["published_at"] or 0):
                # Only a task published before it opened is announced at opening;
                # one published already open was announced by its publication.
                notify([self.actor.id], "task_opened", {"title": row["title"], "due_at": due},
                       link=f"/start?task={row['id']}", dedupe_key=f"task_opened:{row['id']}")
            if due and 0 < due - now <= DUE_SOON_SECONDS and self._completion(row["id"]) is None:
                notify([self.actor.id], "task_due_soon", {"title": row["title"], "due_at": due},
                       link=f"/start?task={row['id']}", dedupe_key=f"task_due_soon:{row['id']}:{due}")

    def assigned(self) -> list[dict[str, Any]]:
        """Every task that reached the learner through the courses they joined (or by name)."""
        if not self._is_student():
            return []
        entries: list[dict[str, Any]] = []
        for row, reaches in self._task_rows():
            settings = json.loads(row["settings_json"] or "{}")
            open_ = (
                row["status"] == "published" and reaches
                and _target_available_to(row["kind"], row["target_id"], self.actor)
            )
            entry = self._entry(
                key=row["id"], kind=row["kind"], title=row["title"], description=row["description"],
                target_id=row["target_id"], settings=settings, due_at=row["due_at"],
                teacher_id=row["owner_user_id"], open_=open_, task_row=row, open_at=_open_at(row),
            )
            entries.append(entry)
        order = {"overdue": 0, "open": 1, "not_open": 2, "completed": 3, "closed": 4}
        entries.sort(key=lambda e: (order[e["state"]], e["due_at"] or float("inf"), e["title"]))
        return entries

    @staticmethod
    def _practice_owner(set_id: str) -> str:
        from deeptutor.practice_sets import store as practice_store

        with practice_store.read() as conn:
            row = conn.execute("SELECT owner_user_id FROM practice_sets WHERE id = ?", (set_id,)).fetchone() if conn else None
        return row["owner_user_id"] if row else ""

    def get(self, key: str) -> dict[str, Any]:
        for entry in self.assigned():
            if entry["key"] == key:
                return entry
        raise TaskNotFound("Task not found.")

    def mark_done(self, task_id: str) -> dict[str, Any]:
        entry = self.get(task_id)
        if entry["kind"] != "note":
            raise TaskError("This task completes itself when you do what it asks.")
        if entry["state"] in ("closed", "not_open"):
            raise TaskConflict("This task is closed." if entry["state"] == "closed" else "This task is not open yet.")
        self._record_completion(task_id, entry["due_at"], self_reported=True)
        return self.get(task_id)

# ── account events ─────────────────────────────────────────


def transfer_tasks(
    user_id: str, successor_id: str, *, published_only: bool = False, counts: dict[str, int] | None = None,
) -> int:
    """A departing teacher's tasks go to *successor_id*."""
    with db.read() as conn:
        if conn is None:
            return 0
        rows = conn.execute(
            "SELECT id, kind, status, title FROM tasks WHERE owner_user_id = ?"
            + (" AND status != 'draft'" if published_only else ""),
            (user_id,),
        ).fetchall()
    if not rows:
        from deeptutor.tasks.courses import transfer_courses

        # Courses go even when no task does (a course may hold none yet).
        courses = transfer_courses(user_id, successor_id, published_only=published_only)
        if counts is not None:
            counts.update(tasks=0, courses=courses)
        return 0
    moves: dict[str, str] = {row["id"]: successor_id for row in rows}
    with db.write() as conn:
        conn.executemany(
            "UPDATE tasks SET owner_user_id = ?, updated_at = ? WHERE id = ?",
            [(target, _now(), task_id) for task_id, target in moves.items()],
        )
    from deeptutor.tasks.courses import transfer_courses

    # The successor hears it in one summary of everything handed over (batch 4 §3).
    courses = transfer_courses(user_id, successor_id, published_only=published_only)
    if counts is not None:
        counts["tasks"] = len(moves)
        counts["courses"] = courses
    return len(moves)


def forget_student(user_id: str) -> None:
    """A deleted student's completions go (§7)."""
    if not db.path().exists():
        return
    with db.write() as conn:
        conn.execute("DELETE FROM task_completions WHERE student_id = ?", (user_id,))


def remove_homework() -> int:
    """Startup, once per data dir: homework is gone from the platform (spec 2026-11-05).

    Deletes every homework task with its place in courses, completions,
    receivers and joins, the hand-ins with their files, and the homework
    notifications. What points at the tasks goes first and the tasks last, so
    a step that fails leaves the tasks to find again at the next start.
    Does nothing once done.
    """
    if not db.path().exists():
        return 0
    with db.read() as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM tasks WHERE kind = 'homework'")]
        had_hand_ins = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'task_submissions'"
        ).fetchone() is not None
    files = files_root()
    if not ids and not had_hand_ins and not files.exists():
        return 0
    from deeptutor.catalog.enrollment import forget_item
    from deeptutor.notifications.service import forget_kinds
    from deeptutor.publishing.scope import TASK, forget

    for task_id in ids:
        forget(TASK, task_id)
        forget_item(TASK, task_id)
    forget_kinds(["homework_submitted", "homework_graded", "homework_regraded", "homework_returned"])
    if files.exists():
        shutil.rmtree(files)
    with db.write() as conn:
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            marks = ",".join("?" for _ in chunk)
            conn.execute(f"DELETE FROM course_items WHERE task_id IN ({marks})", chunk)  # nosec B608
            conn.execute(f"DELETE FROM task_completions WHERE task_key IN ({marks})", chunk)  # nosec B608
            conn.execute(f"DELETE FROM tasks WHERE id IN ({marks})", chunk)  # nosec B608
        conn.execute("DROP TABLE IF EXISTS task_submissions")
    logger.info("Removed %d homework tasks", len(ids))
    return len(ids)


__all__ = [
    "StudentTasks",
    "TaskConflict",
    "TaskError",
    "TaskForbidden",
    "TaskNotFound",
    "TaskService",
    "forget_student",
    "remove_homework",
    "transfer_tasks",
]
