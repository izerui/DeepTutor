"""A student's remedial practice from their own mistakes (spec 2026-11-03 batch 3 §3.2)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()


class SimilarRequest(BaseModel):
    entry_ids: list[int] = Field(min_length=1, max_length=20)
    count: int = Field(default=5, ge=3, le=10)
    language: str = Field(default="zh", max_length=10)


@router.post("/similar")
async def similar(body: SimilarRequest) -> dict:
    from deeptutor.services.llm.usage_limit import AiLimitReached
    from deeptutor.services.practice.remedial import RemedialError, similar_questions

    try:
        return await similar_questions(body.entry_ids, body.count, language=body.language)
    except RemedialError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except AiLimitReached as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
