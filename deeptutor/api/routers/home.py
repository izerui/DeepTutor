"""The student home (spec 2026-11-03 batch 1 §6): assigned, newly listed, mine."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter

from deeptutor.multi_user.context import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

SCHOOL_DAYS = 30
SCHOOL_LIMIT = 6
MINE_LIMIT = 4


def _content_announcements() -> list[dict[str, Any]]:
    from deeptutor.catalog.announcements import AnnouncementService as ContentAnnouncements

    return ContentAnnouncements().mine()


def _market() -> list[dict[str, Any]]:
    """Platform batch 1 §5.3: newly listed in the market, for everyone."""
    import time

    from deeptutor.catalog.market import recent

    return recent(get_current_user(), since=time.time() - SCHOOL_DAYS * 86400, limit=SCHOOL_LIMIT)


def _mine() -> list[dict[str, Any]]:
    """The student's own Mastery Paths, most recently studied first."""
    from deeptutor.learning import policy
    from deeptutor.learning.storage import LearningStore

    store = LearningStore()
    own = []
    for path_id in store.list_all():
        try:
            progress = store.load(path_id)
        except Exception:
            continue
        if progress is None or progress.origin is not None:
            continue
        counts = policy.map_summary(progress)["counts"]
        own.append({
            "path_id": progress.book_id, "name": policy.path_display_name(progress),
            "mastered": counts["mastered"], "total": counts["total"], "updated_at": progress.updated_at,
        })
    own.sort(key=lambda item: item["updated_at"], reverse=True)
    return own[:MINE_LIMIT]


def _joined() -> list[dict[str, Any]]:
    """What one joined from the market, newest first, for every role (traceability #6)."""
    from deeptutor.catalog import items
    from deeptutor.catalog.enrollment import DIRECT
    from deeptutor.catalog.market import _study_target
    from deeptutor.catalog.store import db

    user = get_current_user()
    marks = ",".join("?" for _ in DIRECT)
    with db.read() as conn:
        rows = conn.execute(
            f"SELECT kind, item_id, MAX(joined_at) AS at FROM enrollments WHERE user_id = ? AND left_at IS NULL "  # nosec B608
            f"AND via IN ({marks}) GROUP BY kind, item_id ORDER BY at DESC LIMIT 12",
            (user.id, *DIRECT),
        ).fetchall() if conn else []
    out = []
    for row in rows:
        info = items.info(row["kind"], row["item_id"])
        if info is None or info.closed:
            continue
        out.append({"kind": row["kind"], "id": row["item_id"], "title": info.title, "joined_at": row["at"],
                    "study": _study_target(row["kind"], row["item_id"])})
    return out[:MINE_LIMIT * 2]


def _mine_courses() -> list[dict[str, Any]]:
    """The learner's own courses still in play, most recently changed first (batch 4 §4)."""
    from deeptutor.services.courses import get_course_service

    rows = [c for c in get_course_service().list_courses() if c.status != "archived"]
    rows.sort(key=lambda c: c.updated_at, reverse=True)
    return [{"id": c.id, "name": c.name, "color": c.color, "updated_at": c.updated_at} for c in rows[:MINE_LIMIT]]


@router.get("")
async def home() -> dict[str, Any]:
    from deeptutor.tasks.service import StudentTasks

    def build() -> dict[str, Any]:
        tasks = StudentTasks().assigned()
        sections: dict[str, Any] = {
            "assigned": [t for t in tasks if t["state"] == "open"],
            "history_count": sum(1 for t in tasks if t["state"] in ("completed", "closed")),
        }
        for name, loader in (
            ("market", _market), ("mine", _mine), ("mine_courses", _mine_courses), ("joined", _joined),
            # Announcements of the courses and paths one joined.
            ("announcements", _content_announcements),
        ):
            try:
                sections[name] = loader()
            except Exception:
                logger.exception("Could not build the %s block of the home page", name)
                sections[name] = []
        return sections

    return await asyncio.to_thread(build)


@router.get("/workbench")
async def teacher_workbench() -> dict[str, Any]:
    """A learner's workbench (spec 2026-11-03 batch 3 §5.1, 2026-11-05 §2); administrators get only their role."""
    from deeptutor.teaching.workbench import workbench

    return await asyncio.to_thread(workbench)
