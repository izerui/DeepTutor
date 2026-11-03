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

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
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
#: Kinds whose receivers are students only (they study or answer them).
STUDENT_KINDS = (MASTERY_PATH, PRACTICE_SET, TASK, COURSE)


class ScopeError(Exception):
    status_code = 400


class ScopeNotFound(ScopeError):
    status_code = 404


class ScopeForbidden(ScopeError):
    status_code = 403


class ScopeRequired(ScopeError):
    """A first publication by someone who teaches no class names no receivers."""

    def __init__(self) -> None:
        super().__init__("Choose who receives this before publishing it.")


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
    #: Departments, grades or both (``department:<id>``, ``grade:<id>``,
    #: ``department:<id>+grade:<id>``); administrators only (2026-11-03 §0).
    groups: list[str] = field(default_factory=list)
    #: False when the item has never had a scope (the old rule applies).
    managed: bool = True
    #: Who joined it from the market (platform batch 1 §4.2). Read with the
    #: scope, never stored with it.
    members: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "everyone": self.everyone,
            "users": list(self.users),
            "classes": list(self.classes),
            "groups": list(self.groups),
            "managed": self.managed,
        }

    @property
    def empty(self) -> bool:
        return not (self.everyone or self.users or self.classes or self.groups)

    def all_classes(self) -> set[str]:
        """Its classes, and the active classes its groups cover right now."""
        if not self.groups:
            return set(self.classes)
        from deeptutor.classes.org import group_class_ids

        return set(self.classes) | group_class_ids(self.groups)


# ── reading ────────────────────────────────────────────────


def get_scope(kind: str, item_id: str) -> Scope | None:
    """The stored scope with the item's market members, or ``None`` when it never had one.

    An item an administrator took out of the market reaches nobody but its
    owner (platform batch 1 §2), whatever its scope says.
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
        targets = conn.execute(
            "SELECT target_type, target_id FROM publish_targets WHERE kind = ? AND item_id = ? "
            "ORDER BY target_type, target_id",
            (kind, item_id),
        ).fetchall()
    return Scope(
        everyone=bool(row["everyone"]),
        users=[t["target_id"] for t in targets if t["target_type"] == "user"],
        classes=[t["target_id"] for t in targets if t["target_type"] == "class"],
        groups=[t["target_id"] for t in targets if t["target_type"] == "group"],
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
    """Active classes the account is in: as a student if a student, as a teacher if a teacher."""
    from deeptutor.classes import store as class_store

    account = _accounts().get(user_id)
    if account is None or account["disabled"] or account["role"] not in ("student", "teacher"):
        return set()
    sql = (
        "SELECT m.class_id FROM class_members m JOIN classes c ON c.id = m.class_id "
        "WHERE m.student_id = ? AND c.archived = 0"
        if account["role"] == "student" else
        "SELECT t.class_id FROM class_teachers t JOIN classes c ON c.id = t.class_id "
        "WHERE t.user_id = ? AND c.archived = 0"
    )
    with class_store.read() as conn:
        if conn is None:
            return set()
        return {row[0] for row in conn.execute(sql, (user_id,))}


def _reaches(scope: Scope, user_id: str, classes: set[str] | None = None) -> bool:
    if scope.everyone or user_id in scope.users or user_id in scope.members:
        return True
    if not scope.classes and not scope.groups:
        return False
    if classes is None:
        classes = member_class_ids(user_id)
    return bool(classes & scope.all_classes())


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
        reached = set(scope.users) | _class_audience(sorted(scope.all_classes()), students_only=students_only)
    reached |= set(scope.members)
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
            + [(kind, item_id, "class", c) for c in scope.classes]
            + [(kind, item_id, "group", g) for g in scope.groups],
        )


def taught_class_ids(user_id: str) -> list[str]:
    from deeptutor.classes import store as class_store

    with class_store.read() as conn:
        if conn is None:
            return []
        return sorted(
            row[0] for row in conn.execute(
                "SELECT t.class_id FROM class_teachers t JOIN classes c ON c.id = t.class_id "
                "WHERE t.user_id = ? AND c.archived = 0",
                (user_id,),
            )
        )


#: The receivers the publisher chose for this publication (see :func:`chosen_scope`).
_chosen: ContextVar[Scope | None] = ContextVar("publish_chosen_scope", default=None)
#: Published for the market: nobody receives it until they join (platform batch 1 §4.4).
_join_only: ContextVar[bool] = ContextVar("publish_join_only", default=False)


@contextmanager
def join_only(enabled: bool = True) -> Iterator[None]:
    """Publish inside this block with no receivers: only people who join reach it."""
    token = _join_only.set(enabled)
    try:
        yield
    finally:
        _join_only.reset(token)


def is_join_only() -> bool:
    return _join_only.get()


def join_only_scope(scope: Scope | None) -> bool:
    """Stored without receivers: reached through the market only."""
    return scope is not None and scope.empty


@contextmanager
def chosen_scope(scope: Scope | None) -> Iterator[None]:
    """Publish inside this block to the receivers the publisher chose.

    Set by the publish routes around the visibility change, so the scope is
    written in the same step as the publication; materials made public along
    with a book or a path get the same receivers.
    """
    token = _chosen.set(scope)
    try:
        yield
    finally:
        _chosen.reset(token)


def default_scope_on_publish(kind: str, item_id: str) -> None:
    """Spec 2026-11-02 §4.1: a first publication names its receivers.

    Runs where an item becomes public or published, inside that write, so an
    item is never readable by everyone in between; a failure aborts the
    publication. The receivers are, in order:

    * the ones the publisher chose (:func:`chosen_scope`), checked like any
      scope change;
    * otherwise the classes the publisher teaches;
    * otherwise none can be assumed: :class:`ScopeRequired` is raised and
      nothing is published ("everyone" is only ever an explicit choice).

    An item that already has a scope keeps it. Code without a request user
    (migrations, maintenance) adds none, i.e. the old "everyone".
    """
    from deeptutor.multi_user.context import get_current_user_or_none

    user = get_current_user_or_none()
    if user is None or get_scope(kind, item_id) is not None:
        return
    if _join_only.get():
        _store_scope(kind, item_id, Scope(), actor_id=user.id)
        return
    chosen = _chosen.get()
    if chosen is not None:
        from deeptutor.publishing.service import PublishService

        validated = PublishService(user)._validated(
            kind, chosen.everyone, chosen.users, chosen.classes, Scope(), groups=chosen.groups
        )
        if validated.empty:
            raise ScopeRequired()
        _store_scope(kind, item_id, validated, actor_id=user.id)
        return
    classes = taught_class_ids(user.id)
    if not classes:
        _no_receivers(kind, item_id)
        return
    _store_scope(kind, item_id, Scope(classes=classes), actor_id=user.id)


def _no_receivers(kind: str, item_id: str) -> None:
    """Nothing chosen and no class to default to: publish nothing."""
    raise ScopeRequired()


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
    scope = _stored_scope(MASTERY_PATH, template_id)
    if scope is None:
        return
    scope.users = list(dict.fromkeys([*scope.users, *student_ids]))
    _store_scope(MASTERY_PATH, template_id, scope, actor_id=actor_id)


def path_revoke_allowed(template_id: str, student_id: str, *, actor_id: str) -> None:
    """Drop a named student from a scoped path; refuse when a class or everyone still reaches them."""
    scope = _stored_scope(MASTERY_PATH, template_id)
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
    "ScopeRequired",
    "allows",
    "chosen_scope",
    "is_join_only",
    "join_only",
    "join_only_scope",
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
