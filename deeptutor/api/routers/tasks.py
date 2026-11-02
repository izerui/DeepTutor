"""Tasks and homework API (spec 2026-11-03 batch 1 §3–§4)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
from typing import Any, Literal, TypeVar

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from deeptutor.api.routers.publishing import ScopeChoice, chosen
from deeptutor.publishing.scope import ScopeError
from deeptutor.tasks.service import MAX_FILES, StudentTasks, TaskError, TaskService

logger = logging.getLogger(__name__)
router = APIRouter()
T = TypeVar("T")


async def _call(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    try:
        return await asyncio.to_thread(function, *args, **kwargs)
    except TaskError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except ScopeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


async def _await(call: Awaitable[T]) -> T:
    try:
        return await call
    except TaskError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


class TaskCreate(BaseModel):
    kind: Literal["mastery_path", "practice_set", "book", "homework", "note"]
    title: str = Field(min_length=1, max_length=200)


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)
    target_id: str | None = Field(default=None, max_length=200)
    settings: dict[str, Any] | None = None
    #: ``None`` clears the deadline; leave the key out to keep it.
    due_at: float | None = None


class TaskPublish(BaseModel):
    #: First publication: who receives it (spec 2026-11-02 §4.1).
    scope: ScopeChoice | None = None


class Grade(BaseModel):
    score: float
    comment: str = Field(default="", max_length=20_000)


class SendBack(BaseModel):
    reason: str = Field(min_length=1, max_length=20_000)


# ── authoring ──────────────────────────────────────────────


@router.get("/mine")
async def my_tasks():
    return {"tasks": await _call(TaskService().list_mine)}


@router.post("")
async def create_task(body: TaskCreate):
    return {"task": await _call(TaskService().create, body.kind, body.title)}


@router.get("/assigned")
async def assigned_tasks():
    return {"tasks": await _call(StudentTasks().assigned)}


@router.get("/assigned/{key:path}")
async def assigned_task(key: str):
    return {"task": await _call(StudentTasks().get, key)}


@router.post("/assigned/{task_id}/done")
async def mark_done(task_id: str):
    return {"task": await _call(StudentTasks().mark_done, task_id)}


@router.post("/assigned/{task_id}/submit")
async def submit_homework(
    task_id: str,
    text: str = Form(default=""),
    files: list[UploadFile] = File(default_factory=list),
):
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Attach at most {MAX_FILES} files.")
    payload = [(f.filename or "file", await f.read()) for f in files]
    return {"task": await _await(StudentTasks().submit(task_id, text, payload))}


@router.get("/{task_id}")
async def get_task(task_id: str):
    return {"task": await _call(TaskService().detail, task_id)}


@router.put("/{task_id}")
async def update_task(task_id: str, body: TaskUpdate):
    fields = body.model_dump(exclude_unset=True)
    return {"task": await _call(TaskService().update, task_id, **fields)}


@router.post("/{task_id}/publish")
async def publish_task(task_id: str, body: TaskPublish | None = None):
    with chosen(body.scope if body else None):
        return {"task": await _call(TaskService().publish, task_id)}


@router.post("/{task_id}/archive")
async def archive_task(task_id: str):
    return {"task": await _call(TaskService().archive, task_id)}


@router.delete("/{task_id}")
async def delete_task(task_id: str):
    await _call(TaskService().delete, task_id)
    return {"ok": True}


@router.post("/{task_id}/students/{student_id}/grade")
async def grade(task_id: str, student_id: str, body: Grade):
    return {"task": await _call(TaskService().grade, task_id, student_id, body.score, body.comment)}


@router.post("/{task_id}/students/{student_id}/send-back")
async def send_back(task_id: str, student_id: str, body: SendBack):
    return {"task": await _call(TaskService().send_back, task_id, student_id, body.reason)}


@router.get("/{task_id}/students/{student_id}/files/{name}")
async def homework_file(task_id: str, student_id: str, name: str):
    path = await _call(TaskService().file_path, task_id, student_id, name)
    return FileResponse(path, filename=name)
