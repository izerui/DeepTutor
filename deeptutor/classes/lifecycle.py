"""What an account's role change or deletion means for classes, scopes and practice sets.

Spec 2026-10-29 §3/§5: a class has teachers (accounts with the teacher role)
and students (accounts with the student role). When an account stops being
one of them, its rows go; nothing it used to be keeps granting access.

* :func:`after_role_change` — called by the admin role route after the new
  role is stored.
* :func:`forget_deleted_account` / :func:`transfer_practice_sets` — account
  hand-over stages (``classes``, ``publish_targets``, ``practice_sets``).

Every function is idempotent: a second run finds nothing left to change.
"""

from __future__ import annotations

import logging
import time

from deeptutor.classes import store

logger = logging.getLogger(__name__)


def _leave_classes(user_id: str, *, as_teacher: bool, as_student: bool) -> None:
    if not (as_teacher or as_student) or not store.classes_db_path().exists():
        return
    with store.write() as conn:
        if as_teacher:
            conn.execute("DELETE FROM class_teachers WHERE user_id = ?", (user_id,))
        if as_student:
            conn.execute("DELETE FROM class_members WHERE student_id = ?", (user_id,))
            conn.execute(
                "UPDATE class_invitations SET status = 'cancelled', responded_at = ? "
                "WHERE student_id = ? AND status = 'pending'",
                (time.time(), user_id),
            )


def _end_student_assignments(user_id: str) -> None:
    """A former student keeps their records but no teacher path stays open to them."""
    from deeptutor.teaching import service as teaching_service

    failed = teaching_service.handle_account_deleted(
        user_id, successor_id="", stages=("student_assignments", "path_materials"),
    )
    if failed:
        logger.warning("Could not close every teacher path of %s: %s", user_id, failed)


def after_role_change(user_id: str, old_role: str, new_role: str) -> None:
    """Drop the class rows and assignments the new role no longer allows."""
    if old_role == new_role:
        return
    _leave_classes(user_id, as_teacher=new_role != "teacher", as_student=new_role != "student")
    if old_role == "student" and new_role != "student":
        _end_student_assignments(user_id)
    if new_role == "student":
        from deeptutor.publishing.scope import reconcile_student_quietly

        # Paths published to everyone reach the new student at once.
        reconcile_student_quietly(user_id)


def hand_over_published(user_id: str, successor_id: str) -> dict[str, int]:
    """A teacher who stops teaching hands what they published to an administrator.

    Spec 2026-11-02 §6.1: like an account deletion, but the account lives on,
    so only what reached others moves — public or referenced knowledge bases
    and books, Mastery Paths ever published, practice sets ever published.
    Drafts and private items stay theirs. Every step is idempotent and raises
    on failure, so the caller changes the role only after this succeeded.
    """
    from deeptutor.book import sharing as book_sharing
    from deeptutor.knowledge import registry as kb_registry
    from deeptutor.practice_sets import store as practice_store
    from deeptutor.teaching.store import TeachingStore

    moved = {
        "knowledge_bases": len(kb_registry.transfer_ownership(user_id, successor_id)),
        "books": len(book_sharing.transfer_ownership(user_id, successor_id, shared_only=True)),
        "teacher_paths": TeachingStore().transfer_owner(user_id, successor_id, published_only=True),
        "practice_sets": 0,
    }
    if practice_store.practice_db_path().exists():
        with practice_store.write() as conn:
            moved["practice_sets"] = int(conn.execute(
                "UPDATE practice_sets SET owner_user_id = ?, updated_at = ? "
                "WHERE owner_user_id = ? AND current_revision > 0",
                (successor_id, time.time(), user_id),
            ).rowcount)
    from deeptutor.tasks.service import transfer_tasks

    counts: dict[str, int] = {}
    transfer_tasks(user_id, successor_id, published_only=True, counts=counts)
    moved.update(counts)
    from deeptutor.catalog.listings import follow_owners

    follow_owners(user_id)  # platform batch 1: listings follow their content
    if any(moved.values()):
        logger.info("Handed what %s published to %s: %s", user_id, successor_id, moved)
        from deeptutor.multi_user.identity import get_user_by_id
        from deeptutor.notifications.service import notify_handover

        found = get_user_by_id(user_id)
        notify_handover(successor_id, user_id, str(found[0]) if found else "", moved)
    return moved


def forget_deleted_account(user_id: str) -> None:
    """Hand-over stage ``classes``: the deleted id teaches, attends and is listed under nothing."""
    _leave_classes(user_id, as_teacher=True, as_student=True)
    from deeptutor.classes.org import forget_teacher

    forget_teacher(user_id)


def forget_publish_targets(user_id: str) -> None:
    """Hand-over stage ``publish_targets``: no scope names the deleted id any more."""
    from deeptutor.publishing import store as scope_store

    if not scope_store.scopes_db_path().exists():
        return
    with scope_store.write() as conn:
        conn.execute(
            "DELETE FROM publish_targets WHERE target_type = 'user' AND target_id = ?", (user_id,)
        )


def transfer_practice_sets(user_id: str, successor_id: str) -> int:
    """Hand-over stage ``practice_sets``: a deleted teacher's sets go to the successor."""
    from deeptutor.practice_sets import store as practice_store

    if not practice_store.practice_db_path().exists():
        return 0
    with practice_store.write() as conn:
        moved = conn.execute(
            "UPDATE practice_sets SET owner_user_id = ?, updated_at = ? WHERE owner_user_id = ?",
            (successor_id, time.time(), user_id),
        ).rowcount
    return int(moved)


__all__ = [
    "after_role_change",
    "hand_over_published",
    "forget_deleted_account",
    "forget_publish_targets",
    "transfer_practice_sets",
]
