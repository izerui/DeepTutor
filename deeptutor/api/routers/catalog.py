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


class ClassificationBody(BaseModel):
    stages: list[str] = Field(default_factory=list, max_length=10)
    subjects: list[str] = Field(default_factory=list, max_length=10)
    difficulty: str = Field(default="", max_length=40)
    tags: list[str] = Field(default_factory=list, max_length=20)


@router.put("/review/{kind}/{item_id}/classification")
async def classify(kind: str, item_id: str, body: ClassificationBody) -> dict[str, Any]:
    return await _call(
        _listings().classify, kind, item_id, stages=body.stages, subjects=body.subjects,
        difficulty=body.difficulty, tags=body.tags,
    )


# ── administrators: classification and tags (batch 2 §8) ──


class NodeUpdate(BaseModel):
    name_zh: str | None = Field(default=None, max_length=100)
    name_en: str | None = Field(default=None, max_length=100)
    hidden: bool | None = None


class NodeCreate(BaseModel):
    parent: str = Field(default="", max_length=200)
    name_zh: str = Field(default="", max_length=100)
    name_en: str = Field(default="", max_length=100)


class NodeMove(BaseModel):
    direction: str = Field(pattern="^(up|down)$")


class TagMerge(BaseModel):
    from_tag: str = Field(max_length=100)
    to_tag: str = Field(max_length=100)


def _require_admin() -> None:
    from deeptutor.classes.service import _role

    if _role(get_current_user()) != "admin":
        raise HTTPException(status_code=403, detail="Only administrators manage the classification.")


async def _admin(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    from deeptutor.catalog.tags import TagError
    from deeptutor.catalog.taxonomy import TaxonomyError

    _require_admin()
    try:
        return await asyncio.to_thread(function, *args, **kwargs)
    except (TaxonomyError, TagError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/admin/taxonomy")
async def admin_taxonomy() -> dict[str, Any]:
    from deeptutor.catalog.taxonomy import admin_tree

    return await _admin(admin_tree)


@router.put("/admin/taxonomy/nodes/{node_id}")
async def update_node(node_id: str, body: NodeUpdate) -> dict[str, Any]:
    from deeptutor.catalog.taxonomy import update_node as update

    return await _admin(update, node_id, name_zh=body.name_zh, name_en=body.name_en, hidden=body.hidden)


@router.post("/admin/taxonomy/nodes")
async def add_node(body: NodeCreate) -> dict[str, Any]:
    from deeptutor.catalog.taxonomy import add_node as add

    return await _admin(add, body.parent, body.name_zh, body.name_en)


@router.post("/admin/taxonomy/nodes/{node_id}/move")
async def move_node(node_id: str, body: NodeMove) -> dict[str, Any]:
    from deeptutor.catalog.taxonomy import move_node as move

    return await _admin(move, node_id, body.direction)


@router.delete("/admin/taxonomy/nodes/{node_id}")
async def delete_node(node_id: str) -> dict[str, Any]:
    from deeptutor.catalog.taxonomy import delete_node as delete

    return await _admin(delete, node_id)


@router.get("/admin/tags")
async def admin_tags() -> dict[str, Any]:
    from deeptutor.catalog.tags import list_tags

    return await _admin(list_tags)


@router.post("/admin/tags/merge")
async def merge_tags(body: TagMerge) -> dict[str, Any]:
    from deeptutor.catalog.tags import merge

    return await _admin(merge, body.from_tag, body.to_tag)


@router.delete("/admin/tags/merge/{tag}")
async def unmerge_tag(tag: str) -> dict[str, Any]:
    from deeptutor.catalog.tags import unmerge

    return await _admin(unmerge, tag)
