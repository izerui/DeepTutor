"""Who may name the receivers of an item, and whom (spec 2026-10-29 §7, learning platform).

Only the publisher sets the receivers of their own item (D10: administrators
withdraw or delete other people's content, they do not republish it). People
reach content by joining it from the market; a publisher may also name
learners of their own content (a remedial set). Narrowing that cuts anyone
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


def _clean_ids(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    return [v for v in dict.fromkeys(str(x).strip() for x in values) if v]


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

    def _validated(self, kind: str, everyone: bool, users: Any, current: Scope) -> Scope:
        """Check whom the publisher names; people already named may stay.

        A teacher names their own learners (who joined their content); an
        administrator anyone. "Everyone" is reached through the market only.
        """
        from deeptutor.catalog.learners import learners_of
        from deeptutor.multi_user.accounts import accounts as _accounts

        users = _clean_ids(users)
        if everyone and not current.everyone:
            raise ScopeError("To reach everyone, put it in the learning market.")
        accounts = _accounts()
        # Deleted accounts drop out silently; they can no longer receive anything.
        users = [u for u in users if u in accounts and u != self.actor.id]
        added_users = [u for u in users if u not in current.users]
        reachable = None if self._role() == "admin" else learners_of(self.actor.id)
        for user_id in added_users:
            account = accounts[user_id]
            if account["disabled"]:
                raise ScopeError("Only active accounts can receive content.")
            if account["role"] not in ("student", "teacher"):
                raise ScopeError("Only students and teachers can be chosen.")
            if kind in STUDENT_KINDS and account["role"] != "student":
                raise ScopeError(
                    "Only students receive Mastery Paths." if kind == MASTERY_PATH
                    else "Only students receive tasks." if kind in (TASK, COURSE)
                    else "Only students receive practice sets."
                )
            if reachable is not None and account["role"] == "student" and user_id not in reachable:
                raise ScopeForbidden("You can only choose learners of your own content.")
        return Scope(everyone=bool(everyone) and current.everyone, users=users)

    def set_scope(
        self, kind: str, item_id: str, *, everyone: bool, users: Any, confirm: bool = False,
    ) -> dict[str, Any]:
        self._require_publisher()
        info = self._item(kind, item_id)
        if kind == MASTERY_PATH and not info["public"]:
            raise ScopeError("Publish this path before choosing who receives it.")
        from deeptutor.catalog.enrollment import access_state

        if access_state(kind, item_id)[0]:
            raise ScopeError("An administrator took this down; it reaches nobody until approved again.")
        current = self._current(kind, item_id, info)
        new = self._validated(kind, everyone, users, current)
        new.members = list(current.members)  # people who joined stay whatever the publisher picks
        before = self._reached(kind, current, info)
        cut = before - self._reached(kind, new, info)
        if cut and not confirm:
            raise ScopeNeedsConfirmation(len(cut))
        scopes._store_scope(kind, item_id, new, actor_id=self.actor.id)
        if kind == MASTERY_PATH:
            scopes.reconcile_template(item_id)
        return self.describe(kind, item_id)

    def search_people(self, query: str) -> list[dict[str, Any]]:
        """A teacher's learners (an administrator: anyone) by username, for naming receivers."""
        from deeptutor.catalog.learners import learners_of
        from deeptutor.multi_user.accounts import accounts as _accounts

        self._require_publisher()
        needle = str(query or "").strip().casefold()
        if not needle:
            return []
        reachable = None if self._role() == "admin" else learners_of(self.actor.id)
        return [
            {"id": user_id, "username": account["username"], "role": account["role"]}
            for user_id, account in sorted(_accounts().items(), key=lambda item: item[1]["username"])
            if account["role"] in ("student", "teacher") and not account["disabled"]
            and user_id != self.actor.id and needle in account["username"].casefold()
            and (reachable is None or user_id in reachable)
        ][:50]


__all__ = ["PublishService", "ScopeError", "ScopeNeedsConfirmation"]
