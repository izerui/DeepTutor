"""Tasks API (spec 2026-11-03 batch 1 §3)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from typing import Any, Literal, TypeVar

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from deeptutor.publishing.scope import ScopeError
from deeptutor.tasks.service import LearnerTasks, TaskError, TaskService

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


class TaskCreate(BaseModel):
    kind: Literal["mastery_path", "practice_set", "book", "note"]
    title: str = Field(min_length=1, max_length=200)


class TaskUpdate(BaseModel):
    # Tasks have no deadline or opening time (spec 2026-11-05 remove-teacher-management §1.4):
    # ``due_at`` / ``open_at`` are refused, not dropped.
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)
    target_id: str | None = Field(default=None, max_length=200)
    settings: dict[str, Any] | None = None


class CourseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class CourseUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=20_000)
    task_ids: list[str] | None = Field(default=None, max_length=200)


class HandOver(BaseModel):
    teacher_id: str = Field(min_length=1, max_length=200)


class CoursePracticeSet(BaseModel):
    set_id: str = Field(min_length=1, max_length=200)


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


@router.post("/courses/{course_id}/hand-over")
async def hand_over_course(course_id: str, body: HandOver):
    """Traceability #5: an administrator gives a course they hold to a learner."""
    return {"course": await _call(lambda: _courses().hand_over(course_id, body.teacher_id))}


@router.post("/{task_id}/hand-over")
async def hand_over_task(task_id: str, body: HandOver):
    return {"task": await _call(lambda: TaskService().hand_over(task_id, body.teacher_id))}


@router.post("/courses/{course_id}/practice-sets")
async def add_course_practice_set(course_id: str, body: CoursePracticeSet):
    """Follow-ups §3: a published practice set becomes the course's next task."""
    return {"course": await _call(lambda: _courses().add_practice_set(course_id, body.set_id))}


@router.post("/courses/{course_id}/publish")
async def publish_course(course_id: str):
    return {"course": await _call(lambda: _courses().publish(course_id))}


@router.post("/courses/{course_id}/archive")
async def archive_course(course_id: str):
    return {"course": await _call(lambda: _courses().archive(course_id))}


@router.delete("/courses/{course_id}")
async def delete_course(course_id: str):
    await _call(lambda: _courses().delete(course_id))
    return {"ok": True}


# ── authoring ──────────────────────────────────────────────


@router.get("/mine")
async def my_tasks():
    return {"tasks": await _call(TaskService().list_mine)}


@router.post("")
async def create_task(body: TaskCreate):
    return {"task": await _call(TaskService().create, body.kind, body.title)}


@router.get("/assigned")
async def assigned_tasks():
    return {"tasks": await _call(LearnerTasks().assigned)}


@router.get("/assigned/{key:path}")
async def assigned_task(key: str):
    return {"task": await _call(LearnerTasks().get, key)}


@router.post("/assigned/{task_id}/done")
async def mark_done(task_id: str):
    return {"task": await _call(LearnerTasks().mark_done, task_id)}


@router.get("/{task_id}")
async def get_task(task_id: str):
    return {"task": await _call(TaskService().detail, task_id)}


@router.put("/{task_id}")
async def update_task(task_id: str, body: TaskUpdate):
    fields = body.model_dump(exclude_unset=True)
    return {"task": await _call(TaskService().update, task_id, **fields)}


@router.post("/{task_id}/publish")
async def publish_task(task_id: str):
    return {"task": await _call(TaskService().publish, task_id)}


@router.post("/{task_id}/archive")
async def archive_task(task_id: str):
    return {"task": await _call(TaskService().archive, task_id)}


@router.delete("/{task_id}")
async def delete_task(task_id: str):
    await _call(TaskService().delete, task_id)
    return {"ok": True}
