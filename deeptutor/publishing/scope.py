"""Publish scopes: who receives a published item (spec 2026-10-29 §7).

A scope combines three kinds of target: specific accounts, specific classes
(their students and teachers, including later joiners) and everyone.

* Books and knowledge bases stay governed by their ``visibility`` column:
  ``private`` reaches nobody but the owner. A public item without a scope row
  reaches everyone, as before; with one, only its audience.
* A Mastery Path's audience is turned into assignments. Its scope only
  counts students. An unmanaged path keeps its hand-made assignments; once a
  scope is set, assignments follow it: :func:`reconcile_template` when the
  scope changes, :func:`reconcile_student` when a student's classes change
  or the student opens their learning list.

Reading never creates the store. The store is a leaf: writers here never
hold another store's lock, and teaching writes happen after this store's
transaction has committed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import time
from typing import Any

from deeptutor.publishing import store

logger = logging.getLogger(__name__)

BOOK = "book"
KNOWLEDGE_BASE = "knowledge_base"
MASTERY_PATH = "mastery_path"
PRACTICE_SET = "practice_set"
KINDS = (BOOK, KNOWLEDGE_BASE, MASTERY_PATH, PRACTICE_SET)
#: Kinds whose receivers are students only (they study or answer them).
STUDENT_KINDS = (MASTERY_PATH, PRACTICE_SET)


class ScopeError(Exception):
    status_code = 400


class ScopeNotFound(ScopeError):
    status_code = 404


class ScopeForbidden(ScopeError):
    status_code = 403


class ScopeNeedsConfirmation(ScopeError):
    """Narrowing would cut people off; nothing was written (spec §7.2)."""

    status_code = 409

    def __init__(self, affected: int) -> None:
        super().__init__(f"This change removes access for {affected} people.")
        self.affected = affected


@dataclass
class Scope:
    everyone: bool = False
    users: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    #: False when the item has never had a scope (the old rule applies).
    managed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "everyone": self.everyone,
            "users": list(self.users),
            "classes": list(self.classes),
            "managed": self.managed,
        }

    @property
    def empty(self) -> bool:
        return not (self.everyone or self.users or self.classes)


# ── reading ────────────────────────────────────────────────


def get_scope(kind: str, item_id: str) -> Scope | None:
    """The stored scope, or ``None`` when the item has never had one."""
    with store.read() as conn:
        if conn is None:
            return None
        row = conn.execute(
            "SELECT everyone FROM publish_items WHERE kind = ? AND item_id = ?", (kind, item_id)
        ).fetchone()
        if row is None:
            return None
        targets = conn.execute(
            "SELECT target_type, target_id FROM publish_targets WHERE kind = ? AND item_id = ? "
            "ORDER BY target_type, target_id",
            (kind, item_id),
        ).fetchall()
    return Scope(
        everyone=bool(row["everyone"]),
        users=[t["target_id"] for t in targets if t["target_type"] == "user"],
        classes=[t["target_id"] for t in targets if t["target_type"] == "class"],
    )


def managed_items(kind: str) -> list[str]:
    with store.read() as conn:
        if conn is None:
            return []
        return [
            row["item_id"]
            for row in conn.execute("SELECT item_id FROM publish_items WHERE kind = ?", (kind,))
        ]


def member_class_ids(user_id: str) -> set[str]:
    """Active classes the account is in, as a student or as a teacher."""
    from deeptutor.classes import store as class_store

    with class_store.read() as conn:
        if conn is None:
            return set()
        rows = conn.execute(
            "SELECT m.class_id FROM class_members m JOIN classes c ON c.id = m.class_id "
            "WHERE m.student_id = ? AND c.archived = 0 "
            "UNION SELECT t.class_id FROM class_teachers t JOIN classes c ON c.id = t.class_id "
            "WHERE t.user_id = ? AND c.archived = 0",
            (user_id, user_id),
        ).fetchall()
    return {row[0] for row in rows}


def _reaches(scope: Scope, user_id: str, classes: set[str] | None = None) -> bool:
    if scope.everyone or user_id in scope.users:
        return True
    if not scope.classes:
        return False
    if classes is None:
        classes = member_class_ids(user_id)
    return bool(classes & set(scope.classes))


def allows(kind: str, item_id: str, user_id: str) -> bool:
    """Whether a published book or knowledge base reaches ``user_id``.

    ``True`` for items without a scope (the old "public means everyone").
    The caller has already checked that the item is public.
    """
    try:
        scope = get_scope(kind, item_id)
    except Exception:
        # An unreadable scope store must not widen access.
        logger.exception("Could not read the publish scope of %s %s", kind, item_id)
        return False
    return scope is None or _reaches(scope, user_id)


def _accounts() -> dict[str, dict[str, Any]]:
    from deeptutor.classes.service import _accounts as accounts

    return accounts()


def _class_audience(class_ids: list[str], *, students_only: bool) -> set[str]:
    from deeptutor.classes import store as class_store

    if not class_ids:
        return set()
    marks = ",".join("?" for _ in class_ids)
    with class_store.read() as conn:
        if conn is None:
            return set()
        sql = (
            f"SELECT m.student_id FROM class_members m JOIN classes c ON c.id = m.class_id "
            f"WHERE m.class_id IN ({marks}) AND c.archived = 0"
        )
        if not students_only:
            sql += (
                f" UNION SELECT t.user_id FROM class_teachers t JOIN classes c ON c.id = t.class_id "
                f"WHERE t.class_id IN ({marks}) AND c.archived = 0"
            )
        params = list(class_ids) * (1 if students_only else 2)
        return {row[0] for row in conn.execute(sql, params)}


def audience(scope: Scope, *, students_only: bool = False, owner_id: str = "") -> set[str]:
    """Active accounts the scope reaches, the owner excluded."""
    accounts = {
        user_id: row for user_id, row in _accounts().items()
        if not row["disabled"] and (not students_only or row["role"] == "student")
    }
    if scope.everyone:
        reached = set(accounts)
    else:
        reached = set(scope.users) | _class_audience(scope.classes, students_only=students_only)
    reached.discard(owner_id)
    return reached & set(accounts)


# ── writing ────────────────────────────────────────────────


def _store_scope(kind: str, item_id: str, scope: Scope, *, actor_id: str) -> None:
    with store.write() as conn:
        conn.execute(
            "INSERT INTO publish_items (kind, item_id, everyone, updated_by, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(kind, item_id) DO UPDATE SET "
            "everyone = excluded.everyone, updated_by = excluded.updated_by, "
            "updated_at = excluded.updated_at",
            (kind, item_id, int(scope.everyone), actor_id, time.time()),
        )
        conn.execute("DELETE FROM publish_targets WHERE kind = ? AND item_id = ?", (kind, item_id))
        conn.executemany(
            "INSERT INTO publish_targets VALUES (?, ?, ?, ?)",
            [(kind, item_id, "user", u) for u in scope.users]
            + [(kind, item_id, "class", c) for c in scope.classes],
        )


def forget(kind: str, item_id: str) -> None:
    """Drop an item's scope (the item was deleted)."""
    if not store.scopes_db_path().exists():
        return
    with store.write() as conn:
        conn.execute("DELETE FROM publish_items WHERE kind = ? AND item_id = ?", (kind, item_id))


# ── Mastery Paths: scope -> assignments ────────────────────


def _template_scope(template_id: str) -> Scope:
    """The stored scope, or the hand-made assignments of an unmanaged path."""
    scope = get_scope(MASTERY_PATH, template_id)
    if scope is not None:
        return scope
    from deeptutor.teaching.store import TeachingStore

    return Scope(
        users=[
            a.student_user_id
            for a in TeachingStore().list_assignments(template_id)
            if a.status == "active"
        ],
        managed=False,
    )


def _apply_assignments(template, add: set[str], remove: set[str]) -> None:
    from deeptutor.teaching.instances import notify_instance_closed
    from deeptutor.teaching.service import sync_material_references
    from deeptutor.teaching.store import TeachingStore

    if not add and not remove:
        return
    teaching = TeachingStore()
    if add:
        teaching.assign(template.id, sorted(add), assigned_by=template.owner_user_id)
    for student_id in remove:
        teaching.revoke(template.id, student_id)
    if remove:
        notify_instance_closed(template.id)
    try:
        sync_material_references(template.id, store=teaching, restore=bool(add))
    except Exception:
        logger.exception("Could not sync the materials of teacher path %s", template.id)


def reconcile_template(template_id: str) -> None:
    """Make a scoped path's active assignments equal its student audience."""
    from deeptutor.teaching.store import TeachingStore

    scope = get_scope(MASTERY_PATH, template_id)
    teaching = TeachingStore()
    template = teaching.get_template(template_id)
    if scope is None or template is None or template.status != "published":
        return
    wanted = audience(scope, students_only=True, owner_id=template.owner_user_id)
    active = {a.student_user_id for a in teaching.list_assignments(template_id) if a.status == "active"}
    _apply_assignments(template, wanted - active, active - wanted)


def reconcile_student(student_id: str) -> None:
    """Bring one student's assignments in line with every scoped path."""
    from deeptutor.teaching.store import TeachingStore

    templates = managed_items(MASTERY_PATH)
    if not templates:
        return
    account = _accounts().get(student_id)
    is_student = bool(account) and account["role"] == "student" and not account["disabled"]
    classes = member_class_ids(student_id) if is_student else set()
    teaching = TeachingStore()
    assigned = {
        a.template_id for a in teaching.assignments_for_student(student_id) if a.status == "active"
    }
    for template_id in templates:
        template = teaching.get_template(template_id)
        scope = get_scope(MASTERY_PATH, template_id)
        if template is None or scope is None or template.status != "published":
            continue
        wanted = (
            is_student and student_id != template.owner_user_id
            and _reaches(scope, student_id, classes)
        )
        has = template_id in assigned
        if wanted != has:
            _apply_assignments(
                template, {student_id} if wanted else set(), set() if wanted else {student_id}
            )


def reconcile_student_quietly(student_id: str) -> None:
    try:
        reconcile_student(student_id)
    except Exception:
        logger.exception("Could not update the Mastery Path assignments of %s", student_id)


def path_added_students(template_id: str, student_ids: list[str], *, actor_id: str) -> None:
    """Hand-picked students join a scoped path's audience as named accounts."""
    scope = get_scope(MASTERY_PATH, template_id)
    if scope is None:
        return
    scope.users = list(dict.fromkeys([*scope.users, *student_ids]))
    _store_scope(MASTERY_PATH, template_id, scope, actor_id=actor_id)


def path_revoke_allowed(template_id: str, student_id: str, *, actor_id: str) -> None:
    """Drop a named student from a scoped path; refuse when a class or everyone still reaches them."""
    scope = get_scope(MASTERY_PATH, template_id)
    if scope is None:
        return
    scope.users = [u for u in scope.users if u != student_id]
    if _reaches(scope, student_id):
        raise ScopeError(
            "This student receives the path through a class or everyone; change the scope instead."
        )
    _store_scope(MASTERY_PATH, template_id, scope, actor_id=actor_id)


__all__ = [
    "BOOK",
    "KINDS",
    "KNOWLEDGE_BASE",
    "MASTERY_PATH",
    "PRACTICE_SET",
    "STUDENT_KINDS",
    "Scope",
    "ScopeError",
    "ScopeForbidden",
    "ScopeNeedsConfirmation",
    "ScopeNotFound",
    "allows",
    "audience",
    "forget",
    "get_scope",
    "member_class_ids",
    "path_added_students",
    "path_revoke_allowed",
    "reconcile_student",
    "reconcile_student_quietly",
    "reconcile_template",
]
