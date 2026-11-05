"""What an account's role change or deletion means for the content it published.

* :func:`after_role_change` — called by the admin role route after the new
  role is stored.
* :func:`forget_publish_targets` / :func:`transfer_practice_sets` — account
  hand-over stages (``publish_targets``, ``practice_sets``).

Every function is idempotent: a second run finds nothing left to change.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


def _end_student_assignments(user_id: str) -> None:
    """A former learner keeps their records but no author's path stays open to them by name."""
    from deeptutor.teaching import service as teaching_service

    failed = teaching_service.handle_account_deleted(
        user_id, successor_id="", stages=("student_assignments", "path_materials"),
    )
    if failed:
        logger.warning("Could not close every teacher path of %s: %s", user_id, failed)


def after_role_change(user_id: str, old_role: str, new_role: str) -> None:
    """Close what the new role no longer allows; paths follow what the account joined.

    Spec 2026-11-05: ``teacher``, ``student`` and ``user`` are all learners,
    so only a move between learner and administrator changes anything.
    """
    if (old_role == "admin") == (new_role == "admin"):
        return
    if new_role == "admin":
        _end_student_assignments(user_id)
    from deeptutor.publishing.scope import reconcile_student_quietly

    reconcile_student_quietly(user_id)


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
    """Hand-over stage ``practice_sets``: a deleted account's sets go to the successor."""
    from deeptutor.practice_sets import store as practice_store

    if not practice_store.practice_db_path().exists():
        return 0
    with practice_store.write() as conn:
        moved = conn.execute(
            "UPDATE practice_sets SET owner_user_id = ?, updated_at = ? WHERE owner_user_id = ?",
            (successor_id, time.time(), user_id),
        ).rowcount
    return int(moved)


__all__ = ["after_role_change", "forget_publish_targets", "transfer_practice_sets"]
