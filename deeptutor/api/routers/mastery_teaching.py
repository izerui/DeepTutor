"""Teacher endpoints for mastery templates: publish, assign, revoke, archive.

Every route requires a teacher or an admin (``require_teacher_or_admin`` reads
the live role). Ownership is enforced by :class:`TeachingService`.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from deeptutor.api.routers.auth import require_teacher_or_admin
import deeptutor.teaching.access  # noqa: F401  (registers the "mastery_template" reader)
from deeptutor.teaching.models import PublishRequest
from deeptutor.teaching.service import TeachingError, TeachingService

router = APIRouter(dependencies=[Depends(require_teacher_or_admin)])


class RegisterTemplateRequest(BaseModel):
    path_id: str = Field(..., min_length=1, max_length=200)
    workspace_id: str = Field(default="", max_length=200)


class AssignRequest(BaseModel):
    student_ids: list[str] = Field(..., min_length=1, max_length=500)


async def _run(fn, *args: Any, **kwargs: Any) -> Any:
    """Run a service call off the event loop and map its errors to HTTP."""
    try:
        if inspect.iscoroutinefunction(fn):
            return await fn(*args, **kwargs)
        return await asyncio.to_thread(fn, *args, **kwargs)
    except TeachingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/templates")
async def list_templates():
    return {"templates": await _run(TeachingService().list_for_actor)}


@router.post("/templates")
async def register_template(body: RegisterTemplateRequest):
    service = TeachingService()
    template = await _run(service.register, body.path_id.strip(), body.workspace_id.strip())
    return await _run(service.describe, template)


@router.post("/templates/{template_id}/publish/preview")
async def preview_publish(template_id: str):
    return await _run(TeachingService().preview_publish, template_id)


@router.post("/templates/{template_id}/publish")
async def publish_template(template_id: str, body: PublishRequest):
    return await _run(TeachingService().publish, template_id, body)


@router.get("/templates/{template_id}/assignments")
async def list_assignments(template_id: str):
    return {"assignments": await _run(TeachingService().assignments, template_id)}


@router.post("/templates/{template_id}/assignments")
async def assign_students(template_id: str, body: AssignRequest):
    return {"assignments": await _run(TeachingService().assign, template_id, body.student_ids)}


@router.delete("/templates/{template_id}/assignments/{student_id}")
async def revoke_assignment(template_id: str, student_id: str):
    await _run(TeachingService().revoke, template_id, student_id)
    return {"ok": True}


@router.post("/templates/{template_id}/archive")
async def archive_template(template_id: str):
    service = TeachingService()
    template = await _run(service.archive, template_id)
    return await _run(service.describe, template)


@router.delete("/templates/{template_id}")
async def delete_template(template_id: str):
    await _run(TeachingService().delete, template_id)
    return {"ok": True}


@router.get("/students")
async def list_students(q: str = ""):
    """Who a teacher may assign: student accounts, id/username/avatar only (D5)."""
    return {"students": await _run(TeachingService().list_students, q[:100])}
