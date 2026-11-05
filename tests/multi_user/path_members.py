"""Learners joining or leaving a teacher's Mastery Path, for tests.

Nobody is handed a path or taken off it by hand any more (spec
2026-11-05-remove-teacher-management §1.2): a path reaches the people who
join it. These write the enrollment rows the market writes (``via='migrated'``,
like the ``deliver`` fixture) and let the path's assignments follow, which is
what the removed ``TeachingService.assign`` / ``revoke`` used to do by hand.
"""

from __future__ import annotations

import time
from typing import Any


def join_path(template_id: str, *user_ids: str) -> list[dict[str, Any]]:
    """Those accounts join the path; returns its assignments."""
    from deeptutor.catalog import enrollment
    from deeptutor.catalog.store import db
    from deeptutor.teaching.store import TeachingStore

    now = time.time()
    with db.write() as conn:
        conn.executemany(
            "INSERT INTO enrollments (kind, item_id, user_id, via, joined_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(kind, item_id, user_id, via) DO UPDATE SET left_at = NULL",
            [("mastery_path", template_id, user_id, enrollment.MIGRATED, now) for user_id in user_ids],
        )
    enrollment.reconcile_path(template_id)
    return [a.model_dump(mode="json") for a in TeachingStore().list_assignments(template_id)]


def leave_path(template_id: str, *user_ids: str) -> None:
    """Those accounts leave the path; their copies close."""
    from deeptutor.catalog import enrollment
    from deeptutor.catalog.store import db

    with db.write() as conn:
        conn.executemany(
            "UPDATE enrollments SET left_at = ? WHERE kind = 'mastery_path' AND item_id = ? AND user_id = ? "
            "AND left_at IS NULL",
            [(time.time(), template_id, user_id) for user_id in user_ids],
        )
    enrollment.reconcile_path(template_id)
