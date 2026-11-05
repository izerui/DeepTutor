"""Publish scopes: who receives a published item (spec 2026-10-29 §7, learning platform).

On the learning platform people reach content by joining it from the market
(``members``, read from the market store with the scope). Content from before
may still name accounts or say "everyone": both are read, never written anew
(spec 2026-11-05 remove-teacher-management §2).

* Books and knowledge bases stay governed by their ``visibility`` column:
  ``private`` reaches nobody but the owner. A public item without a scope row
  reaches everyone, as before; with one, only its audience.
* A Mastery Path's audience is turned into assignments: :func:`reconcile_template`
  when its receivers change, :func:`reconcile_student` when a learner opens
  their learning list. An unmanaged path keeps its old hand-made assignments.
* An item an administrator took down reaches nobody but its owner.

Reading never creates the store. The store is a leaf: writers here never
hold another store's lock, and teaching writes happen after this store's
transaction has committed.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
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
TASK = "task"
COURSE = "course"
KINDS = (BOOK, KNOWLEDGE_BASE, MASTERY_PATH, PRACTICE_SET, TASK, COURSE)
#: Kinds whose named receivers are students only (they study or answer them).
STUDENT_KINDS = (MASTERY_PATH, PRACTICE_SET, TASK, COURSE)


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
    #: Content from before the market only; new content reaches everyone through the market.
    everyone: bool = False
    #: Accounts named by older content (e.g. a teacher's remedial set); read, never added.
    users: list[str] = field(default_factory=list)
    #: False when the item has never had a scope (the old rule applies).
    managed: bool = True
    #: Who joined it from the market. Read with the scope, never stored with it.
    members: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"everyone": self.everyone, "users": list(self.users), "managed": self.managed}

    @property
    def empty(self) -> bool:
        return not (self.everyone or self.users)


# ── reading ────────────────────────────────────────────────


def get_scope(kind: str, item_id: str) -> Scope | None:
    """The stored scope with the item's market members, or ``None`` when it never had one.

    An item an administrator took down reaches nobody but its owner, whatever
    its scope says.
    """
    return _with_members(kind, item_id, _stored_scope(kind, item_id))


def _with_members(kind: str, item_id: str, scope: Scope | None) -> Scope | None:
    try:
        from deeptutor.catalog.enrollment import access_state

        taken_down, members = access_state(kind, item_id)
    except Exception:
        # An unreadable market store must not widen access.
        logger.exception("Could not read the market members of %s %s", kind, item_id)
        return Scope()
    if taken_down:
        return Scope()
    if scope is not None:
        scope.members = members
    return scope


def _stored_scope(kind: str, item_id: str) -> Scope | None:
    with store.read() as conn:
        if conn is None:
            return None
        row = conn.execute(
            "SELECT everyone FROM publish_items WHERE kind = ? AND item_id = ?", (kind, item_id)
        ).fetchone()
        if row is None:
            return None
        users = [
            t["target_id"] for t in conn.execute(
                "SELECT target_id FROM publish_targets WHERE kind = ? AND item_id = ? AND target_type = 'user' "
                "ORDER BY target_id",
                (kind, item_id),
            )
        ]
    return Scope(everyone=bool(row["everyone"]), users=users)


def named_items(kind: str, user_id: str) -> list[str]:
    """Items whose stored scope names this account (legacy: content once sent to chosen learners)."""
    with store.read() as conn:
        if conn is None:
            return []
        return [
            row["item_id"] for row in conn.execute(
                "SELECT item_id FROM publish_targets WHERE kind = ? AND target_type = 'user' AND target_id = ?",
                (kind, user_id),
            )
        ]


def managed_items(kind: str) -> list[str]:
    with store.read() as conn:
        if conn is None:
            return []
        return [
            row["item_id"]
            for row in conn.execute("SELECT item_id FROM publish_items WHERE kind = ?", (kind,))
        ]


def _reaches(scope: Scope, user_id: str) -> bool:
    return scope.everyone or user_id in scope.users or user_id in scope.members


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
    from deeptutor.multi_user.accounts import accounts

    return accounts()


def audience(scope: Scope, *, students_only: bool = False, owner_id: str = "") -> set[str]:
    """Active accounts the scope reaches, the owner excluded.

    *students_only* narrows the receivers a publisher named; people who joined
    count whatever their role.
    """
    active = {user_id: row for user_id, row in _accounts().items() if not row["disabled"]}
    named = {user_id for user_id, row in active.items() if not students_only or row["role"] == "student"}
    reached = set(named) if scope.everyone else set(scope.users) & named
    reached |= set(scope.members) & set(active)
    reached.discard(owner_id)
    return reached


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
            [(kind, item_id, "user", u) for u in scope.users],
        )


@contextmanager
def join_only(enabled: bool = True) -> Iterator[None]:
    """Kept for callers that say so explicitly: publishing reaches only who joins anyway."""
    yield


def default_scope_on_publish(kind: str, item_id: str) -> None:
    """A first publication stores an empty scope: nobody is named, people join.

    Runs where an item becomes public or published, inside that write, so an
    item is never readable by everyone in between. An item that already has a
    scope keeps it. Code without a request user (migrations, maintenance) adds
    none, i.e. the old "everyone".
    """
    from deeptutor.multi_user.context import get_current_user_or_none

    user = get_current_user_or_none()
    if user is None or get_scope(kind, item_id) is not None:
        return
    _store_scope(kind, item_id, Scope(), actor_id=user.id)


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
    """Make a scoped path's active assignments equal its audience."""
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
    """Bring one learner's assignments in line with every scoped path."""
    from deeptutor.teaching.store import TeachingStore

    templates = managed_items(MASTERY_PATH)
    if not templates:
        return
    account = _accounts().get(student_id)
    active = bool(account) and not account["disabled"]
    is_student = active and account["role"] == "student"
    teaching = TeachingStore()
    assigned = {
        a.template_id for a in teaching.assignments_for_student(student_id) if a.status == "active"
    }
    try:
        from deeptutor.catalog.enrollment import access_states

        states = access_states(MASTERY_PATH)  # one read for every path
    except Exception:
        logger.exception("Could not read the market members of the paths")
        return
    for template_id in templates:
        template = teaching.get_template(template_id)
        blocked, joined = states.get(template_id, (False, set()))
        scope = None if blocked else _stored_scope(MASTERY_PATH, template_id)
        if scope is not None:
            scope.members = sorted(joined)
        elif blocked:
            scope = Scope()
        if template is None or scope is None or template.status != "published":
            continue
        wanted = student_id != template.owner_user_id and (
            (is_student and (scope.everyone or student_id in scope.users)) or (active and student_id in scope.members)
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


__all__ = [
    "BOOK",
    "COURSE",
    "KINDS",
    "KNOWLEDGE_BASE",
    "MASTERY_PATH",
    "PRACTICE_SET",
    "STUDENT_KINDS",
    "TASK",
    "Scope",
    "ScopeError",
    "ScopeForbidden",
    "ScopeNeedsConfirmation",
    "ScopeNotFound",
    "allows",
    "audience",
    "forget",
    "get_scope",
    "join_only",
    "managed_items",
    "named_items",
    "reconcile_student",
    "reconcile_student_quietly",
    "reconcile_template",
]
