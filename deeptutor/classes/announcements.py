"""Class announcements (spec 2026-11-03 batch 2 §4).

A class's teachers and administrators post to the class; its students read
them on their home page and the class page. The author (while still teaching
the class) and administrators edit and delete; once the author steps down or
is gone, only administrators may delete. An archived class's announcements
are hidden from its students and read-only for its teachers.
"""

from __future__ import annotations

import time
from typing import Any
import uuid

from deeptutor.classes import store
from deeptutor.classes.service import ClassConflict, ClassError, ClassForbidden, ClassNotFound, _accounts, _role
from deeptutor.multi_user.context import get_current_user
from deeptutor.multi_user.models import CurrentUser

TEXT_MAX = 5000
HOME_LIMIT = 3


def _teaches(conn, class_id: str, user_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM class_teachers WHERE class_id = ? AND user_id = ?", (class_id, user_id)
    ).fetchone() is not None


def _manages(conn, class_id: str, actor: CurrentUser) -> bool:
    return actor.is_admin or (_role(actor) == "teacher" and _teaches(conn, class_id, actor.id))


def _clean(text: str) -> str:
    text = str(text or "").strip()
    if not text or len(text) > TEXT_MAX:
        raise ClassError(f"An announcement must contain 1 to {TEXT_MAX} characters.")
    return text


def _view(row, names: dict[str, str], *, can_edit: bool) -> dict[str, Any]:
    return {
        "id": row["id"],
        "class_id": row["class_id"],
        "class_name": row["class_name"] if "class_name" in row.keys() else "",
        "author": names.get(row["author_id"], ""),
        "text": row["text"],
        "pinned": bool(row["pinned"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "can_edit": can_edit,
    }


class AnnouncementService:
    def __init__(self, actor: CurrentUser | None = None) -> None:
        self.actor = actor or get_current_user()

    def post(self, class_id: str, text: str, pinned: bool = False) -> dict[str, Any]:
        text = _clean(text)
        announcement_id = f"ann_{uuid.uuid4().hex[:12]}"
        now = time.time()
        with store.write() as conn:
            row = conn.execute("SELECT name, archived FROM classes WHERE id = ?", (class_id,)).fetchone()
            if row is None:
                raise ClassNotFound("Class not found.")
            if not _manages(conn, class_id, self.actor):
                raise ClassForbidden("Only this class's teachers and administrators post announcements.")
            if row["archived"]:
                raise ClassConflict("This class is archived.")
            conn.execute(
                "INSERT INTO class_announcements VALUES (?, ?, ?, ?, ?, ?, ?)",
                (announcement_id, class_id, self.actor.id, text, int(bool(pinned)), now, now),
            )
            students = [r[0] for r in conn.execute("SELECT student_id FROM class_members WHERE class_id = ?", (class_id,))]
        from deeptutor.notifications.service import notify

        notify(students, "class_announcement", {"class_name": row["name"], "teacher": self.actor.username},
               link="/classes", dedupe_key=f"class_announcement:{announcement_id}")
        return self._one(announcement_id)

    def _one(self, announcement_id: str) -> dict[str, Any]:
        for item in self._visible(announcement_id=announcement_id):
            return item
        raise ClassNotFound("Announcement not found.")

    def _editable(self, conn, row) -> bool:
        archived = conn.execute("SELECT archived FROM classes WHERE id = ?", (row["class_id"],)).fetchone()
        if self.actor.is_admin:
            return True
        return (
            not (archived and archived["archived"])
            and row["author_id"] == self.actor.id
            and _role(self.actor) == "teacher"
            and _teaches(conn, row["class_id"], self.actor.id)
        )

    def update(self, announcement_id: str, *, text: str | None = None, pinned: bool | None = None) -> dict[str, Any]:
        with store.write() as conn:
            row = conn.execute("SELECT * FROM class_announcements WHERE id = ?", (announcement_id,)).fetchone()
            if row is None:
                raise ClassNotFound("Announcement not found.")
            if not self._editable(conn, row):
                raise ClassForbidden("Only its author, while teaching the class, and administrators change it.")
            conn.execute(
                "UPDATE class_announcements SET text = ?, pinned = ?, updated_at = ? WHERE id = ?",
                (_clean(text) if text is not None else row["text"],
                 int(bool(pinned)) if pinned is not None else row["pinned"], time.time(), announcement_id),
            )
        return self._one(announcement_id)

    def delete(self, announcement_id: str) -> None:
        with store.write() as conn:
            row = conn.execute("SELECT * FROM class_announcements WHERE id = ?", (announcement_id,)).fetchone()
            if row is None:
                raise ClassNotFound("Announcement not found.")
            if not self._editable(conn, row):
                raise ClassForbidden("Only its author, while teaching the class, and administrators delete it.")
            conn.execute("DELETE FROM class_announcements WHERE id = ?", (announcement_id,))

    def _visible(self, *, class_id: str | None = None, announcement_id: str | None = None) -> list[dict[str, Any]]:
        """Announcements the actor may read: their active classes as a student, any as a manager."""
        names = {uid: a["username"] for uid, a in _accounts().items()}
        with store.read() as conn:
            if conn is None:
                return []
            try:
                clauses, params = [], []
                if class_id:
                    clauses.append("a.class_id = ?")
                    params.append(class_id)
                if announcement_id:
                    clauses.append("a.id = ?")
                    params.append(announcement_id)
                where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
                rows = conn.execute(
                    "SELECT a.*, c.archived, c.name AS class_name FROM class_announcements a JOIN classes c ON c.id = a.class_id"
                    + where + " ORDER BY a.pinned DESC, a.created_at DESC",  # nosec B608 - fixed clauses
                    params,
                ).fetchall()
            except Exception:
                return []  # an older database without the table
            out = []
            for row in rows:
                managing = _manages(conn, row["class_id"], self.actor)
                student = (
                    not row["archived"] and _role(self.actor) == "student"
                    and conn.execute(
                        "SELECT 1 FROM class_members WHERE class_id = ? AND student_id = ?",
                        (row["class_id"], self.actor.id),
                    ).fetchone() is not None
                )
                if managing or student:
                    out.append(_view(row, names, can_edit=self._editable(conn, row)))
        return out

    def list_for_class(self, class_id: str) -> list[dict[str, Any]]:
        return self._visible(class_id=class_id)

    def for_home(self) -> list[dict[str, Any]]:
        """A student's latest announcements across their active classes (§4)."""
        if _role(self.actor) != "student":
            return []
        return self._visible()[:HOME_LIMIT]


__all__ = ["AnnouncementService"]
