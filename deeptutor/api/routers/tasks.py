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
    #: Before it, students see only when it opens (spec 2026-11-03 batch 2 §6).
    open_at: float | None = None


class TaskPublish(BaseModel):
    #: First publication: who receives it (spec 2026-11-02 §4.1).
    scope: ScopeChoice | None = None


class CourseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class CourseUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)
    task_ids: list[str] | None = Field(default=None, max_length=200)


class Grade(BaseModel):
    score: float
    comment: str = Field(default="", max_length=20_000)


class SendBack(BaseModel):
    reason: str = Field(min_length=1, max_length=20_000)


# ── courses (spec 2026-11-03 batch 2 §5) ───────────────────


def _courses():
    from deeptutor.tasks.courses import CourseService

    return CourseService()


@router.get("/courses/mine")
async def my_courses():
    return {"courses": await _call(lambda: _courses().list_mine())}


@router.get("/courses/assigned")
async def assigned_courses():
    from deeptutor.tasks.courses import assigned_courses as assigned

    return {"courses": await _call(assigned)}


@router.post("/courses")
async def create_course(body: CourseCreate):
    return {"course": await _call(lambda: _courses().create(body.title))}


@router.get("/courses/{course_id}")
async def get_course(course_id: str):
    return {"course": await _call(lambda: _courses().get(course_id))}


@router.put("/courses/{course_id}")
async def update_course(course_id: str, body: CourseUpdate):
    return {"course": await _call(lambda: _courses().update(
        course_id, title=body.title, description=body.description, task_ids=body.task_ids,
    ))}


@router.post("/courses/{course_id}/tasks")
async def add_course_task(course_id: str, body: TaskCreate):
    return {"course": await _call(lambda: _courses().add_new_task(course_id, body.kind, body.title))}


@router.post("/courses/{course_id}/publish")
async def publish_course(course_id: str, body: TaskPublish | None = None):
    with chosen(body.scope if body else None):
        return {"course": await _call(lambda: _courses().publish(course_id))}


@router.post("/courses/{course_id}/archive")
async def archive_course(course_id: str):
    return {"course": await _call(lambda: _courses().archive(course_id))}


@router.delete("/courses/{course_id}")
async def delete_course(course_id: str):
    await _call(lambda: _courses().delete(course_id))
    return {"ok": True}


# ── calendar (spec 2026-11-03 batch 2 §7) ──────────────────


@router.get("/calendar")
async def calendar():
    """Opening times and deadlines: what reached a student, or what an author assigned."""

    def build() -> list[dict[str, Any]]:
        student = StudentTasks()
        if student._is_student():
            entries = [e for e in student.assigned() if e["state"] != "closed"]
            items = [(e["key"], e["title"], e["kind"], e["state"], e.get("open_at"), e["due_at"]) for e in entries]
        else:
            items = [
                (t["id"], t["title"], t["kind"], t["status"], t["open_at"], t["due_at"])
                for t in TaskService().list_mine() if t["status"] == "published"
            ]
        events = []
        for key, title, kind, state, opens, due in items:
            if opens:
                events.append({"key": key, "title": title, "kind": kind, "state": state, "type": "open", "at": opens})
            if due:
                events.append({"key": key, "title": title, "kind": kind, "state": state, "type": "due", "at": due})
        return sorted(events, key=lambda e: e["at"])

    return {"events": await _call(build)}


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
