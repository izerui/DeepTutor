"""Classes API (spec 2026-10-29 §5): management, invitations and invite links."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import io
from typing import Any, TypeVar

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from deeptutor.classes.service import ClassError, ClassService, link_info

router = APIRouter()
#: Mounted without authentication: an invite page shows the class name before sign-in.
public_router = APIRouter()

T = TypeVar("T")


async def _call(function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a store-backed call off the event loop and map class errors to HTTP."""
    try:
        return await asyncio.to_thread(function, *args, **kwargs)
    except ClassError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


class ClassCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ClassUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    archived: bool | None = None


class PersonRef(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)


class StudentRef(BaseModel):
    student_id: str = Field(min_length=1, max_length=64)


class StudentIds(BaseModel):
    student_ids: list[str] = Field(min_length=1, max_length=500)


class InviteLinkRequest(BaseModel):
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


class JoinRequest(BaseModel):
    token: str = Field(min_length=1, max_length=128)


class StudentPolicyUpdate(BaseModel):
    allow_personal_mastery: bool | None = None
    allow_private_kb: bool | None = None


class PasswordReset(BaseModel):
    new_password: str = Field(min_length=8, max_length=256)


# ── static paths first, so they are not taken for a class id ─────────


@router.get("")
async def list_classes(include_archived: bool = False) -> dict:
    return {"classes": await _call(lambda: ClassService().list_classes(include_archived=include_archived))}


@router.post("")
async def create_class(payload: ClassCreate) -> dict:
    return {"class": await _call(lambda: ClassService().create(payload.name))}


@router.get("/students/search")
async def search_students(q: str = Query(default="", max_length=100)) -> dict:
    return {"students": await _call(lambda: ClassService().search_students(q))}


@router.get("/teachers/search")
async def search_teachers(q: str = Query(default="", max_length=100)) -> dict:
    return {"teachers": await _call(lambda: ClassService().search_teachers(q))}


@router.get("/invitations/mine")
async def my_invitations() -> dict:
    return {"invitations": await _call(lambda: ClassService().my_invitations())}


@router.post("/invitations/{invitation_id}/accept")
async def accept_invitation(invitation_id: str) -> dict:
    await _call(lambda: ClassService().respond(invitation_id, accept=True))
    return {"ok": True}


@router.post("/invitations/{invitation_id}/decline")
async def decline_invitation(invitation_id: str) -> dict:
    await _call(lambda: ClassService().respond(invitation_id, accept=False))
    return {"ok": True}


@router.delete("/invitations/{invitation_id}")
async def cancel_invitation(invitation_id: str) -> dict:
    await _call(lambda: ClassService().cancel_invitation(invitation_id))
    return {"ok": True}


@router.post("/join")
async def join_class(payload: JoinRequest) -> dict:
    return await _call(lambda: ClassService().join_by_link(payload.token))


@router.post("/students/{student_id}/password")
async def reset_student_password(student_id: str, payload: PasswordReset) -> dict:
    await _call(lambda: ClassService().reset_student_password(student_id, payload.new_password))
    return {"ok": True}


@router.get("/qr.svg")
async def qr_code(data: str = Query(min_length=1, max_length=512)) -> Response:
    """An SVG QR code for an invite URL the page already shows."""
    if not data.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Only web links can be encoded.")

    def render() -> bytes:
        import qrcode
        from qrcode.image.svg import SvgPathImage

        buffer = io.BytesIO()
        qrcode.make(data, image_factory=SvgPathImage, box_size=10, border=2).save(buffer)
        return buffer.getvalue()

    svg = await asyncio.to_thread(render)
    return Response(content=svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


# ── one class ────────────────────────────────────────────────────────


@router.get("/{class_id}")
async def get_class(class_id: str) -> dict:
    return {"class": await _call(lambda: ClassService().get(class_id))}


@router.patch("/{class_id}")
async def update_class(class_id: str, payload: ClassUpdate) -> dict:
    return {"class": await _call(
        lambda: ClassService().update(class_id, name=payload.name, archived=payload.archived)
    )}


@router.post("/{class_id}/teachers")
async def add_teacher(class_id: str, payload: PersonRef) -> dict:
    return {"class": await _call(lambda: ClassService().add_teacher(class_id, payload.user_id))}


@router.delete("/{class_id}/teachers/{user_id}")
async def remove_teacher(class_id: str, user_id: str) -> dict:
    return {"class": await _call(lambda: ClassService().remove_teacher(class_id, user_id))}


@router.post("/{class_id}/students")
async def add_students(class_id: str, payload: StudentIds) -> dict:
    return {"class": await _call(lambda: ClassService().add_students(class_id, payload.student_ids))}


@router.delete("/{class_id}/students/{student_id}")
async def remove_student(class_id: str, student_id: str) -> dict:
    return {"class": await _call(lambda: ClassService().remove_student(class_id, student_id))}


@router.post("/{class_id}/leave")
async def leave_class(class_id: str) -> dict:
    await _call(lambda: ClassService().leave(class_id))
    return {"ok": True}


@router.put("/{class_id}/student-policy")
async def set_class_student_policy(class_id: str, payload: StudentPolicyUpdate) -> dict:
    return {"class": await _call(
        lambda: ClassService().set_student_policy(class_id, payload.model_dump(exclude_none=True))
    )}


@router.get("/{class_id}/report")
async def class_report(class_id: str) -> dict:
    from deeptutor.classes.report import ReportService

    return await _call(lambda: ReportService().class_report(class_id))


@router.get("/{class_id}/students/{student_id}/report")
async def student_report(class_id: str, student_id: str) -> dict:
    from deeptutor.classes.report import ReportService

    return await _call(lambda: ReportService().student_report(class_id, student_id))


@router.post("/{class_id}/invitations")
async def invite_student(class_id: str, payload: StudentRef) -> dict:
    return {"invitation": await _call(lambda: ClassService().invite(class_id, payload.student_id))}


@router.get("/{class_id}/invite-link")
async def get_invite_link(class_id: str) -> dict:
    return {"link": await _call(lambda: ClassService().invite_link(class_id))}


@router.post("/{class_id}/invite-link")
async def regenerate_invite_link(class_id: str, payload: InviteLinkRequest) -> dict:
    return {"link": await _call(
        lambda: ClassService().regenerate_link(class_id, expires_in_days=payload.expires_in_days)
    )}


@router.delete("/{class_id}/invite-link")
async def revoke_invite_link(class_id: str) -> dict:
    await _call(lambda: ClassService().revoke_link(class_id))
    return {"ok": True}


@public_router.get("/{token}")
async def invite_link_info(token: str) -> dict:
    return await _call(lambda: link_info(token))
