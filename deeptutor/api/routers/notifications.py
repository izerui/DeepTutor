"""In-app notifications API (spec 2026-11-03 batch 1 §5)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel, Field

from deeptutor.multi_user.context import get_current_user
from deeptutor.notifications import service

router = APIRouter()


class MarkRead(BaseModel):
    #: Leave out to mark every notification read.
    ids: list[str] | None = Field(default=None, max_length=200)


@router.get("")
async def list_notifications():
    user = get_current_user()
    items, unread = await asyncio.gather(
        asyncio.to_thread(service.list_for, user.id),
        asyncio.to_thread(service.unread_count, user.id),
    )
    return {"notifications": items, "unread": unread}


@router.get("/unread")
async def unread():
    return {"unread": await asyncio.to_thread(service.unread_count, get_current_user().id)}


@router.post("/read")
async def mark_read(body: MarkRead):
    user = get_current_user()
    await asyncio.to_thread(service.mark_read, user.id, body.ids)
    return {"unread": await asyncio.to_thread(service.unread_count, user.id)}
