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


def forget_deleted_account(user_id: str) -> None:
    """Hand-over stage ``classes``: the deleted id teaches and attends nothing."""
    _leave_classes(user_id, as_teacher=True, as_student=True)


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
    "forget_deleted_account",
    "forget_publish_targets",
    "transfer_practice_sets",
]
