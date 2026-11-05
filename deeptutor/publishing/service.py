"""Who receives an item, as its publisher sees it (spec 2026-10-29 §7, learning platform).

Only the publisher reads or changes the receivers of their own item (D10:
administrators withdraw or delete other people's content, they do not
republish it). People reach content by joining it from the market; nobody is
named any more (spec 2026-11-05 remove-teacher-management §1.2). Accounts an
older item named, and "everyone" from before the market, are kept as stored;
the publisher may only stop reaching everyone. Narrowing that cuts anyone
off needs ``confirm`` (§7.2).
"""

from __future__ import annotations

from typing import Any

from deeptutor.multi_user.context import get_current_user
from deeptutor.multi_user.models import CurrentUser
from deeptutor.publishing import scope as scopes
from deeptutor.publishing.scope import (
    BOOK,
    KINDS,
    KNOWLEDGE_BASE,
    MASTERY_PATH,
    PRACTICE_SET,
    STUDENT_KINDS,
    TASK,
    COURSE,
    Scope,
    ScopeError,
    ScopeForbidden,
    ScopeNeedsConfirmation,
    ScopeNotFound,
)


class PublishService:
    def __init__(self, actor: CurrentUser | None = None) -> None:
        self.actor = actor or get_current_user()

    # ── the item ──────────────────────────────────────────────

    def _item(self, kind: str, item_id: str) -> dict[str, Any]:
        """Owner and visibility of an item the actor publishes; 404 otherwise."""
        from deeptutor.multi_user.ownership import owns

        if kind not in KINDS:
            raise ScopeNotFound("Unknown kind of content.")
        if kind == BOOK:
            from deeptutor.book import registry

            record = registry.get_book(item_id)
            found = record is not None and owns(record.created_by, self.actor)
            info = {"owner": record.created_by, "public": record.visibility == "public"} if found else {}
        elif kind == KNOWLEDGE_BASE:
            from deeptutor.knowledge import registry

            record = registry.get_kb(item_id)
            found = record is not None and owns(record.created_by, self.actor)
            info = {"owner": record.created_by, "public": record.visibility == "public"} if found else {}
        elif kind == COURSE:
            from deeptutor.tasks.store import db as task_db

            with task_db.read() as conn:
                row = conn.execute(
                    "SELECT owner_user_id, status FROM courses WHERE id = ?", (item_id,)
                ).fetchone() if conn else None
            found = row is not None and row["owner_user_id"] == self.actor.id
            info = {"owner": row["owner_user_id"], "public": row["status"] == "published"} if found else {}
        elif kind == TASK:
            from deeptutor.tasks.store import db as task_db

            with task_db.read() as conn:
                row = conn.execute(
                    "SELECT owner_user_id, status FROM tasks WHERE id = ?", (item_id,)
                ).fetchone() if conn else None
            found = row is not None and row["owner_user_id"] == self.actor.id
            info = {"owner": row["owner_user_id"], "public": row["status"] == "published"} if found else {}
        elif kind == PRACTICE_SET:
            from deeptutor.practice_sets import store as practice_store

            with practice_store.read() as conn:
                row = conn.execute(
                    "SELECT owner_user_id, status FROM practice_sets WHERE id = ?", (item_id,)
                ).fetchone() if conn else None
            found = row is not None and row["owner_user_id"] == self.actor.id
            info = {"owner": row["owner_user_id"], "public": row["status"] == "published"} if found else {}
        else:
            from deeptutor.teaching.store import TeachingStore

            template = TeachingStore().get_template(item_id)
            found = template is not None and template.owner_user_id == self.actor.id
            info = {"owner": template.owner_user_id, "public": template.status == "published"} if found else {}
        if not found:
            raise ScopeNotFound("Only the publisher can choose who receives this.")
        return info

    def _role(self) -> str:
        from deeptutor.multi_user.accounts import live_role as _role

        return _role(self.actor)

    def _require_publisher(self) -> None:
        if self._role() not in ("teacher", "admin"):
            raise ScopeForbidden("Only teachers and administrators publish content.")

    def _current(self, kind: str, item_id: str, info: dict[str, Any]) -> Scope:
        # What is stored, with its members — never the empty scope a taken-down item
        # reads as, which saving would write back over the stored one (review M3).
        stored = scopes._stored_scope(kind, item_id)
        if stored is not None:
            from deeptutor.catalog.enrollment import members

            stored.members = sorted(members(kind, item_id))
            return stored
        if kind == MASTERY_PATH:
            return scopes._template_scope(item_id)
        if kind in (PRACTICE_SET, TASK, COURSE):
            return Scope(managed=False)  # reaches nobody until its scope is set
        return Scope(everyone=bool(info["public"]), managed=False)

    def _reached(self, kind: str, scope: Scope, info: dict[str, Any]) -> set[str]:
        if not info["public"]:
            return set()
        return scopes.audience(scope, students_only=kind in STUDENT_KINDS, owner_id=info["owner"])

    # ── API ───────────────────────────────────────────────────

    def describe(self, kind: str, item_id: str) -> dict[str, Any]:
        self._require_publisher()
        info = self._item(kind, item_id)
        scope = self._current(kind, item_id, info)
        from deeptutor.multi_user.accounts import accounts as _accounts

        accounts = _accounts()
        return {
            "kind": kind,
            "item_id": item_id,
            "published": info["public"],
            "scope": {
                **scope.to_dict(),
                "users": [
                    {"id": u, "username": (accounts.get(u) or {}).get("username") or u}
                    for u in scope.users
                ],
            },
            "audience_size": len(self._reached(kind, scope, info)),
        }

    def set_scope(self, kind: str, item_id: str, *, everyone: bool, confirm: bool = False) -> dict[str, Any]:
        """Keep or drop the old "everyone"; the accounts an older item named stay as stored."""
        self._require_publisher()
        info = self._item(kind, item_id)
        if kind == MASTERY_PATH and not info["public"]:
            raise ScopeError("Publish this path before choosing who receives it.")
        from deeptutor.catalog.enrollment import access_state

        if access_state(kind, item_id)[0]:
            raise ScopeError("An administrator took this down; it reaches nobody until approved again.")
        current = self._current(kind, item_id, info)
        if everyone and not current.everyone:
            raise ScopeError("To reach everyone, put it in the learning market.")
        new = Scope(everyone=bool(everyone), users=list(current.users))
        new.members = list(current.members)  # people who joined stay whatever the publisher picks
        before = self._reached(kind, current, info)
        cut = before - self._reached(kind, new, info)
        if cut and not confirm:
            raise ScopeNeedsConfirmation(len(cut))
        scopes._store_scope(kind, item_id, new, actor_id=self.actor.id)
        if kind == MASTERY_PATH:
            scopes.reconcile_template(item_id)
        return self.describe(kind, item_id)


__all__ = ["PublishService", "ScopeError", "ScopeNeedsConfirmation"]
