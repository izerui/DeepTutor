"""Publish scope API (spec 2026-10-29 §7): who receives a published item."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from deeptutor.publishing.service import PublishService, ScopeError, ScopeNeedsConfirmation

router = APIRouter()

T = TypeVar("T")


async def _call(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    try:
        return await asyncio.to_thread(function, *args, **kwargs)
    except ScopeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


class ScopeChoice(BaseModel):
    """The receivers chosen when an item is published for the first time (spec 2026-11-02 §4.1)."""

    everyone: bool = False
    users: list[str] = Field(default_factory=list, max_length=1000)
    classes: list[str] = Field(default_factory=list, max_length=200)
    groups: list[str] = Field(default_factory=list, max_length=200)

    def to_scope(self):
        from deeptutor.publishing.scope import Scope

        return Scope(
            everyone=self.everyone, users=list(self.users), classes=list(self.classes), groups=list(self.groups),
        )


def chosen(choice: ScopeChoice | None):
    """``with chosen(body.scope):`` around a publication."""
    from deeptutor.publishing.scope import chosen_scope

    return chosen_scope(choice.to_scope() if choice is not None else None)


class ScopeUpdate(BaseModel):
    everyone: bool = False
    users: list[str] = Field(default_factory=list, max_length=1000)
    classes: list[str] = Field(default_factory=list, max_length=200)
    groups: list[str] = Field(default_factory=list, max_length=200)
    confirm: bool = False


@router.get("/people")
async def search_people(q: str = Query(default="", max_length=100)):
    return {"people": await _call(PublishService().search_people, q)}


@router.get("/{kind}/{item_id}/scope")
async def get_scope(kind: str, item_id: str):
    return await _call(PublishService().describe, kind, item_id)


@router.put("/{kind}/{item_id}/scope")
async def set_scope(kind: str, item_id: str, body: ScopeUpdate):
    try:
        return await asyncio.to_thread(
            PublishService().set_scope, kind, item_id,
            everyone=body.everyone, users=body.users, classes=body.classes, confirm=body.confirm,
            groups=body.groups,
        )
    except ScopeNeedsConfirmation as exc:
        # Spec §7.2: say how many people lose access; nothing was written.
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc), "needs_confirmation": True, "affected": exc.affected},
        )
    except ScopeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
