"""Teacher comments API (spec 2026-11-03 batch 2 §3)."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from deeptutor.comments import service

router = APIRouter()
Kind = Literal["practice_submission", "path_point"]


class CommentWrite(BaseModel):
    kind: Kind
    ref: str = Field(min_length=3, max_length=300)
    student_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=5000)


async def _call(function, *args):
    try:
        return await asyncio.to_thread(function, *args)
    except service.CommentError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.get("/mine")
async def my_comments(kind: Kind, subject: str = Query(min_length=1, max_length=200)):
    """The current student's comments on one practice set or Mastery Path."""
    return {"comments": await _call(service.for_student, kind, subject)}


@router.get("/written")
async def written(kind: Kind, subject: str = Query(min_length=1, max_length=200),
                  student_id: str = Query(min_length=1, max_length=64)):
    return {"comments": await _call(service.written, kind, subject, student_id)}


@router.put("")
async def write_comment(body: CommentWrite):
    return {"comment": await _call(service.write, body.kind, body.ref, body.student_id, body.text)}


@router.delete("/{comment_id}")
async def delete_comment(comment_id: str):
    await _call(service.delete, comment_id)
    return {"ok": True}
