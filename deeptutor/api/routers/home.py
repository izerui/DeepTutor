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


def _mine_courses() -> list[dict[str, Any]]:
    """The learner's own courses still in play, most recently changed first (batch 4 §4)."""
    from deeptutor.services.courses import get_course_service

    rows = [c for c in get_course_service().list_courses() if c.status != "archived"]
    rows.sort(key=lambda c: c.updated_at, reverse=True)
    return [{"id": c.id, "name": c.name, "color": c.color, "updated_at": c.updated_at} for c in rows[:MINE_LIMIT]]


@router.get("")
async def home() -> dict[str, Any]:
    from deeptutor.multi_user.roles import can_create_personal_mastery
    from deeptutor.tasks.service import StudentTasks

    def build() -> dict[str, Any]:
        tasks = StudentTasks().assigned()
        sections: dict[str, Any] = {
            "assigned": [t for t in tasks if t["state"] in ("open", "overdue")],
            "history_count": sum(1 for t in tasks if t["state"] in ("completed", "closed")),
        }
        from deeptutor.classes.announcements import AnnouncementService

        for name, loader in (
            ("market", _market), ("mine", _mine), ("mine_courses", _mine_courses),
            ("announcements", AnnouncementService().for_home),
        ):
            try:
                sections[name] = loader()
            except Exception:
                logger.exception("Could not build the %s block of the home page", name)
                sections[name] = []
        try:
            sections["can_create_path"] = bool(can_create_personal_mastery())
        except Exception:
            sections["can_create_path"] = False
        return sections

    return await asyncio.to_thread(build)


@router.get("/workbench")
async def teacher_workbench() -> dict[str, Any]:
    """The teacher's home (spec 2026-11-03 batch 3 §5.1); administrators get only their role."""
    from deeptutor.teaching.workbench import workbench

    return await asyncio.to_thread(workbench)


@router.get("/to-grade")
async def to_grade_list() -> dict[str, Any]:
    """Batch 3 §5.4: every hand-in waiting for the current teacher."""
    from deeptutor.classes.service import _role
    from deeptutor.teaching.workbench import to_grade

    user = get_current_user()
    if _role(user) != "teacher":
        return {"items": []}
    return {"items": await asyncio.to_thread(to_grade, user.id)}
