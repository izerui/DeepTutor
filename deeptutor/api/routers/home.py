"""The student home (spec 2026-11-03 batch 1 §6): assigned, school, mine."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from fastapi import APIRouter

from deeptutor.multi_user.context import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

SCHOOL_DAYS = 30
SCHOOL_LIMIT = 6
MINE_LIMIT = 4


def _recent(stamp: Any, since: datetime) -> bool:
    try:
        moment = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return True
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment >= since


def _school() -> list[dict[str, Any]]:
    """Books and knowledge bases administrators published that the student can read."""
    from deeptutor.book import registry as book_registry
    from deeptutor.knowledge import registry as kb_registry
    from deeptutor.publishing.provenance import provenance

    user = get_current_user()
    since = datetime.now(timezone.utc) - timedelta(days=SCHOOL_DAYS)
    items: list[dict[str, Any]] = []
    for record in book_registry.list_book_records(visibility="public"):
        if provenance(record.created_by, user)["kind"] == "school" and book_registry.can_read_book(record, user) \
                and _recent(record.updated_at, since):
            items.append({"kind": "book", "id": record.book_id, "title": record.title, "updated_at": record.updated_at})
    for record in kb_registry.list_records(visibility="public"):
        if provenance(record.created_by, user)["kind"] == "school" and kb_registry.can_read(record, user) \
                and _recent(record.updated_at, since):
            items.append({"kind": "knowledge_base", "id": record.kb_id, "ref": record.ref,
                          "title": record.name, "updated_at": record.updated_at})
    items.sort(key=lambda item: str(item["updated_at"]), reverse=True)
    return items[:SCHOOL_LIMIT]


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
        for name, loader in (("school", _school), ("mine", _mine)):
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
