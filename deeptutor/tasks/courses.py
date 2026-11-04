"""Teacher courses (spec 2026-11-03 batch 2 §5).

A course is tasks in an order, with a name and a description. Everything a
student does happens in its tasks — completion, deadlines, opening times,
homework — so a course adds no rules of its own: it is how a teacher lays
out a term. It reaches students through a publish scope (kind ``course``).

Publishing a course publishes its draft tasks to the course's receivers; an
already published task must already reach all of them. Withdrawing a course
only hides the course: its tasks stay as they are. The order is a
suggestion, never a lock.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any
import uuid

from deeptutor.multi_user.context import get_current_user
from deeptutor.multi_user.models import CurrentUser
from deeptutor.tasks.service import (
    StudentTasks,
    TaskConflict,
    TaskError,
    TaskNotFound,
    TaskService,
    TITLE_MAX,
    TEXT_MAX,
    _role,
)
from deeptutor.tasks.store import db


class CourseService:
    def __init__(self, actor: CurrentUser | None = None) -> None:
        self.actor = actor or get_current_user()
        self.tasks = TaskService(self.actor)

    def _owned(self, conn, course_id: str):
        row = conn.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone() if conn else None
        if row is None or row["owner_user_id"] != self.actor.id:
            raise TaskNotFound("Course not found.")
        return row

    @staticmethod
    def _items(conn, course_id: str) -> list[str]:
        return [r[0] for r in conn.execute(
            "SELECT task_id FROM course_items WHERE course_id = ? ORDER BY position", (course_id,)
        )] if conn else []

    def _view(self, row, items: list[str]) -> dict[str, Any]:
        tasks = []
        for task_id in items:
            try:
                tasks.append(self.tasks.get(task_id))
            except TaskError:
                # A task handed to someone else (homework to a co-teacher) stays in
                # the course, shown but not editable here.
                with db.read() as conn:
                    other = conn.execute(
                        "SELECT id, kind, title, status, due_at, open_at FROM tasks WHERE id = ?", (task_id,)
                    ).fetchone() if conn else None
                if other is not None:
                    tasks.append({**dict(other), "owned": False})
        return {
            "id": row["id"], "title": row["title"], "description": row["description"], "status": row["status"],
            "created_at": row["created_at"], "updated_at": row["updated_at"], "published_at": row["published_at"],
            "tasks": tasks,
        }

    # authoring

    def create(self, title: str) -> dict[str, Any]:
        self.tasks._require_author()
        title = str(title or "").strip()
        if not title or len(title) > TITLE_MAX:
            raise TaskError(f"A title must contain 1 to {TITLE_MAX} characters.")
        course_id = f"crs_{uuid.uuid4().hex[:12]}"
        now = time.time()
        with db.write() as conn:
            conn.execute(
                "INSERT INTO courses (id, owner_user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (course_id, self.actor.id, title, now, now),
            )
        return self.get(course_id)

    def get(self, course_id: str) -> dict[str, Any]:
        self.tasks._require_author()
        with db.read() as conn:
            row = self._owned(conn, course_id)
            items = self._items(conn, course_id)
        return self._view(row, items)

    def list_mine(self) -> list[dict[str, Any]]:
        self.tasks._require_author()
        with db.read() as conn:
            if conn is None:
                return []
            try:
                rows = conn.execute(
                    "SELECT c.*, (SELECT COUNT(*) FROM course_items i WHERE i.course_id = c.id) AS n "
                    "FROM courses c WHERE owner_user_id = ? ORDER BY updated_at DESC",
                    (self.actor.id,),
                ).fetchall()
            except sqlite3.OperationalError:
                return []  # a task store from before courses; the next write adds the tables
        return [{"id": r["id"], "title": r["title"], "status": r["status"], "task_count": r["n"],
                 "updated_at": r["updated_at"]} for r in rows]

    def update(self, course_id: str, *, title: str | None = None, description: str | None = None,
               task_ids: list[str] | None = None) -> dict[str, Any]:
        """Rename, describe, or set the tasks and their order (§5.1)."""
        self.tasks._require_author()
        with db.read() as conn:
            row = self._owned(conn, course_id)
        if row["status"] == "archived":
            raise TaskConflict("This course was withdrawn.")
        with db.write() as conn:
            self._owned(conn, course_id)
            if title is not None:
                title = str(title).strip()
                if not title or len(title) > TITLE_MAX:
                    raise TaskError(f"A title must contain 1 to {TITLE_MAX} characters.")
                conn.execute("UPDATE courses SET title = ? WHERE id = ?", (title, course_id))
            if description is not None:
                conn.execute("UPDATE courses SET description = ? WHERE id = ?", (str(description)[:TEXT_MAX], course_id))
            if task_ids is not None:
                wanted = list(dict.fromkeys(str(t) for t in task_ids))
                mine = {
                    r[0] for r in conn.execute(
                        "SELECT id FROM tasks WHERE owner_user_id = ? AND status != 'archived'", (self.actor.id,)
                    )
                }
                kept = set(self._items(conn, course_id))
                # New items are one's own tasks; items already there may have been handed to others.
                if any(t not in mine and t not in kept for t in wanted):
                    raise TaskError("A course holds your own tasks that are not withdrawn.")
                added = [t for t in wanted if t not in kept]
                conn.execute("DELETE FROM course_items WHERE course_id = ?", (course_id,))
                conn.executemany(
                    "INSERT INTO course_items VALUES (?, ?, ?)",
                    [(course_id, task_id, index) for index, task_id in enumerate(wanted)],
                )
            conn.execute("UPDATE courses SET updated_at = ? WHERE id = ?", (time.time(), course_id))
        if row["status"] == "published" and task_ids is not None:
            # Tasks added to a published course follow its receivers (§5.1); a task
            # already published must already reach them all, as at publication.
            from deeptutor.catalog.enrollment import refresh_course
            from deeptutor.publishing import scope as scopes

            if not scopes.join_only_scope(scopes._stored_scope(scopes.COURSE, course_id)):
                self._check_coverage(course_id, added)
            self._publish_drafts(course_id)
            # Its members get the new tasks and what they cite (platform batch 1 §4.3).
            refresh_course(course_id)
        return self.get(course_id)

    def _check_coverage(self, course_id: str, task_ids: list[str]) -> None:
        from deeptutor.publishing import scope as scopes

        if not task_ids:
            return
        scope = scopes.get_scope(scopes.COURSE, course_id)
        receivers = scopes.audience(scope, students_only=True, owner_id=self.actor.id) if scope else set()
        with db.read() as conn:
            published = [
                r[0] for r in conn.execute(
                    f"SELECT id FROM tasks WHERE status = 'published' AND id IN ({','.join('?' for _ in task_ids)})",  # nosec B608
                    task_ids,
                )
            ] if conn else []
        for task_id in published:
            task_scope = scopes.get_scope(scopes.TASK, task_id)
            reach = scopes.audience(task_scope, students_only=True) if task_scope else set()
            if receivers - reach:
                with db.write() as conn:  # undo the addition: the course keeps reaching everyone it did
                    conn.execute("DELETE FROM course_items WHERE course_id = ? AND task_id = ?", (course_id, task_id))
                raise TaskError(
                    "A published task in this course does not reach all of these students. Widen who receives it first."
                )

    def add_new_task(self, course_id: str, kind: str, title: str) -> dict[str, Any]:
        """Create a task and put it at the end of the course in one step (§2)."""
        with db.read() as conn:
            self._owned(conn, course_id)
            items = self._items(conn, course_id)
        task = self.tasks.create(kind, title)
        self.update(course_id, task_ids=[*items, task["id"]])
        return self.get(course_id)

    def add_practice_set(self, course_id: str, set_id: str) -> dict[str, Any]:
        """One's published practice set as the course's next task (follow-ups §3)."""
        from deeptutor.practice_sets import store as practice_store

        with db.read() as conn:
            self._owned(conn, course_id)
            items = self._items(conn, course_id)
        with practice_store.read() as conn:
            row = conn.execute(
                "SELECT title, owner_user_id, status FROM practice_sets WHERE id = ?", (set_id,)
            ).fetchone() if conn else None
        if row is None or row["owner_user_id"] != self.actor.id or row["status"] != "published":
            raise TaskError("Choose a practice set you published.")
        task = self.tasks.create("practice_set", row["title"])
        self.tasks.update(task["id"], target_id=set_id)
        self.update(course_id, task_ids=[*items, task["id"]])
        return self.get(course_id)

    def _publish_drafts(self, course_id: str) -> None:
        from deeptutor.publishing import scope as scopes

        scope = scopes.get_scope(scopes.COURSE, course_id)
        with db.read() as conn:
            items = self._items(conn, course_id)
            drafts = [r[0] for r in conn.execute(
                f"SELECT id FROM tasks WHERE status = 'draft' AND id IN ({','.join('?' for _ in items)})",  # nosec B608
                items,
            )] if items else []
        done: list[str] = []
        # A course for the market hands its tasks to its members (platform batch 1 §4.3).
        market = scopes.join_only_scope(scope)
        try:
            for task_id in drafts:
                with scopes.join_only(market), scopes.chosen_scope(scopes.Scope(
                    everyone=scope.everyone, users=list(scope.users), classes=list(scope.classes),
                    groups=list(scope.groups),
                ) if scope else None):
                    self.tasks.publish(task_id, quiet=True)
                done.append(task_id)
        except Exception:
            # All or nothing: tasks published before the failure go back to drafts,
            # so nobody receives part of a course without being told.
            if done:
                with db.write() as conn:
                    conn.executemany(
                        "UPDATE tasks SET status = 'draft', published_at = NULL WHERE id = ?", [(t,) for t in done]
                    )
                for task_id in done:
                    scopes.forget(scopes.TASK, task_id)
            raise

    def publish(self, course_id: str) -> dict[str, Any]:
        """Settle receivers, check published tasks reach them, publish drafts, then the course."""
        from deeptutor.publishing import scope as scopes

        self.tasks._require_author()
        with db.read() as conn:
            row = self._owned(conn, course_id)
            items = self._items(conn, course_id)
            published = {
                r["id"]: r["title"] for r in conn.execute(
                    f"SELECT id, title FROM tasks WHERE status = 'published' AND id IN ({','.join('?' for _ in items)})",  # nosec B608
                    items,
                )
            } if items else {}
        if row["status"] != "draft":
            raise TaskConflict("This course was already published.")
        if not items:
            raise TaskError("Add at least one task before publishing.")
        had_scope = scopes.get_scope(scopes.COURSE, course_id) is not None
        scopes.default_scope_on_publish(scopes.COURSE, course_id)
        scope = scopes.get_scope(scopes.COURSE, course_id)
        try:
            if scope is None or (scope.empty and not (scopes.is_join_only() or scopes.classes_retired())):
                raise scopes.ScopeRequired()
            receivers = scopes.audience(scope, students_only=True, owner_id=self.actor.id)
            for task_id in published:
                task_scope = scopes.get_scope(scopes.TASK, task_id)
                reach = scopes.audience(task_scope, students_only=True) if task_scope else set()
                if receivers - reach:
                    raise TaskError("A published task in this course does not reach all of these students. Widen who receives it first.")
            self._publish_drafts(course_id)
        except Exception:
            if not had_scope:
                scopes.forget(scopes.COURSE, course_id)
            raise
        now = time.time()
        with db.write() as conn:
            conn.execute(
                "UPDATE courses SET status = 'published', published_at = ?, updated_at = ? WHERE id = ?",
                (now, now, course_id),
            )
        from deeptutor.notifications.service import notify

        notify(receivers, "course_published", {"title": row["title"], "teacher": self.actor.username},
               link=f"/tasks?course={course_id}", dedupe_key=f"course_published:{course_id}")
        return self.get(course_id)

    def archive(self, course_id: str) -> dict[str, Any]:
        """Hide the course; its tasks stay as they are (§5.1). Administrators may withdraw any (D10)."""
        self.tasks._require_author()
        with db.write() as conn:
            row = conn.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()
            if row is None or not (
                row["owner_user_id"] == self.actor.id
                or (_role(self.actor) == "admin" and row["status"] == "published")
            ):
                raise TaskNotFound("Course not found.")
            conn.execute("UPDATE courses SET status = 'archived', updated_at = ? WHERE id = ?", (time.time(), course_id))
        from deeptutor.catalog.listings import content_closed

        content_closed("course", course_id)  # platform batch 1 §2
        return {"id": course_id, "status": "archived"}

    def delete(self, course_id: str) -> None:
        self.tasks._require_author()
        with db.write() as conn:
            row = self._owned(conn, course_id)
            if row["status"] != "draft":
                raise TaskConflict("Published courses cannot be deleted; withdraw them instead.")
            conn.execute("DELETE FROM courses WHERE id = ?", (course_id,))
        from deeptutor.publishing.scope import COURSE, forget

        forget(COURSE, course_id)


# ── the student's side ─────────────────────────────────────


def assigned_courses(actor: CurrentUser | None = None) -> list[dict[str, Any]]:
    """Published courses that reach the student, each with its tasks as the student sees them."""
    from deeptutor.publishing import scope as scopes
    from deeptutor.publishing.provenance import provenance

    actor = actor or get_current_user()
    student = _role(actor) == "student"
    with db.read() as conn:
        if conn is None:
            return []
        try:
            rows = conn.execute(
                "SELECT * FROM courses WHERE status = 'published' ORDER BY published_at DESC"
            ).fetchall()
        except sqlite3.OperationalError:
            return []  # a task store from before courses
        items = {
            r["id"]: [t[0] for t in conn.execute(
                "SELECT task_id FROM course_items WHERE course_id = ? ORDER BY position", (r["id"],)
            )]
            for r in rows
        }
    classes = scopes.member_class_ids(actor.id) if student else set()
    # Students, and anyone who joined (platform batch 2 §6).
    reached = [
        r for r in rows if (s := scopes.get_scope(scopes.COURSE, r["id"])) and r["owner_user_id"] != actor.id
        and ((student and scopes._reaches(s, actor.id, classes)) or actor.id in s.members)
    ]
    if not reached:
        return []
    entries = {e["key"]: e for e in StudentTasks(actor).assigned()}
    return [
        {
            "id": r["id"], "title": r["title"], "description": r["description"],
            "provenance": provenance(r["owner_user_id"], actor), "published_at": r["published_at"],
            "tasks": [entries[t] for t in items[r["id"]] if t in entries],
        }
        for r in reached
    ]


def transfer_courses(user_id: str, successor_id: str, *, published_only: bool = False) -> int:
    """A departing teacher's courses go with their tasks (batch 2 §8); a teacher who
    becomes a student keeps their drafts, like their draft tasks."""
    if not db.path().exists():
        return 0
    with db.write() as conn:
        return int(conn.execute(
            "UPDATE courses SET owner_user_id = ?, updated_at = ? WHERE owner_user_id = ?"
            + (" AND status != 'draft'" if published_only else ""),
            (successor_id, time.time(), user_id),
        ).rowcount)


__all__ = ["CourseService", "assigned_courses", "transfer_courses"]
