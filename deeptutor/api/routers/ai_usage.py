"""Learners' daily AI limit (spec 2026-11-03 batch 3 §8, 2026-11-05 §2)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()


class DefaultLimit(BaseModel):
    #: Tokens per learner per day; ``0`` is no limit.
    daily_tokens: int = Field(ge=0, le=100_000_000)


@router.get("/me")
async def my_usage() -> dict:
    from deeptutor.services.llm.usage_limit import status_for_current_user

    return await asyncio.to_thread(status_for_current_user)


def _require_admin() -> None:
    from deeptutor.multi_user.context import get_current_user

    if not get_current_user().is_admin:
        raise HTTPException(status_code=403, detail="Only administrators set the AI limit.")


@router.get("/default")
async def school_default() -> dict:
    from deeptutor.services.llm.usage_limit import school_default_daily_tokens

    _require_admin()
    return {"daily_tokens": await asyncio.to_thread(school_default_daily_tokens)}


@router.put("/default")
async def set_school_default(payload: DefaultLimit) -> dict:
    from deeptutor.services.llm.usage_limit import set_school_default_daily_tokens

    _require_admin()
    return {"daily_tokens": await asyncio.to_thread(set_school_default_daily_tokens, payload.daily_tokens)}
