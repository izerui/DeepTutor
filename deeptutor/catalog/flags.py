"""Reports of content and of reviews (audit fix B §1).

Anyone signed in reports an item they can see in the market (or joined), or a
review of it, with a reason and a few words. One report per person and
target: reporting again while the first is open changes nothing. Nothing is
taken down automatically: an administrator looks at the reports, gathered per
target, and takes the item down, deletes the review, or dismisses them. The
people who reported hear what was decided.

Called "flags" here: ``catalog.reports`` is the learning reports of authors.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from deeptutor.catalog import items
from deeptutor.catalog.listings import (
    ListingConflict,
    ListingError,
    ListingForbidden,
    ListingNotFound,
    author_name,
    status,
)
from deeptutor.catalog.store import db
from deeptutor.multi_user.context import get_current_user
from deeptutor.multi_user.models import CurrentUser

logger = logging.getLogger(__name__)

REASONS = ("wrong", "infringement", "advertising", "inappropriate", "other")
NOTE_MAX = 500
OPEN = "open"
TAKEN_DOWN = "taken_down"
REVIEW_DELETED = "review_deleted"
DISMISSED = "dismissed"


def _role(user: CurrentUser) -> str:
    from deeptutor.multi_user.accounts import live_role

    return live_role(user)


def _admin_ids() -> list[str]:
    from deeptutor.multi_user.accounts import accounts as _accounts

    return [uid for uid, row in _accounts().items() if row["role"] == "admin" and not row["disabled"]]


def reported_by(user_id: str, kind: str, item_id: str) -> set[str]:
    """The targets of this item the user has an open report on: '' (the item) and reviewers' ids."""
    with db.read() as conn:
        if conn is None:
            return set()
        try:
            return {
                r[0] for r in conn.execute(
                    "SELECT review_user_id FROM content_reports WHERE kind = ? AND item_id = ? "
                    "AND reporter_id = ? AND outcome = 'open'",
                    (kind, item_id, user_id),
                )
            }
        except Exception:
            return set()  # a store from before reports


def _notify_reporters(rows, outcome: str, title: str) -> None:
    """Each reporter hears once what was decided (a report opened again is a new decision)."""
    from deeptutor.notifications.service import notify

    try:
        for row in rows:
            target = "review" if row["review_user_id"] else "item"
            notify(
                [row["reporter_id"]], "report_resolved",
                {"content": row["kind"], "title": title, "outcome": outcome, "target": target},
                link=f"/market/item?kind={row['kind']}&id={row['item_id']}",
                dedupe_key=f"report_resolved:{row['id']}:{row['created_at']!r}",
            )
    except Exception:
        # Best effort: the decision itself stands.
        logger.warning("Could not tell the reporters of %s %s", rows[0]["kind"], rows[0]["item_id"], exc_info=True)


def resolve(kind: str, item_id: str, review_user_id: str, outcome: str, actor_id: str, *, title: str = "") -> int:
    """Close the open reports of one target with *outcome* and tell their reporters; how many closed."""
    if not db.path().exists():
        return 0
    now = time.time()
    with db.write() as conn:
        rows = conn.execute(
            "SELECT * FROM content_reports WHERE kind = ? AND item_id = ? AND review_user_id = ? AND outcome = 'open'",
            (kind, item_id, review_user_id),
        ).fetchall()
        conn.execute(
            "UPDATE content_reports SET outcome = ?, handled_by = ?, handled_at = ? "
            "WHERE kind = ? AND item_id = ? AND review_user_id = ? AND outcome = 'open'",
            (outcome, actor_id, now, kind, item_id, review_user_id),
        )
    if rows:
        if not title:
            info = items.info(kind, item_id)
            title = info.title if info else ""
        _notify_reporters(rows, outcome, title)
    return len(rows)


class FlagService:
    def __init__(self, actor: CurrentUser | None = None) -> None:
        self.actor = actor or get_current_user()

    def _require_admin(self) -> None:
        if _role(self.actor) != "admin":
            raise ListingForbidden("Only administrators handle reports.")

    def _visible_item(self, kind: str, item_id: str) -> items.ItemInfo:
        """An item the actor may see in the market: listed, or one they joined."""
        from deeptutor.catalog.enrollment import joined_by

        if kind not in items.KINDS:
            raise ListingNotFound("Not found.")
        info = items.info(kind, item_id)
        if info is None:
            raise ListingNotFound("Not found.")
        if status(kind, item_id) != "listed" and (kind, item_id) not in joined_by(self.actor.id):
            raise ListingNotFound("Not found.")
        return info

    # ── reporting ─────────────────────────────────────────

    def report(self, kind: str, item_id: str, reason: str, note: str = "", review_user_id: str = "") -> dict[str, Any]:
        """Report an item (``review_user_id`` empty) or one review of it."""
        from deeptutor.multi_user.ownership import owns

        info = self._visible_item(kind, item_id)
        review_user_id = str(review_user_id or "")
        if reason not in REASONS:
            raise ListingError("Choose a reason.")
        note = str(note or "").strip()
        if len(note) > NOTE_MAX:
            raise ListingError(f"Keep it under {NOTE_MAX} characters.")
        if reason == "other" and not note:
            raise ListingError("Say what is wrong.")
        if review_user_id:
            if review_user_id == self.actor.id:
                raise ListingForbidden("You cannot report your own review.")
            with db.read() as conn:
                found = conn.execute(
                    "SELECT 1 FROM reviews WHERE kind = ? AND item_id = ? AND user_id = ?",
                    (kind, item_id, review_user_id),
                ).fetchone() if conn else None
            if found is None:
                raise ListingNotFound("Review not found.")
        elif owns(info.owner, self.actor):
            raise ListingForbidden("You cannot report your own.")
        now = time.time()
        with db.write() as conn:
            row = conn.execute(
                "SELECT outcome FROM content_reports WHERE kind = ? AND item_id = ? AND review_user_id = ? "
                "AND reporter_id = ?",
                (kind, item_id, review_user_id, self.actor.id),
            ).fetchone()
            if row is not None and row["outcome"] == OPEN:
                raise ListingConflict("You already reported this.")
            # A report an administrator already decided on is opened again: it counts once.
            conn.execute(
                "INSERT INTO content_reports (kind, item_id, review_user_id, reporter_id, reason, note, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(kind, item_id, review_user_id, reporter_id) DO UPDATE SET "
                "reason = excluded.reason, note = excluded.note, created_at = excluded.created_at, "
                "outcome = 'open', handled_by = '', handled_at = NULL",
                (kind, item_id, review_user_id, self.actor.id, reason, note, now),
            )
        from deeptutor.notifications.service import notify

        notify(
            _admin_ids(), "report_submitted", {"content": kind, "title": info.title},
            link="/admin/review?tab=reports",
            dedupe_key=f"report_submitted:{kind}:{item_id}:{time.strftime('%Y-%m-%d')}", bump=True,
        )
        return {"kind": kind, "id": item_id, "review_user_id": review_user_id, "reported": True}

    # ── administrators ────────────────────────────────────

    def queue(self) -> list[dict[str, Any]]:
        """Open reports gathered per target, the most reported first."""
        self._require_admin()
        from deeptutor.multi_user.accounts import accounts as _accounts

        with db.read() as conn:
            try:
                rows = conn.execute(
                    "SELECT * FROM content_reports WHERE outcome = 'open' ORDER BY created_at"
                ).fetchall() if conn else []
            except Exception:
                rows = []
            groups: dict[tuple[str, str, str], dict[str, Any]] = {}
            accounts = _accounts()
            for row in rows:
                key = (row["kind"], row["item_id"], row["review_user_id"])
                group = groups.get(key)
                if group is None:
                    group = groups[key] = self._target(conn, *key, accounts=accounts)
                group["count"] += 1
                group["reasons"][row["reason"]] = group["reasons"].get(row["reason"], 0) + 1
                group["reports"].append({
                    "reporter": author_name(row["reporter_id"], accounts), "reason": row["reason"],
                    "note": row["note"], "created_at": row["created_at"],
                })
                group["first_at"] = min(group["first_at"] or row["created_at"], row["created_at"])
        return sorted(groups.values(), key=lambda g: (-g["count"], g["first_at"] or 0))

    def _target(self, conn, kind: str, item_id: str, review_user_id: str, *, accounts) -> dict[str, Any]:
        listing = conn.execute(
            "SELECT title, status, owner_user_id FROM listings WHERE kind = ? AND item_id = ?", (kind, item_id)
        ).fetchone()
        info = items.info(kind, item_id) if listing is None else None
        target: dict[str, Any] = {
            "kind": kind, "id": item_id, "review_user_id": review_user_id,
            "target": "review" if review_user_id else "item",
            "title": listing["title"] if listing is not None else (info.title if info else ""),
            "status": listing["status"] if listing is not None else "none",
            "author": author_name(listing["owner_user_id"] if listing is not None else (info.owner if info else ""), accounts),
            "count": 0, "reasons": {}, "reports": [], "first_at": None, "review": None,
        }
        if review_user_id:
            review = conn.execute(
                "SELECT stars, text, updated_at FROM reviews WHERE kind = ? AND item_id = ? AND user_id = ?",
                (kind, item_id, review_user_id),
            ).fetchone()
            target["review"] = {
                "author": author_name(review_user_id, accounts),
                "stars": review["stars"] if review else None,
                "text": review["text"] if review else "",
                "exists": review is not None,
            }
        return target

    def handle(self, kind: str, item_id: str, action: str, *, review_user_id: str = "", reason: str = "") -> dict[str, Any]:
        """Take the item down, delete the review, or dismiss the reports of one target."""
        self._require_admin()
        review_user_id = str(review_user_id or "")
        if action == "take_down":
            if review_user_id:
                raise ListingError("A review is deleted, not taken down.")
            from deeptutor.catalog.listings import ListingService

            if status(kind, item_id) == "taken_down":
                resolve(kind, item_id, "", TAKEN_DOWN, self.actor.id)
            else:
                # Its open reports are closed by the take-down itself, which tells their reporters.
                ListingService(self.actor).take_down(kind, item_id, reason)
        elif action == "delete_review":
            if not review_user_id:
                raise ListingError("Choose the review to delete.")
            from deeptutor.catalog.reviews import ReviewService

            # Its open reports are closed by the deletion itself, which tells their reporters.
            ReviewService(self.actor).delete(kind, item_id, review_user_id)
        elif action == "dismiss":
            if not resolve(kind, item_id, review_user_id, DISMISSED, self.actor.id):
                raise ListingConflict("These reports were already handled.")
        else:
            raise ListingError("Unknown action.")
        return {"kind": kind, "id": item_id, "review_user_id": review_user_id, "handled": action}


def forget_user(user_id: str) -> None:
    if db.path().exists():
        with db.write() as conn:
            conn.execute(
                "DELETE FROM content_reports WHERE reporter_id = ? OR review_user_id = ?", (user_id, user_id)
            )


def forget_item(kind: str, item_id: str) -> None:
    if db.path().exists():
        with db.write() as conn:
            conn.execute("DELETE FROM content_reports WHERE kind = ? AND item_id = ?", (kind, item_id))


__all__ = [
    "DISMISSED", "FlagService", "NOTE_MAX", "OPEN", "REASONS", "REVIEW_DELETED", "TAKEN_DOWN",
    "forget_item", "forget_user", "reported_by", "resolve",
]
