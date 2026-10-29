"""Who may set a publish scope, and with which targets (spec 2026-10-29 §7).

Only the publisher sets the scope of their own item (D10: administrators
withdraw or delete other people's content, they do not republish it).
Teachers pick among the classes they teach; administrators among all
classes. Narrowing that cuts anyone off needs ``confirm`` (§7.2).
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
        from deeptutor.classes.service import _role

        return _role(self.actor)

    def _require_publisher(self) -> None:
        if self._role() not in ("teacher", "admin"):
            raise ScopeForbidden("Only teachers and administrators publish content.")

    def _pickable_classes(self) -> dict[str, str]:
        """Class id -> name the actor may publish to."""
        from deeptutor.classes import store as class_store

        with class_store.read() as conn:
            if conn is None:
                return {}
            if self._role() == "admin":
                rows = conn.execute("SELECT id, name FROM classes WHERE archived = 0").fetchall()
            else:
                rows = conn.execute(
                    "SELECT c.id, c.name FROM classes c JOIN class_teachers t ON t.class_id = c.id "
                    "WHERE t.user_id = ? AND c.archived = 0",
                    (self.actor.id,),
                ).fetchall()
        return {row["id"]: row["name"] for row in rows}

    def _current(self, kind: str, item_id: str, info: dict[str, Any]) -> Scope:
        stored = scopes.get_scope(kind, item_id)
        if stored is not None:
            return stored
        if kind == MASTERY_PATH:
            return scopes._template_scope(item_id)
        if kind == PRACTICE_SET:
            return Scope(managed=False)  # a practice set reaches nobody until its scope is set
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
        from deeptutor.classes.service import _accounts

        accounts = _accounts()
        classes = self._pickable_classes()
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
                "classes": [{"id": c, "name": classes.get(c, "")} for c in scope.classes],
            },
            "audience_size": len(self._reached(kind, scope, info)),
            "choices": {"classes": [{"id": k, "name": v} for k, v in sorted(classes.items(), key=lambda i: i[1])]},
            # §7.1: by default a teacher publishes to every class they teach.
            "suggested_classes": [] if self._role() == "admin" else sorted(classes),
        }

    def _validated(self, kind: str, everyone: bool, users: Any, classes: Any, current: Scope) -> Scope:
        """Check what the publisher adds; targets already in the scope may stay.

        A class the teacher no longer teaches, or a student who left their
        classes, must not block other changes; removing them is always allowed.
        """
        from deeptutor.classes.service import _accounts, teacher_student_ids

        users, classes = _clean_ids(users), _clean_ids(classes)
        accounts = _accounts()
        # Deleted accounts drop out silently; they can no longer receive anything.
        users = [u for u in users if u in accounts and u != self.actor.id]
        added_classes = [c for c in classes if c not in current.classes]
        added_users = [u for u in users if u not in current.users]
        pickable = self._pickable_classes()
        if any(c not in pickable for c in added_classes):
            raise ScopeForbidden("You can only publish to classes you teach.")
        reachable = None if self._role() == "admin" else teacher_student_ids(self.actor.id)
        for user_id in added_users:
            account = accounts[user_id]
            if account["disabled"]:
                raise ScopeError("Only active accounts can receive content.")
            if kind in STUDENT_KINDS and account["role"] != "student":
                raise ScopeError(
                    "Only students receive Mastery Paths." if kind == MASTERY_PATH
                    else "Only students receive practice sets."
                )
            # Spec §5: a teacher names only students of their own classes (any teacher may be named).
            if reachable is not None and account["role"] == "student" and user_id not in reachable:
                raise ScopeForbidden("You can only choose students in your classes.")
        return Scope(everyone=bool(everyone), users=users, classes=classes)

    def set_scope(
        self, kind: str, item_id: str, *, everyone: bool, users: Any, classes: Any,
        confirm: bool = False,
    ) -> dict[str, Any]:
        self._require_publisher()
        info = self._item(kind, item_id)
        if kind == MASTERY_PATH and not info["public"]:
            raise ScopeError("Publish this path before choosing who receives it.")
        current = self._current(kind, item_id, info)
        new = self._validated(kind, everyone, users, classes, current)
        before = self._reached(kind, current, info)
        cut = before - self._reached(kind, new, info)
        if cut and not confirm:
            raise ScopeNeedsConfirmation(len(cut))
        scopes._store_scope(kind, item_id, new, actor_id=self.actor.id)
        if kind == MASTERY_PATH:
            scopes.reconcile_template(item_id)
        return self.describe(kind, item_id)

    def search_people(self, query: str) -> list[dict[str, Any]]:
        """Students and teachers by username, for picking specific people."""
        from deeptutor.classes.service import _accounts

        self._require_publisher()
        needle = str(query or "").strip().casefold()
        if not needle:
            return []
        from deeptutor.classes.service import teacher_student_ids

        # Spec §5: teachers see the students of their own classes; any teacher.
        reachable = None if self._role() == "admin" else teacher_student_ids(self.actor.id)
        return [
            {"id": user_id, "username": account["username"], "role": account["role"]}
            for user_id, account in sorted(_accounts().items(), key=lambda item: item[1]["username"])
            if account["role"] in ("student", "teacher") and not account["disabled"]
            and user_id != self.actor.id and needle in account["username"].casefold()
            and (reachable is None or account["role"] == "teacher" or user_id in reachable)
        ][:50]


__all__ = ["PublishService", "ScopeError", "ScopeNeedsConfirmation"]
