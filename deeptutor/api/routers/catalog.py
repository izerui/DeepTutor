"""The learning market (platform batch 1 §8): browsing, joining, listing, reviewing."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from deeptutor.catalog.enrollment import EnrollmentError
from deeptutor.catalog.listings import ListingError
from deeptutor.multi_user.context import get_current_user

router = APIRouter()

T = TypeVar("T")


async def _call(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    try:
        return await asyncio.to_thread(function, *args, **kwargs)
    except (ListingError, EnrollmentError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


class ListingBody(BaseModel):
    title: str = Field(default="", max_length=200)
    summary: str = Field(default="", max_length=2000)
    stages: list[str] = Field(default_factory=list, max_length=10)
    subjects: list[str] = Field(default_factory=list, max_length=10)
    difficulty: str = Field(default="", max_length=40)
    tags: list[str] = Field(default_factory=list, max_length=20)


class ReasonBody(BaseModel):
    reason: str = Field(default="", max_length=1000)


def _listings():
    from deeptutor.catalog.listings import ListingService

    return ListingService()


# ── browsing ───────────────────────────────────────────────


@router.get("/taxonomy")
async def taxonomy() -> dict[str, Any]:
    from deeptutor.catalog.taxonomy import tree

    return tree()


@router.get("/items")
async def items(
    q: str = Query(default="", max_length=100),
    stage: str = Query(default="", max_length=2000),
    subject: str = Query(default="", max_length=2000),
    kind: str = Query(default="", max_length=200),
    difficulty: str = Query(default="", max_length=200),
    tag: str = Query(default="", max_length=2000),
    author: str = Query(default="", max_length=2000),
    sort: str = Query(default="newest", pattern="^(newest|popular)$"),
    page: int = Query(default=1, ge=1, le=10_000),
) -> dict[str, Any]:
    from deeptutor.catalog.market import Filters, parse_list, search

    filters = Filters(
        q=q, stages=parse_list(stage), subjects=parse_list(subject), kinds=parse_list(kind),
        difficulties=parse_list(difficulty), tags=parse_list(tag), authors=parse_list(author),
        sort=sort, page=page,
    )
    return await _call(search, get_current_user(), filters)


@router.get("/items/{kind}/{item_id}")
async def item(kind: str, item_id: str) -> dict[str, Any]:
    from deeptutor.catalog.market import detail

    return await _call(detail, get_current_user(), kind, item_id)


@router.post("/items/{kind}/{item_id}/join")
async def join(kind: str, item_id: str) -> dict[str, Any]:
    from deeptutor.catalog.enrollment import join as join_item

    return await _call(join_item, get_current_user(), kind, item_id)


@router.delete("/items/{kind}/{item_id}/join")
async def leave(kind: str, item_id: str) -> dict[str, Any]:
    from deeptutor.catalog.enrollment import leave as leave_item

    return await _call(leave_item, get_current_user(), kind, item_id)


@router.get("/authors/{author_id}")
async def author(author_id: str) -> dict[str, Any]:
    from deeptutor.catalog.market import author_page

    return await _call(author_page, get_current_user(), author_id)


# ── the author ─────────────────────────────────────────────


@router.get("/listings/mine")
async def my_listings() -> dict[str, Any]:
    return {"listings": await _call(_listings().mine)}


@router.get("/listings/{kind}/{item_id}")
async def get_listing(kind: str, item_id: str) -> dict[str, Any]:
    return await _call(_listings().get, kind, item_id)


@router.put("/listings/{kind}/{item_id}")
async def save_listing(kind: str, item_id: str, body: ListingBody) -> dict[str, Any]:
    return await _call(
        _listings().save, kind, item_id, title=body.title, summary=body.summary, stages=body.stages,
        subjects=body.subjects, difficulty=body.difficulty, tags=body.tags,
    )


@router.post("/listings/{kind}/{item_id}/submit")
async def submit_listing(kind: str, item_id: str) -> dict[str, Any]:
    return await _call(_listings().submit, kind, item_id)


@router.post("/listings/{kind}/{item_id}/withdraw-request")
async def cancel_listing(kind: str, item_id: str) -> dict[str, Any]:
    return await _call(_listings().cancel, kind, item_id)


@router.post("/listings/{kind}/{item_id}/unlist")
async def unlist(kind: str, item_id: str) -> dict[str, Any]:
    return await _call(_listings().unlist, kind, item_id)


@router.post("/listings/{kind}/{item_id}/relist")
async def relist(kind: str, item_id: str) -> dict[str, Any]:
    return await _call(_listings().relist, kind, item_id)


# ── administrators ─────────────────────────────────────────


@router.get("/review")
async def review_queue() -> dict[str, Any]:
    return {"listings": await _call(_listings().review_queue)}


@router.get("/review/listed")
async def review_listed(q: str = Query(default="", max_length=100)) -> dict[str, Any]:
    return {"listings": await _call(_listings().listed, q)}


@router.get("/review/{kind}/{item_id}")
async def review_item(kind: str, item_id: str) -> dict[str, Any]:
    return await _call(_listings().review_item, kind, item_id)


@router.post("/review/{kind}/{item_id}/approve")
async def approve(kind: str, item_id: str) -> dict[str, Any]:
    return await _call(_listings().approve, kind, item_id)


@router.post("/review/{kind}/{item_id}/reject")
async def reject(kind: str, item_id: str, body: ReasonBody) -> dict[str, Any]:
    return await _call(_listings().reject, kind, item_id, body.reason)


@router.post("/review/{kind}/{item_id}/take-down")
async def take_down(kind: str, item_id: str, body: ReasonBody) -> dict[str, Any]:
    return await _call(_listings().take_down, kind, item_id, body.reason)
