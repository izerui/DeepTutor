"""Practice set API (spec 2026-10-29 §8): authoring, answering, the teacher's report."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypeVar

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from deeptutor.api.routers.publishing import ScopeChoice, chosen
from deeptutor.publishing.scope import ScopeError

from deeptutor.practice_sets.service import PracticeError, PracticeSetService

router = APIRouter()
logger = logging.getLogger(__name__)

T = TypeVar("T")


async def _call(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    try:
        return await asyncio.to_thread(function, *args, **kwargs)
    except PracticeError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except ScopeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


async def _await(call: Awaitable[T]) -> T:
    try:
        return await call
    except PracticeError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


class SetCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class SetUpdate(BaseModel):
    # No deadline any more (spec 2026-11-05 remove-teacher-management §1.4): ``due_at`` /
    # ``clear_due`` are refused, not dropped; answers show right after submitting.
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    questions: list[dict[str, Any]] | None = Field(default=None, max_length=200)
    #: Accepted from older clients: answers are always shown after submitting.
    reveal: Literal["after_submit"] | None = None
    #: Batch 3 §6.1: at most this many attempts; ``clear_attempts_limit`` lifts it.
    attempts_limit: int | None = Field(default=None, ge=1, le=100)
    clear_attempts_limit: bool = False
    score_mode: str | None = None


class GenerateRequest(BaseModel):
    #: May be empty when a book is chosen: its chapter titles are the topic.
    topic: str = Field(default="", max_length=2000)
    count: int = Field(default=5, ge=1, le=20)
    kb_ref: str | None = Field(default=None, max_length=300)
    question_types: list[str] = Field(default_factory=list, max_length=8)
    difficulty: str = Field(default="", max_length=20)
    language: str = Field(default="zh", max_length=10)
    #: Batch 3 §4: questions from a book (``book:<id>``), whole or some chapters.
    book_ref: str | None = Field(default=None, max_length=300)
    chapter_ids: list[str] = Field(default_factory=list, max_length=200)


class FromBankRequest(BaseModel):
    entry_ids: list[int] = Field(min_length=1, max_length=200)


class SubmitRequest(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)
    started_at: float | None = None
    #: The version the student opened; answers are graded against it.
    revision: int | None = None


# ── authoring ──────────────────────────────────────────────


@router.get("/withdrawable")
async def withdrawable_sets():
    """Administrators: other people's published sets, which they may only withdraw (D10)."""
    return {"sets": await _call(PracticeSetService().withdrawable)}


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
    attempts: Any = None if body.clear_attempts_limit else (
        body.attempts_limit if body.attempts_limit is not None else ""
    )
    return {
        "set": await _call(
            PracticeSetService().update, set_id, title=body.title, description=body.description,
            questions=body.questions,
            attempts_limit=attempts, score_mode=body.score_mode,
        )
    }


class PublishBody(BaseModel):
    #: First publication (spec 2026-11-02 §4.1): it names nobody; people join.
    scope: ScopeChoice | None = None


@router.post("/{set_id}/publish")
async def publish_set(set_id: str, body: PublishBody | None = None):
    with chosen(body.scope if body else None):
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
            book_ref=body.book_ref, chapter_ids=body.chapter_ids,
        ))
    }


@router.post("/from-bank")
async def questions_from_bank(body: FromBankRequest):
    return {"questions": await _await(PracticeSetService().from_bank(body.entry_ids))}


@router.get("/{set_id}/report")
async def set_report(set_id: str):
    return await _call(PracticeSetService().report, set_id)


# ── the author's review (batch 3 §6.1) ─────────────────────


@router.get("/{set_id}/export")
async def export_results(set_id: str):
    from fastapi.responses import Response

    from deeptutor.practice_sets.review import PracticeReview

    text = await _call(PracticeReview().export_csv, set_id)
    # A BOM so spreadsheet programs read the names as UTF-8.
    return Response(
        content="\ufeff" + text, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{set_id}.csv"'},
    )


@router.get("/{set_id}/submissions/{submission_id}")
async def review_attempt(set_id: str, submission_id: str):
    from deeptutor.practice_sets.review import PracticeReview

    return {"attempt": await _call(PracticeReview().attempt, set_id, submission_id)}


# ── answering ──────────────────────────────────────────────


@router.get("/{set_id}/take")
async def take_set(set_id: str):
    service = PracticeSetService()
    view = await _call(service.take, set_id)
    try:
        view = await service.with_my_answers(view)
    except Exception:
        # The results stand without them; the student can still answer again.
        logger.exception("Could not read practice set %s answers from the question bank", set_id)
    if view["submissions"]:
        # Answers withheld from the student's bank until now are filled in
        # (it checks the reveal time itself, for every version submitted).
        try:
            await service.reveal_in_bank(set_id)
        except Exception:
            logger.exception("Could not complete practice set %s answers in the question bank", set_id)
    return {"set": view}


@router.post("/{set_id}/submit")
async def submit_set(set_id: str, body: SubmitRequest):
    return await _await(PracticeSetService().submit(
        set_id, body.answers, started_at=body.started_at, revision=body.revision,
    ))
