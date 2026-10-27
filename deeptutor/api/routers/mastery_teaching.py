"""Teacher endpoints for mastery templates: publish, assign, revoke, archive.

Every route requires a teacher or an admin (``require_teacher_or_admin`` reads
the live role). Ownership is enforced by :class:`TeachingService`.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from deeptutor.api.routers.auth import require_teacher_or_admin
import deeptutor.teaching.access  # noqa: F401  (registers the "mastery_template" reader)
from deeptutor.teaching.models import PublishRequest
from deeptutor.teaching.service import TeachingError, TeachingService

#: The only routes a teacher-path context opens (R9): original content of a
#: KB (file, extracted text, verified visual asset, rendered source page) and
#: of a book (the book, its chapters, its pages). Config, KB progress, linked
#: folders, sync sources and every write keep their normal rules, except the
#: reader's own book state in ``LEARNER_STATE_ROUTES`` (B2-17). Matched by
#: suffix of the resolved route template, so the include prefix does not matter.
ORIGINAL_CONTENT_ROUTES: frozenset[str] = frozenset(
    {
        "/knowledge-bases/{kb_name}/files/{filename:path}",
        "/knowledge-bases/{kb_name}/file-preview-text/{filename:path}",
        "/knowledge-bases/{kb_name}/visual-assets/{asset_id}",
        "/knowledge-bases/{kb_name}/source-page",
        "/books/{book_id}",
        "/books/{book_id}/spine",
        "/books/{book_id}/pages/{page_id}",
    }
)
#: R2 (B2-17): the reader's OWN state on a path book — reading progress,
#: bookmarks, in-book quiz attempts, learning captures and page-chat bindings. The same entitlement
#: as reading the book opens them; the endpoints then resolve the canonical
#: book, whose learning store for a non-owner is the reader's own overlay
#: (``BookLearningOverlay``), never the owner's book directory. Method-exact:
#: no other write on these paths is opened.
LEARNER_STATE_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/books/progress/visit"),
        ("POST", "/books/progress/bookmark"),
        ("POST", "/books/quiz-attempt"),
        ("GET", "/books/{book_id}/learning-captures"),
        ("POST", "/books/{book_id}/learning-captures"),
        ("PATCH", "/books/{book_id}/learning-captures/{capture_id}"),
        # The reader's own page-chat binding (overlay ``page_chat_sessions``).
        ("POST", "/books/page-chat-session"),
    }
)
MASTERY_TEMPLATE_PARAM = "mastery_template"
MASTERY_TEMPLATE_HEADER = "x-deeptutor-mastery-template"
#: Optional query parameter naming a cited document by its fixed id.
DOCUMENT_ID_PARAM = "doc_id"

async def enter_mastery_template_scope(request: Request) -> None:
    """R9: a student opens a teacher path's originals from the Study page.

    A GET on an original-content route that names the path
    (``?mastery_template=<id>`` or the ``X-DeepTutor-Mastery-Template``
    header) is authorized against the revision the caller's copy is on:
    a library document must be one that revision cites (followed by its fixed
    id to wherever it lives now; ``access.original_document`` carries the
    live path to the endpoint), a book must be one of its books. Only
    then is the request served inside the template's reference access — and,
    for a book or a book's library, inside plan D's reading scope of the
    server-resolved canonical book. On any other route the parameter is
    ignored. The reader's own state on a path book (``LEARNER_STATE_ROUTES``,
    B2-17) gets the same book check, with the book named by the path or, for
    the body-addressed routes, by the request body's ``book_id``. Declared
    ``async`` so the context variables are set in the endpoint's own context,
    like ``require_auth`` sets the user.
    """
    template_id = str(
        request.query_params.get(MASTERY_TEMPLATE_PARAM)
        or request.headers.get(MASTERY_TEMPLATE_HEADER)
        or ""
    ).strip()
    if not template_id:
        return
    from deeptutor.api.routers.auth import _resolved_route_path

    method = request.method.upper()
    route = _resolved_route_path(request) or ""
    learner_state = any(
        method == allowed and route.endswith(template)
        for allowed, template in LEARNER_STATE_ROUTES
    )
    original_content = method == "GET" and any(
        route.endswith(template) for template in ORIGINAL_CONTENT_ROUTES
    )
    if not (learner_state or original_content):
        return
    params = request.path_params
    book_ref = str(params.get("book_id") or "")
    if learner_state and not book_ref:
        book_ref = await _body_book_id(request)
        if not book_ref:
            return
    from deeptutor.teaching import access

    materials = await asyncio.to_thread(access.entitled_materials, template_id)
    if materials is None:
        # Path closed / not assigned / archived — ignore the template parameter
        # and fall back to the route's normal authorization rules (B2-12).
        return
    try:
        if book_ref:
            grant = await asyncio.to_thread(access.book_original_grant, materials, book_ref)
        else:
            grant = await asyncio.to_thread(
                access.kb_original_grant,
                materials,
                str(params.get("kb_name") or ""),
                document=str(
                    params.get("filename") or request.query_params.get("source_path") or ""
                ),
                doc_id=str(request.query_params.get(DOCUMENT_ID_PARAM) or "").strip(),
                asset_id=str(params.get("asset_id") or ""),
            )
    except access.OriginalDenied as exc:
        # Same answer as an unreadable library: nothing to probe.
        raise HTTPException(status_code=404, detail="File not found") from exc
    if grant is None:
        return
    if grant.kb_id:
        access.entitled_kb_id.set(grant.kb_id)
    if grant.studying:
        access.active_mastery_template.set(template_id)
    if grant.books:
        from deeptutor.book import registry as book_registry

        book_registry.bind_reading(grant.books)
    if grant.rel_path:
        access.original_document.set(grant.rel_path)
    if grant.book_id:
        access.original_book.set(grant.book_id)


async def _body_book_id(request: Request) -> str:
    """The ``book_id`` of a body-addressed learner-state request (B2-17).

    FastAPI has already read the body for the endpoint and caches it on this
    request, so the endpoint validates the very same value.
    """
    try:
        body = await request.json()
    except Exception:
        return ""
    value = body.get("book_id") if isinstance(body, dict) else None
    return value.strip() if isinstance(value, str) else ""


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
