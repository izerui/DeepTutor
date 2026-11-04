"""Ratings, reviews and favorites (platform follow-ups §1).

People who joined an item — by themselves, by the migration or through a
course — rate it 1 to 5 with an optional text, one review each, which they
change or delete. Its author answers each review once (and may change the
answer); administrators delete any review. Anyone signed in keeps favorites
of listed items; a favorite is not joining.
"""

from __future__ import annotations

import time
from typing import Any

from deeptutor.catalog import items
from deeptutor.catalog.listings import ListingError, ListingForbidden, ListingNotFound, author_name, status
from deeptutor.catalog.store import db
from deeptutor.multi_user.context import get_current_user
from deeptutor.multi_user.models import CurrentUser

TEXT_MAX = 1000
REPLY_MAX = 1000
PAGE = 50


def _clean(text: str, maximum: int) -> str:
    text = str(text or "").strip()
    if len(text) > maximum:
        raise ListingError(f"Keep it under {maximum} characters.")
    return text


def ratings(keys: list[tuple[str, str]] | None = None) -> dict[tuple[str, str], tuple[float, int]]:
    """(average stars, number of reviews) per item."""
    with db.read() as conn:
        if conn is None:
            return {}
        try:
            rows = conn.execute(
                "SELECT kind, item_id, AVG(stars), COUNT(*) FROM reviews GROUP BY kind, item_id"
            ).fetchall()
        except Exception:
            return {}  # a store from before reviews
    found = {(r[0], r[1]): (round(float(r[2]), 2), int(r[3])) for r in rows}
    if keys is None:
        return found
    return {key: found[key] for key in keys if key in found}


def favorites_of(user_id: str) -> set[tuple[str, str]]:
    with db.read() as conn:
        if conn is None:
            return set()
        try:
            return {(r[0], r[1]) for r in conn.execute(
                "SELECT kind, item_id FROM favorites WHERE user_id = ?", (user_id,)
            )}
        except Exception:
            return set()


class ReviewService:
    def __init__(self, actor: CurrentUser | None = None) -> None:
        self.actor = actor or get_current_user()

    def _role(self) -> str:
        from deeptutor.classes.service import _role

        return _role(self.actor)

    def _item(self, kind: str, item_id: str) -> items.ItemInfo:
        if kind not in items.KINDS:
            raise ListingNotFound("Not found.")
        info = items.info(kind, item_id)
        if info is None:
            raise ListingNotFound("Not found.")
        return info

    def _view(self, row, owner: str) -> dict[str, Any]:
        from deeptutor.multi_user.ownership import owns

        mine = row["user_id"] == self.actor.id
        return {
            "user_id": row["user_id"], "author": author_name(row["user_id"]), "stars": row["stars"],
            "text": row["text"], "reply": row["reply"], "reply_at": row["reply_at"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "mine": mine, "can_reply": owns(owner, self.actor),
            "can_delete": mine or self._role() == "admin",
        }

    def list_for(self, kind: str, item_id: str, page: int = 1) -> dict[str, Any]:
        from deeptutor.catalog.enrollment import joined_by
        from deeptutor.multi_user.ownership import owns

        info = self._item(kind, item_id)
        joined = (kind, item_id) in joined_by(self.actor.id)
        if status(kind, item_id) != "listed" and not (joined or owns(info.owner, self.actor) or self._role() == "admin"):
            raise ListingNotFound("Not found.")  # review L1: nothing about items one cannot see
        with db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM reviews WHERE kind = ? AND item_id = ? ORDER BY updated_at DESC",
                (kind, item_id),
            ).fetchall() if conn else []
        average = round(sum(r["stars"] for r in rows) / len(rows), 2) if rows else None
        start = (max(1, page) - 1) * PAGE
        return {
            "average": average,
            "count": len(rows),
            "reviews": [self._view(r, info.owner) for r in rows[start:start + PAGE]],
            "mine": next((self._view(r, info.owner) for r in rows if r["user_id"] == self.actor.id), None),
            "can_review": joined and not owns(info.owner, self.actor),
        }

    def save(self, kind: str, item_id: str, stars: int, text: str = "") -> dict[str, Any]:
        """Write or change one's review: only someone who joined, never its author."""
        from deeptutor.catalog.enrollment import joined_by
        from deeptutor.multi_user.ownership import owns

        info = self._item(kind, item_id)
        if owns(info.owner, self.actor):
            raise ListingForbidden("You cannot review your own.")
        if (kind, item_id) not in joined_by(self.actor.id):
            raise ListingForbidden("Join it before reviewing it.")
        if isinstance(stars, bool) or not isinstance(stars, int) or not 1 <= stars <= 5:
            raise ListingError("Give 1 to 5 stars.")
        text = _clean(text, TEXT_MAX)
        now = time.time()
        with db.write() as conn:
            new = conn.execute(
                "SELECT 1 FROM reviews WHERE kind = ? AND item_id = ? AND user_id = ?", (kind, item_id, self.actor.id)
            ).fetchone() is None
            conn.execute(
                "INSERT INTO reviews (kind, item_id, user_id, stars, text, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(kind, item_id, user_id) DO UPDATE SET "
                "stars = excluded.stars, text = excluded.text, updated_at = excluded.updated_at",
                (kind, item_id, self.actor.id, stars, text, now, now),
            )
        if new:
            from deeptutor.notifications.service import notify

            notify(
                [info.owner], "listing_reviewed", {"content": kind, "title": info.title},
                link=f"/market/item?kind={kind}&id={item_id}",
                dedupe_key=f"listing_reviewed:{kind}:{item_id}:{time.strftime('%Y-%m-%d')}", bump=True,
            )
        return self.list_for(kind, item_id)

    def delete(self, kind: str, item_id: str, user_id: str | None = None) -> dict[str, Any]:
        """One's own review, or (administrators) anyone's."""
        target = user_id or self.actor.id
        if target != self.actor.id and self._role() != "admin":
            raise ListingForbidden("Only administrators delete other people's reviews.")
        self._item(kind, item_id)
        with db.write() as conn:
            conn.execute(
                "DELETE FROM reviews WHERE kind = ? AND item_id = ? AND user_id = ?", (kind, item_id, target)
            )
        return self.list_for(kind, item_id)

    def reply(self, kind: str, item_id: str, user_id: str, text: str) -> dict[str, Any]:
        """The author answers a review once; answering again replaces it, empty removes it."""
        from deeptutor.multi_user.ownership import owns

        info = self._item(kind, item_id)
        if not owns(info.owner, self.actor):
            raise ListingForbidden("Only its author answers reviews.")
        text = _clean(text, REPLY_MAX)
        with db.write() as conn:
            row = conn.execute(
                "SELECT 1 FROM reviews WHERE kind = ? AND item_id = ? AND user_id = ?", (kind, item_id, user_id)
            ).fetchone()
            if row is None:
                raise ListingNotFound("Review not found.")
            conn.execute(
                "UPDATE reviews SET reply = ?, reply_at = ? WHERE kind = ? AND item_id = ? AND user_id = ?",
                (text, time.time() if text else None, kind, item_id, user_id),
            )
        return self.list_for(kind, item_id)

    # ── favorites ─────────────────────────────────────────

    def favorite(self, kind: str, item_id: str, on: bool) -> dict[str, Any]:
        self._item(kind, item_id)
        if on and status(kind, item_id) != "listed":
            raise ListingNotFound("This is not in the market.")
        with db.write() as conn:
            if on:
                conn.execute(
                    "INSERT INTO favorites (kind, item_id, user_id, created_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(kind, item_id, user_id) DO NOTHING",
                    (kind, item_id, self.actor.id, time.time()),
                )
            else:
                conn.execute(
                    "DELETE FROM favorites WHERE kind = ? AND item_id = ? AND user_id = ?",
                    (kind, item_id, self.actor.id),
                )
        return {"kind": kind, "id": item_id, "favorited": on}


def forget_user(user_id: str) -> None:
    if db.path().exists():
        with db.write() as conn:
            conn.execute("DELETE FROM reviews WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM favorites WHERE user_id = ?", (user_id,))


def forget_item(kind: str, item_id: str) -> None:
    if db.path().exists():
        with db.write() as conn:
            conn.execute("DELETE FROM reviews WHERE kind = ? AND item_id = ?", (kind, item_id))
            conn.execute("DELETE FROM favorites WHERE kind = ? AND item_id = ?", (kind, item_id))


__all__ = ["ReviewService", "favorites_of", "forget_item", "forget_user", "ratings"]
