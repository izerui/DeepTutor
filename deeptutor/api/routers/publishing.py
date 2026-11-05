"""Publish scope API (spec 2026-10-29 §7): who receives a published item."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any, TypeVar

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator, model_validator

from deeptutor.publishing.service import PublishService, ScopeError, ScopeNeedsConfirmation

router = APIRouter()

T = TypeVar("T")


async def _call(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    try:
        return await asyncio.to_thread(function, *args, **kwargs)
    except ScopeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


class _NobodyNamed(BaseModel):
    """Nobody is named any more (spec 2026-11-05 remove-teacher-management §1.2).

    Older clients send ``users: []`` with every choice; that is still accepted.
    """

    @model_validator(mode="before")
    @classmethod
    def _refuse_users(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("users"):
            raise ValueError("Content reaches the people who join it; it cannot be sent to chosen accounts.")
        return data


class ScopeChoice(_NobodyNamed):
    """Sent with a first publication (spec 2026-11-02 §4.1): nobody is named, people join."""

    everyone: bool = False
    #: Accepted from older clients: publishing reaches only who joins anyway.
    join_only: bool = False

    @field_validator("everyone")
    @classmethod
    def _not_everyone(cls, value: bool) -> bool:
        if value:
            raise ValueError("To reach everyone, put it in the learning market.")
        return value


@contextmanager
def chosen(choice: ScopeChoice | None):
    """``with chosen(body.scope):`` around a first publication (says whether it is for the market)."""
    from deeptutor.publishing.scope import join_only

    with join_only(bool(choice and choice.join_only)):
        yield


class ScopeUpdate(_NobodyNamed):
    #: Only an older item reaches everyone; this may turn that off, never on.
    everyone: bool = False
    confirm: bool = False


@router.get("/{kind}/{item_id}/scope")
async def get_scope(kind: str, item_id: str):
    return await _call(PublishService().describe, kind, item_id)


@router.put("/{kind}/{item_id}/scope")
async def set_scope(kind: str, item_id: str, body: ScopeUpdate):
    try:
        return await asyncio.to_thread(
            PublishService().set_scope, kind, item_id,
            everyone=body.everyone, confirm=body.confirm,
        )
    except ScopeNeedsConfirmation as exc:
        # Spec §7.2: say how many people lose access; nothing was written.
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc), "needs_confirmation": True, "affected": exc.affected},
        )
    except ScopeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
