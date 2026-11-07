"""Tasks (spec 2026-11-03 batch 1 §3).

An author (or administrator) asks learners to do one thing: study a Mastery
Path, answer a practice set, read chapters of a book, or a plain
instruction. The task reaches learners through a publish scope (kind
``task``) and only ever names content its author published.

On the learner side a task is *open*, *completed* or *closed*: tasks have
no deadline or opening time (spec 2026-11-05 remove-teacher-management §1.4;
the columns are kept and never read). Completion is detected from what the
learner actually did and then kept (R24). *Closed* is never stored: it
is whether the task, its content and the learner's place in its scope still
hold right now, so coming back into scope restores it.

Practice sets and Mastery Paths a learner received and no task names are
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
from deeptutor.tasks.store import db

logger = logging.getLogger(__name__)

KINDS = ("mastery_path", "practice_set", "book", "note")
TITLE_MAX = 200
TEXT_MAX = 20_000


class TaskError(Exception):
    status = 400


class TaskNotFound(TaskError):
    status = 404


class TaskConflict(TaskError):
    status = 409


def _now() -> float:
    return time.time()


def _role(user: CurrentUser) -> str:
    from deeptutor.multi_user.accounts import live_role

    return live_role(user)


def _accounts() -> dict[str, dict[str, Any]]:
    from deeptutor.multi_user.accounts import accounts

    return accounts()


# ── targets ────────────────────────────────────────────────


def _hand_tasks_to(conn, admin_id: str, author_id: str, task_ids: list[str]) -> int:
    """Inside a write: the administrator's tasks among *task_ids* go to an active learner (spec 2026-11-05 §4)."""
    account = _accounts().get(author_id)
    if account is None or account["role"] == "admin" or account["disabled"]:
        raise TaskError("Choose an active learner.")
    if not task_ids:
        return 0
    marks = ",".join("?" for _ in task_ids)
    return int(conn.execute(
        f"UPDATE tasks SET owner_user_id = ?, updated_at = ? WHERE owner_user_id = ? AND id IN ({marks})",  # nosec B608
        (author_id, _now(), admin_id, *task_ids),
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
    """Learners the referenced content reaches; ``None`` means everyone."""
    from deeptutor.publishing import scope as scopes

    if kind == "practice_set":
        scope = scopes.get_scope(scopes.PRACTICE_SET, target_id)
        return scopes.audience(scope, learners_only=True) if scope else set()
    if kind == "mastery_path":
        scope = scopes.get_scope(scopes.MASTERY_PATH, target_id)
        if scope is not None:
            return scopes.audience(scope, learners_only=True)
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
        return None if scope is None else scopes.audience(scope, learners_only=True)
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


def _target_archived(kind: str, target_id: str) -> bool:
    """Its author archived the practice set or Mastery Path a lesson asks for (audit fix B §5)."""
    if not target_id:
        return False
    if kind == "practice_set":
        from deeptutor.practice_sets import store as practice_store

        with practice_store.read() as conn:
            row = conn.execute("SELECT status FROM practice_sets WHERE id = ?", (target_id,)).fetchone() if conn else None
        return bool(row and row["status"] == "archived")
    if kind == "mastery_path":
        from deeptutor.teaching.store import TeachingStore

        template = TeachingStore().get_template(target_id)
        return bool(template and template.status == "archived")
    return False


def _target_available_to(kind: str, target_id: str, learner: CurrentUser) -> bool:
    """Whether the learner can still open the referenced content."""
    if kind == "note":
        return True
    if kind == "practice_set":
        from deeptutor.practice_sets import store as practice_store
        from deeptutor.publishing import scope as scopes

        with practice_store.read() as conn:
            row = conn.execute("SELECT status FROM practice_sets WHERE id = ?", (target_id,)).fetchone() if conn else None
        scope = scopes.get_scope(scopes.PRACTICE_SET, target_id)
        return bool(row and row["status"] == "published" and scope and scopes._reaches(scope, learner.id))
    if kind == "mastery_path":
        from deeptutor.teaching.store import TeachingStore

        teaching = TeachingStore()
        template = teaching.get_template(target_id)
        assignment = teaching.get_assignment(target_id, learner.id)
        return bool(
            template and template.status == "published" and assignment and assignment.status == "active"
        )
    if kind == "book":
        from deeptutor.book import registry as book_registry

        record = book_registry.get_book(target_id)
        return bool(record and book_registry.can_read_book(record, learner))
    return False


def _first_submission(set_id: str, learner_id: str) -> float | None:
    """When the learner first submitted a practice set: the moment it was done."""
    from deeptutor.practice_sets import store as practice_store

    with practice_store.read() as conn:
        row = conn.execute(
            "SELECT MIN(submitted_at) FROM practice_submissions WHERE set_id = ? AND student_id = ?",
            (set_id, learner_id),
        ).fetchone() if conn else None
    return float(row[0]) if row and row[0] else None


def _completed_now(kind: str, target_id: str, settings: dict[str, Any], learner: CurrentUser) -> bool:
    """Whether what the learner did meets the task (notes are stored, not detected)."""
    try:
        if kind == "practice_set":
            from deeptutor.practice_sets import store as practice_store

            with practice_store.read() as conn:
                return bool(conn and conn.execute(
                    "SELECT 1 FROM practice_submissions WHERE set_id = ? AND student_id = ? LIMIT 1",
                    (target_id, learner.id),
                ).fetchone())
        if kind == "mastery_path":
            from deeptutor.learning import policy
            from deeptutor.learning.storage import LearningStore
            from deeptutor.teaching.instances import instance_path_id
            from deeptutor.teaching.store import TeachingStore

            assignment = TeachingStore().get_assignment(target_id, learner.id)
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

    def _owned(self, conn, task_id: str):
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone() if conn else None
        if row is None or row["owner_user_id"] != self.actor.id:
            raise TaskNotFound("Lesson not found.")
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
            "target_archived": _target_archived(row["kind"], row["target_id"]),
            "settings": settings,
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
            if record.visibility != "public":
                # Audit fix A: the author may read their private book; the course's learners may not.
                from deeptutor.catalog.items import private_course_book

                raise TaskError(private_course_book(record.title))
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
        if kind not in KINDS:
            raise TaskError("Unknown kind of lesson.")
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
        with db.read() as conn:
            return self._summary(self._owned(conn, task_id))

    def list_mine(self) -> list[dict[str, Any]]:
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
        with db.read() as conn:
            row = self._owned(conn, task_id)
        if row["status"] == "archived":
            raise TaskConflict("This lesson was withdrawn.")
        published = row["status"] == "published"
        sets: dict[str, Any] = {}
        if fields.get("title") is not None:
            title = str(fields["title"]).strip()
            if not title or len(title) > TITLE_MAX:
                raise TaskError(f"A title must contain 1 to {TITLE_MAX} characters.")
            sets["title"] = title
        if fields.get("description") is not None:
            sets["description"] = str(fields["description"])[:TEXT_MAX]
        if fields.get("target_id") is not None or fields.get("settings") is not None:
            if published:
                raise TaskConflict("A published lesson keeps what it asks for; withdraw it and add a new one.")
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
        return self.get(task_id)

    def hand_over(self, task_id: str, author_id: str) -> dict[str, Any]:
        """An administrator gives a task they hold to a learner (traceability #5)."""
        if _role(self.actor) != "admin":
            raise TaskError("Only administrators hand lessons over.")
        with db.write() as conn:
            self._owned(conn, task_id)
            _hand_tasks_to(conn, self.actor.id, author_id, [task_id])
        from deeptutor.notifications.service import notify

        notify([author_id], "tasks_transferred", {"count": 1}, link=f"/tasks?task={task_id}",
               dedupe_key=f"task_handed_over:{task_id}:{author_id}")
        return {"id": task_id, "owner_user_id": author_id}

    def publish(self, task_id: str, *, quiet: bool = False) -> dict[str, Any]:
        """Check what it names, settle its receivers (§3.2), then publish and notify.

        A course announces its tasks once, as the course (*quiet*).
        """
        from deeptutor.publishing import scope as scopes

        with db.read() as conn:
            row = self._owned(conn, task_id)
        if row["status"] != "draft":
            raise TaskConflict("This lesson was already published.")
        if not _in_a_course(task_id):
            # Learning platform: tasks go out as part of a course.
            raise TaskError("Add the lesson to a course; it is published with the course.")
        kind = row["kind"]
        settings = self._check_target(kind, row["target_id"], json.loads(row["settings_json"] or "{}")) \
            if kind not in ("note",) else {}
        had_scope = scopes.get_scope(scopes.TASK, task_id) is not None
        scopes.default_scope_on_publish(scopes.TASK, task_id)
        scope = scopes.get_scope(scopes.TASK, task_id)
        try:
            receivers = scopes.audience(scope, learners_only=True, owner_id=self.actor.id) if scope else set()
            reach = _target_audience(kind, row["target_id"])
            if reach is not None and receivers - reach:
                raise TaskError(
                    "Some learners of this lesson cannot open what it asks for. Use your own content or content in the market."
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
        if not quiet:
            from deeptutor.notifications.service import notify

            notify(
                receivers, "task_published",
                # ``teacher`` is the author's name (stored as before); ``course`` names the course it joins.
                {"title": row["title"], "teacher": self.actor.username, "course": _course_of(task_id)["course_title"]},
                link=f"/start?task={task_id}", dedupe_key=f"task_published:{task_id}",
            )
        return self.get(task_id)

    def archive(self, task_id: str) -> dict[str, Any]:
        """Withdraw: the author, or (D10) an administrator for anyone's published task."""
        with db.write() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None or not (
                row["owner_user_id"] == self.actor.id
                or (_role(self.actor) == "admin" and row["status"] == "published")
            ):
                raise TaskNotFound("Lesson not found.")
            conn.execute(
                "UPDATE tasks SET status = 'archived', updated_at = ? WHERE id = ?", (_now(), task_id)
            )
        if row["owner_user_id"] != self.actor.id:
            return {**self._summary(row), "status": "archived"}
        return self.get(task_id)

    def delete(self, task_id: str) -> None:
        """Only a never-published task is deleted (R19); otherwise an administrator withdraws."""
        with db.read() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone() if conn else None
        if row is not None and row["owner_user_id"] != self.actor.id:
            self.archive(task_id)
            return
        with db.write() as conn:
            row = self._owned(conn, task_id)
            if row["status"] != "draft":
                raise TaskConflict("Published lessons cannot be deleted; withdraw them instead.")
            conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        from deeptutor.publishing.scope import TASK, forget

        forget(TASK, task_id)

    # the author's view of their learners

    def _receivers(self, row) -> set[str]:
        from deeptutor.publishing import scope as scopes

        scope = scopes.get_scope(scopes.TASK, row["id"])
        return scopes.audience(scope, learners_only=True, owner_id=row["owner_user_id"]) if scope else set()

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
        with db.read() as conn:
            row = self._owned(conn, task_id)
            completions = {
                r["student_id"]: r for r in conn.execute(
                    "SELECT * FROM task_completions WHERE task_key = ?", (task_id,)
                )
            }
        receivers = self._receivers(row)
        accounts = _accounts()
        learners = []
        for learner_id in sorted(receivers | set(completions),
                                 key=lambda s: (accounts.get(s) or {}).get("username", s)):
            account = accounts.get(learner_id)
            if account is None:
                continue
            done = completions.get(learner_id)
            learners.append({
                "id": learner_id,
                "username": account["username"],
                "in_scope": learner_id in receivers,
                "completed": bool(done),
                "completed_at": done["completed_at"] if done else None,
                "self_reported": bool(done and done["self_reported"]),
            })
        return {**self._summary(row), "students": learners}


# ── the learner's side ─────────────────────────────────────


class LearnerTasks:
    """What reaches the current learner, with its live state."""

    def __init__(self, actor: CurrentUser | None = None) -> None:
        self.actor = actor or get_current_user()

    def _is_learner(self) -> bool:
        """A learner here: a learner, or anyone who joined something (platform batch 2 §6)."""
        if _role(self.actor) != "admin":
            return True
        from deeptutor.catalog.enrollment import joined_by

        return bool(joined_by(self.actor.id))

    def _task_rows(self) -> list:
        """Published or withdrawn tasks that reach the learner, or that they worked on."""
        from deeptutor.publishing import scope as scopes

        # A task reaches who joined it (through its course), or whom older data named; only those are read.
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
        learner = _role(self.actor) != "admin"
        out = []
        for row in rows:
            scope = scopes.get_scope(scopes.TASK, row["id"])
            reaches = bool(scope) and row["owner_user_id"] != self.actor.id and (
                (learner and scopes._reaches(scope, self.actor.id)) or self.actor.id in scope.members
            )
            if reaches or row["id"] in mine:
                out.append((row, reaches))
        return out

    def _completion(self, key: str):
        with db.read() as conn:
            return conn.execute(
                "SELECT * FROM task_completions WHERE task_key = ? AND student_id = ?", (key, self.actor.id)
            ).fetchone() if conn else None

    def _record_completion(self, key: str, *, self_reported: bool = False, at: float | None = None) -> None:
        # ``at``: when it was actually done, if known (a practice set's first submission);
        # otherwise when it is noticed. Nothing is late: tasks have no deadline.
        now = at or _now()
        with db.write() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO task_completions VALUES (?, ?, ?, ?, ?)",
                (key, self.actor.id, now, 0, int(self_reported)),
            )

    def _entry(self, *, key: str, kind: str, title: str, description: str, target_id: str,
               settings: dict[str, Any], author_id: str, open_: bool, task_row=None) -> dict[str, Any]:
        from deeptutor.publishing.provenance import provenance

        completion = self._completion(key)
        if completion is None and open_ and kind != "note":
            if _completed_now(kind, target_id, settings, self.actor):
                self._record_completion(
                    key, at=_first_submission(target_id, self.actor.id) if kind == "practice_set" else None,
                )
                completion = self._completion(key)
        if completion is not None:
            state = "completed"
        elif not open_:
            state = "closed"
        else:
            state = "open"
        return {
            "key": key,
            "task_id": task_row["id"] if task_row is not None else "",
            "kind": kind,
            "title": title,
            "description": description,
            "target_id": target_id,
            "target_title": _target_title(kind, target_id) if target_id else "",
            # Audit fix B §5: why a lesson closed, when its author archived what it asks for.
            "target_archived": state == "closed" and _target_archived(kind, target_id),
            "settings": settings,
            "state": state,
            "completed_at": completion["completed_at"] if completion else None,
            "provenance": provenance(author_id, self.actor),
            "teacher_id": author_id,
        }

    def assigned(self) -> list[dict[str, Any]]:
        """Every task that reached the learner through the courses they joined (or by name)."""
        if not self._is_learner():
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
                target_id=row["target_id"], settings=settings,
                author_id=row["owner_user_id"], open_=open_, task_row=row,
            )
            entries.append(entry)
        order = {"open": 0, "completed": 1, "closed": 2}
        entries.sort(key=lambda e: (order[e["state"]], e["title"]))
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
        raise TaskNotFound("Lesson not found.")

    def mark_done(self, task_id: str) -> dict[str, Any]:
        entry = self.get(task_id)
        if entry["kind"] != "note":
            raise TaskError("This lesson completes itself when you do what it asks.")
        if entry["state"] == "closed":
            raise TaskConflict("This lesson is closed.")
        self._record_completion(task_id, self_reported=True)
        return self.get(task_id)

# ── account events ─────────────────────────────────────────


def transfer_tasks(
    user_id: str, successor_id: str, *, published_only: bool = False, counts: dict[str, int] | None = None,
) -> int:
    """A departing author's tasks go to *successor_id*."""
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


def forget_learner(user_id: str) -> None:
    """A deleted learner's completions go (§7)."""
    if not db.path().exists():
        return
    with db.write() as conn:
        conn.execute("DELETE FROM task_completions WHERE student_id = ?", (user_id,))


__all__ = [
    "LearnerTasks",
    "TaskConflict",
    "TaskError",
    "TaskNotFound",
    "TaskService",
    "forget_learner",
    "transfer_tasks",
]
