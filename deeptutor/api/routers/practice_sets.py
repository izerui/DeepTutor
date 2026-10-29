"""Practice set API (spec 2026-10-29 §8): authoring, answering, the teacher's report."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from deeptutor.practice_sets.service import PracticeError, PracticeSetService

router = APIRouter()

T = TypeVar("T")


async def _call(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    try:
        return await asyncio.to_thread(function, *args, **kwargs)
    except PracticeError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


async def _await(call: Awaitable[T]) -> T:
    try:
        return await call
    except PracticeError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


class SetCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class SetUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    questions: list[dict[str, Any]] | None = Field(default=None, max_length=200)
    #: Seconds since the epoch; ``null`` removes the deadline; absent keeps it.
    due_at: float | None = None
    clear_due: bool = False
    reveal: str | None = None


class GenerateRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=2000)
    count: int = Field(default=5, ge=1, le=20)
    kb_ref: str | None = Field(default=None, max_length=300)
    question_types: list[str] = Field(default_factory=list, max_length=8)
    difficulty: str = Field(default="", max_length=20)
    language: str = Field(default="zh", max_length=10)


class FromBankRequest(BaseModel):
    entry_ids: list[int] = Field(min_length=1, max_length=200)


class SubmitRequest(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)
    started_at: float | None = None


# ── authoring ──────────────────────────────────────────────


@router.get("/mine")
async def list_mine():
    return {"sets": await _call(PracticeSetService().list_mine)}


@router.post("")
async def create_set(body: SetCreate):
    return {"set": await _call(PracticeSetService().create, body.title)}


@router.get("/assigned")
async def list_assigned():
    return {"sets": await _call(PracticeSetService().assigned)}


@router.get("/{set_id}")
async def get_set(set_id: str):
    return {"set": await _call(PracticeSetService().get, set_id)}


@router.put("/{set_id}")
async def update_set(set_id: str, body: SetUpdate):
    due: Any = None if body.clear_due else body.due_at if body.due_at is not None else ""
    return {
        "set": await _call(
            PracticeSetService().update, set_id, title=body.title, description=body.description,
            questions=body.questions, due_at=due, reveal=body.reveal,
        )
    }


@router.post("/{set_id}/publish")
async def publish_set(set_id: str):
    return {"set": await _call(PracticeSetService().publish, set_id)}


@router.post("/{set_id}/archive")
async def archive_set(set_id: str):
    return {"set": await _call(PracticeSetService().archive, set_id)}


@router.delete("/{set_id}")
async def delete_set(set_id: str):
    await _call(PracticeSetService().delete, set_id)
    return {"ok": True}


@router.post("/generate")
async def generate_questions(body: GenerateRequest):
    return {
        "questions": await _await(PracticeSetService().generate(
            topic=body.topic, count=body.count, kb_ref=body.kb_ref,
            question_types=body.question_types, difficulty=body.difficulty, language=body.language,
        ))
    }


@router.post("/from-bank")
async def questions_from_bank(body: FromBankRequest):
    return {"questions": await _await(PracticeSetService().from_bank(body.entry_ids))}


@router.get("/{set_id}/report")
async def set_report(set_id: str):
    return await _call(PracticeSetService().report, set_id)


# ── answering ──────────────────────────────────────────────


@router.get("/{set_id}/take")
async def take_set(set_id: str):
    return {"set": await _call(PracticeSetService().take, set_id)}


@router.post("/{set_id}/submit")
async def submit_set(set_id: str, body: SubmitRequest):
    return await _await(PracticeSetService().submit(set_id, body.answers, started_at=body.started_at))
