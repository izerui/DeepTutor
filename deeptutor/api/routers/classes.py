"""Classes API (spec 2026-10-29 §5): management, invitations and invite links."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import io
from typing import Any, TypeVar

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from deeptutor.classes import org
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
    except org.OrgError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


class OrgName(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class TeacherDepartments(BaseModel):
    department_ids: list[str] = Field(default_factory=list, max_length=100)


class AnnouncementPost(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    pinned: bool = False


class AnnouncementUpdate(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=5000)
    pinned: bool | None = None


class ClassCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ClassUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    archived: bool | None = None
    #: Spec 2026-11-03 §0: "" clears it; leave out to keep it.
    department_id: str | None = Field(default=None, max_length=64)
    grade_id: str | None = Field(default=None, max_length=64)


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
    allow_personal_book: bool | None = None
    allow_private_kb: bool | None = None
    # Spec 2026-11-03 batch 3 §7.1 and §8.
    allow_personal_course: bool | None = None
    allow_personal_task_card: bool | None = None
    allow_partners: bool | None = None
    allow_agents: bool | None = None
    allow_co_writer: bool | None = None
    #: Tokens per student per day; 0 is no limit.
    ai_daily_tokens: int | None = Field(default=None, ge=0, le=100_000_000)


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


# ── announcements (spec 2026-11-03 batch 2 §4) ─────────────


@router.put("/announcements/{announcement_id}")
async def update_announcement(announcement_id: str, payload: AnnouncementUpdate) -> dict:
    from deeptutor.classes.announcements import AnnouncementService

    return {"announcement": await _call(
        lambda: AnnouncementService().update(announcement_id, text=payload.text, pinned=payload.pinned)
    )}


@router.delete("/announcements/{announcement_id}")
async def delete_announcement(announcement_id: str) -> dict:
    from deeptutor.classes.announcements import AnnouncementService

    await _call(lambda: AnnouncementService().delete(announcement_id))
    return {"ok": True}


@router.get("/{class_id}/announcements")
async def class_announcements(class_id: str) -> dict:
    from deeptutor.classes.announcements import AnnouncementService

    return {"announcements": await _call(lambda: AnnouncementService().list_for_class(class_id))}


@router.post("/{class_id}/announcements")
async def post_announcement(class_id: str, payload: AnnouncementPost) -> dict:
    from deeptutor.classes.announcements import AnnouncementService

    return {"announcement": await _call(lambda: AnnouncementService().post(class_id, payload.text, payload.pinned))}


# ── departments and grades (spec 2026-11-03 batch 2 §0) ───


@router.get("/org")
async def org_lists() -> dict:
    return await _call(org.lists)


# Before ``/org/{kind}/…``, which would otherwise take ``teachers`` as a kind.
@router.get("/org/teachers")
async def org_teachers() -> dict:
    """Administrators: which departments each teacher is listed under."""
    from deeptutor.multi_user.context import get_current_user

    if not get_current_user().is_admin:
        raise HTTPException(status_code=403, detail="Only administrators manage departments and grades.")
    from deeptutor.classes.service import _accounts

    people = [
        {"id": user_id, "username": account["username"]}
        for user_id, account in sorted(_accounts().items(), key=lambda item: item[1]["username"])
        if account["role"] == "teacher" and not account["disabled"]
    ]
    return {"teachers": await _call(org.teacher_departments), "people": people}


@router.put("/org/teachers/{user_id}")
async def org_set_teacher(user_id: str, payload: TeacherDepartments) -> dict:
    await _call(org.set_teacher_departments, user_id, payload.department_ids)
    return {"teachers": await _call(org.teacher_departments)}


@router.post("/org/{kind}")
async def org_create(kind: str, payload: OrgName) -> dict:
    return {"item": await _call(org.create, kind, payload.name)}


@router.put("/org/{kind}/{item_id}")
async def org_rename(kind: str, item_id: str, payload: OrgName) -> dict:
    await _call(org.rename, kind, item_id, payload.name)
    return await _call(org.lists)


@router.delete("/org/{kind}/{item_id}")
async def org_delete(kind: str, item_id: str) -> dict:
    await _call(org.delete, kind, item_id)
    return await _call(org.lists)


@router.get("/{class_id}")
async def get_class(class_id: str) -> dict:
    return {"class": await _call(lambda: ClassService().get(class_id))}


@router.patch("/{class_id}")
async def update_class(class_id: str, payload: ClassUpdate) -> dict:
    return {"class": await _call(
        lambda: ClassService().update(
            class_id, name=payload.name, archived=payload.archived,
            department_id=payload.department_id, grade_id=payload.grade_id,
        )
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
async def class_report(class_id: str, days: int | None = None) -> dict:
    """``days`` = 7 or 30 limits the period numbers (batch 3 §6.3)."""
    from deeptutor.classes.report import ReportService

    return await _call(lambda: ReportService().class_report(class_id, days))


@router.get("/{class_id}/report/paths/{template_id}")
async def class_path_points(class_id: str, template_id: str) -> dict:
    from deeptutor.classes.report import ReportService

    return await _call(lambda: ReportService().class_path_points(class_id, template_id))


@router.get("/{class_id}/report/export")
async def export_class_report(class_id: str, days: int | None = None):
    from fastapi.responses import Response

    from deeptutor.classes.report import ReportService

    text = await _call(lambda: ReportService().export_csv(class_id, days))
    return Response(
        content="\ufeff" + text, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{class_id}-report.csv"'},
    )


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
