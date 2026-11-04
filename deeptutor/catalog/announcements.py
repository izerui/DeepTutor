"""Announcements to the people who joined a course or a Mastery Path (platform batch 3 §3).

Its author posts, edits and pins; an administrator may only delete. Members
read them on the course, the path's introduction page and their home page,
and hear of each new one. Someone who left, or an item taken down, shows
nothing.
"""

from __future__ import annotations

import time
from typing import Any
import uuid

from deeptutor.catalog import enrollment, items
from deeptutor.catalog.listings import ListingError, ListingForbidden, ListingNotFound, author_name
from deeptutor.catalog.store import db
from deeptutor.multi_user.context import get_current_user
from deeptutor.multi_user.models import CurrentUser
from deeptutor.publishing import scope as scopes

KINDS = (scopes.COURSE, scopes.MASTERY_PATH)
TEXT_MAX = 5000
HOME_LIMIT = 3


def _clean(text: str) -> str:
    text = str(text or "").strip()
    if not text or len(text) > TEXT_MAX:
        raise ListingError(f"An announcement has 1 to {TEXT_MAX} characters.")
    return text


def _link(kind: str, item_id: str) -> str:
    return f"/tasks?course={item_id}" if kind == scopes.COURSE else f"/market/item?kind={kind}&id={item_id}"


class AnnouncementService:
    def __init__(self, actor: CurrentUser | None = None) -> None:
        self.actor = actor or get_current_user()

    def _role(self) -> str:
        from deeptutor.classes.service import _role

        return _role(self.actor)

    def _item(self, kind: str, item_id: str) -> items.ItemInfo:
        if kind not in KINDS:
            raise ListingNotFound("Not found.")
        info = items.info(kind, item_id)
        if info is None:
            raise ListingNotFound("Not found.")
        return info

    def _authors(self, info: items.ItemInfo) -> bool:
        from deeptutor.multi_user.ownership import owns

        return owns(info.owner, self.actor) and self._role() in ("teacher", "admin")

    def _view(self, row, info: items.ItemInfo | None = None) -> dict[str, Any]:
        from deeptutor.multi_user.ownership import owns

        # Its writer, or whoever owns the item now (a handed-over course keeps its announcements editable).
        mine = row["author_id"] == self.actor.id or (info is not None and owns(info.owner, self.actor))
        return {
            "id": row["id"], "kind": row["kind"], "item_id": row["item_id"],
            "item_title": info.title if info else "",
            "author": author_name(row["author_id"]), "text": row["text"], "pinned": bool(row["pinned"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "can_edit": mine, "can_delete": mine or self._role() == "admin",
        }

    def post(self, kind: str, item_id: str, text: str, pinned: bool = False) -> dict[str, Any]:
        info = self._item(kind, item_id)
        if not self._authors(info):
            raise ListingForbidden("Only its author posts announcements.")
        now = time.time()
        announcement_id = f"ann_{uuid.uuid4().hex[:12]}"
        with db.write() as conn:
            conn.execute(
                "INSERT INTO content_announcements VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (announcement_id, kind, item_id, self.actor.id, _clean(text), int(bool(pinned)), now, now),
            )
            row = conn.execute("SELECT * FROM content_announcements WHERE id = ?", (announcement_id,)).fetchone()
        from deeptutor.notifications.service import notify

        notify(
            enrollment.members(kind, item_id) - {self.actor.id}, "content_announcement",
            {"content": kind, "title": info.title, "teacher": self.actor.username},
            link=_link(kind, item_id), dedupe_key=f"content_announcement:{announcement_id}",
        )
        return self._view(row, info)

    def list_for(self, kind: str, item_id: str) -> list[dict[str, Any]]:
        """Its author and administrators always; members while it is not taken down."""
        info = self._item(kind, item_id)
        if not (self._authors(info) or self._role() == "admin"):
            blocked, members = enrollment.access_state(kind, item_id)
            if blocked or self.actor.id not in members:
                return []
        with db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM content_announcements WHERE kind = ? AND item_id = ? "
                "ORDER BY pinned DESC, created_at DESC",
                (kind, item_id),
            ).fetchall() if conn else []
        return [self._view(row, info) for row in rows]

    def _may_edit(self, row) -> bool:
        if row["author_id"] == self.actor.id:
            return True
        from deeptutor.multi_user.ownership import owns

        info = items.info(row["kind"], row["item_id"])
        return info is not None and owns(info.owner, self.actor)

    def _own(self, conn, announcement_id: str):
        row = conn.execute("SELECT * FROM content_announcements WHERE id = ?", (announcement_id,)).fetchone()
        if row is None:
            raise ListingNotFound("Announcement not found.")
        return row

    def update(self, announcement_id: str, *, text: str | None = None, pinned: bool | None = None) -> dict[str, Any]:
        with db.write() as conn:
            row = self._own(conn, announcement_id)
            if not self._may_edit(row):
                raise ListingForbidden("Only its author edits an announcement.")
            conn.execute(
                "UPDATE content_announcements SET text = ?, pinned = ?, updated_at = ? WHERE id = ?",
                (_clean(text) if text is not None else row["text"],
                 int(bool(pinned)) if pinned is not None else row["pinned"], time.time(), announcement_id),
            )
            row = self._own(conn, announcement_id)
        return self._view(row, items.info(row["kind"], row["item_id"]))

    def delete(self, announcement_id: str) -> None:
        with db.write() as conn:
            row = self._own(conn, announcement_id)
            if not self._may_edit(row) and self._role() != "admin":
                raise ListingForbidden("Only its author or an administrator deletes an announcement.")
            conn.execute("DELETE FROM content_announcements WHERE id = ?", (announcement_id,))

    def mine(self, limit: int = HOME_LIMIT) -> list[dict[str, Any]]:
        """The newest announcements of what the user joined, for the home page."""
        joined = [key for key in enrollment.joined_by(self.actor.id) if key[0] in KINDS]
        if not joined:
            return []
        # Only the announcements of what one joined, newest first (review M5).
        clause = " OR ".join("(kind = ? AND item_id = ?)" for _ in joined[:200])
        with db.read() as conn:
            rows = conn.execute(
                f"SELECT * FROM content_announcements WHERE {clause} ORDER BY created_at DESC LIMIT 50",  # nosec B608
                [value for key in joined[:200] for value in key],
            ).fetchall() if conn else []
        wanted = set(joined)
        out = []
        for row in rows:
            key = (row["kind"], row["item_id"])
            if key not in wanted or enrollment.access_state(*key)[0]:
                continue
            out.append(self._view(row, items.info(*key)))
            if len(out) >= limit:
                break
        return out


def forget_item(kind: str, item_id: str) -> None:
    if db.path().exists():
        with db.write() as conn:
            conn.execute("DELETE FROM content_announcements WHERE kind = ? AND item_id = ?", (kind, item_id))


__all__ = ["AnnouncementService", "HOME_LIMIT", "KINDS", "TEXT_MAX", "forget_item"]
