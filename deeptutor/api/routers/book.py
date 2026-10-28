"""
Book Engine API Router
======================

REST + WebSocket endpoints for the ``BookEngine``. Phase 1 surface:
create / confirm / compile / read / delete + a per-book event stream.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import ExitStack, contextmanager
import hashlib
import logging
from pathlib import Path
import sqlite3
import time
from typing import Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, Field

from deeptutor.api.routers.mastery_teaching import enter_mastery_template_scope
from deeptutor.api.utils.http_headers import content_disposition
from deeptutor.book import progress as progress_ops
from deeptutor.book import registry as book_registry
from deeptutor.book import sharing as book_sharing
from deeptutor.book.errors import BookPausedError
from deeptutor.book.export import export_filename, render_book_markdown
from deeptutor.book.models import (
    BlockType,
    BookProposal,
    ContentType,
    LearningCapture,
    LearningCaptureStatus,
    Spine,
)
from deeptutor.book.source_access import content_writable
from deeptutor.book.storage import get_book_storage
from deeptutor.book.streaming import SOURCE as BOOK_SOURCE
from deeptutor.multi_user.audit import log_admin_action, log_usage
from deeptutor.multi_user.book_access import (
    ResolvedBook,
    accessible_books,
    can_create_book,
    resolve_book,
)
from deeptutor.multi_user.context import get_current_user
from deeptutor.runtime.stream_bus import StreamBus

router = APIRouter(dependencies=[Depends(enter_mastery_template_scope)])
ws_router = APIRouter()
logger = logging.getLogger(__name__)


def get_book_engine():
    """Resolve the large compilation engine only when a book route uses it."""

    from deeptutor.book.engine import get_book_engine as resolve

    return resolve()


def _book_paused_http(exc: BookPausedError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"code": "book_paused", "message": str(exc)},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Request / response models
# ─────────────────────────────────────────────────────────────────────────────


class CreateBookRequest(BaseModel):
    source_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=32)
    user_intent: str = Field(default="")
    chat_session_id: str = Field(default="")
    chat_selections: list[dict[str, Any]] = Field(default_factory=list)
    notebook_refs: list[dict[str, Any]] = Field(default_factory=list)
    knowledge_bases: list[str] = Field(default_factory=list)
    question_categories: list[int] = Field(default_factory=list)
    question_entries: list[int] = Field(default_factory=list)
    language: str = Field(default="en")
    fallback_language: str = Field(default="en")
    depth: str = Field(default="standard")


class ConfirmProposalRequest(BaseModel):
    book_id: str
    proposal: dict[str, Any] | None = None  # full edited BookProposal payload
    expected_revision: int | None = Field(default=None, ge=1)


class ConfirmSpineRequest(BaseModel):
    book_id: str
    spine: dict[str, Any] | None = None
    auto_compile: bool = True
    block_types: list[str] | None = None
    expected_revision: int | None = Field(default=None, ge=1)


class CompilePageRequest(BaseModel):
    book_id: str
    page_id: str
    force: bool = False
    expected_revision: int | None = Field(default=None, ge=1)


class RegenerateBlockRequest(BaseModel):
    book_id: str
    page_id: str
    block_id: str
    params_override: dict[str, Any] | None = None
    expected_revision: int | None = Field(default=None, ge=1)


class InsertBlockRequest(BaseModel):
    book_id: str
    page_id: str
    block_type: str
    params: dict[str, Any] | None = None
    position: int | None = None
    compile_now: bool = True
    expected_revision: int | None = Field(default=None, ge=1)


class DeleteBlockRequest(BaseModel):
    book_id: str
    page_id: str
    block_id: str
    expected_revision: int | None = Field(default=None, ge=1)


class MoveBlockRequest(BaseModel):
    book_id: str
    page_id: str
    block_id: str
    new_position: int
    expected_revision: int | None = Field(default=None, ge=1)


class ChangeBlockTypeRequest(BaseModel):
    book_id: str
    page_id: str
    block_id: str
    new_type: str
    params_override: dict[str, Any] | None = None
    expected_revision: int | None = Field(default=None, ge=1)


class DeepDiveRequest(BaseModel):
    book_id: str
    parent_page_id: str
    topic: str
    block_id: str | None = None
    content_type: str = "concept"
    expected_revision: int | None = Field(default=None, ge=1)


class QuizAttemptRequest(BaseModel):
    book_id: str
    page_id: str
    block_id: str
    question_id: str = ""
    user_answer: str = ""
    # ``None`` = revealed but not graded (a written answer the reader skipped
    # self-assessing). Distinct from ``False``, which means they got it wrong.
    is_correct: bool | None = None
    submission_id: str = Field(default="", max_length=200)


def _focus_check_question(
    questions: list[dict[str, Any]], requested_id: str, block_id: str
) -> tuple[str, dict[str, Any]] | None:
    """Resolve the durable question behind a Focus-Check submission."""
    for index, question in enumerate(questions):
        if not isinstance(question, dict):
            continue
        question_id = str(question.get("question_id") or "").strip()
        if requested_id and question_id == requested_id:
            return question_id, question
        if not requested_id and not question_id:
            return f"{block_id}:{index + 1}", question
    if requested_id:
        return requested_id, {}
    return None


def _verified_choice_grade(question: dict[str, Any], user_answer: str) -> bool | None:
    """A linked Book answer is trusted only when the stored key can grade it."""
    question_type = str(question.get("question_type") or "").strip().lower().replace(" ", "_")
    if question_type not in {"choice", "multiple_choice", "multiple-choice", "mcq"}:
        return None
    options = question.get("options")
    if not isinstance(options, dict):
        return None
    answer = user_answer.strip().upper()
    keys = {str(key).strip().upper(): str(label).strip() for key, label in options.items()}
    correct_text = str(question.get("correct_answer") or "").strip()
    correct = correct_text.upper()
    if correct not in keys:
        matching = [
            key for key, label in keys.items() if label.casefold() == correct_text.casefold()
        ]
        if len(matching) != 1:
            return None
        correct = matching[0]
    if answer not in keys:
        return None
    return answer == correct


class UpdateBlockRequest(BaseModel):
    book_id: str
    page_id: str
    block_id: str
    title: str | None = None
    body: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)


class ProgressRequest(BaseModel):
    book_id: str
    page_id: str


class SupplementRequest(BaseModel):
    book_id: str
    page_id: str
    topic: str
    expected_revision: int | None = Field(default=None, ge=1)


class PageChatSessionRequest(BaseModel):
    book_id: str
    page_id: str
    session_id: str


class RebuildBookRequest(BaseModel):
    book_id: str
    auto_compile: bool = True
    block_types: list[str] | None = None
    expected_revision: int | None = Field(default=None, ge=1)


class ResumeBookRequest(BaseModel):
    book_id: str
    expected_revision: int | None = Field(default=None, ge=1)


class PauseBookRequest(BaseModel):
    book_id: str
    expected_revision: int | None = Field(default=None, ge=1)


def _auth_enabled() -> bool:
    from deeptutor.services.auth import AUTH_ENABLED

    return bool(AUTH_ENABLED)


def _resolve_book_or_404(
    book_id: str,
    *,
    edit: bool = False,
    delete: bool = False,
) -> ResolvedBook:
    resolved = resolve_book(book_id)
    # Several embedders and focused tests run the router without auth and
    # inject an engine directly. Preserve that single-user extension point.
    if resolved is None and not _auth_enabled() and not book_id.startswith(book_registry.REF_PREFIX):
        engine = get_book_engine()
        if engine.load_book(book_id) is not None:
            storage = getattr(engine, "storage", get_book_storage())
            writable = content_writable(storage)
            resolved = ResolvedBook(
                engine=engine,
                source="own",
                permission="edit" if writable else "read",
                can_edit=writable,
                can_delete=writable,
                learning=storage,
            )
    if resolved is None:
        raise HTTPException(status_code=404, detail="Book not found")
    # Denials deliberately use the same 404 as an unknown id so an untrusted
    # caller cannot probe which shared books exist.
    if edit and not resolved.can_edit:
        raise HTTPException(status_code=404, detail="Book not found")
    if delete and not resolved.can_delete:
        raise HTTPException(status_code=404, detail="Book not found")
    return resolved


def _page_chat_sessions(resolved: ResolvedBook, book: Any) -> dict[str, Any]:
    """The caller's page -> chat session map for ``book``.

    Shared books keep it in the reader's own learning overlay; own books in
    the book's metadata.
    """
    if resolved.is_shared:
        sessions = resolved.learning.load_page_chat_sessions(book.id)
    else:
        metadata = book.metadata if isinstance(book.metadata, dict) else {}
        sessions = metadata.get("page_chat_sessions")
    return sessions if isinstance(sessions, dict) else {}


def _page_chat_session_id(resolved: ResolvedBook, book: Any, page_id: str) -> str:
    """The chat session bound to ``page_id``; own books fall back to the legacy
    book-wide session in ``inputs``."""
    session_id = str(_page_chat_sessions(resolved, book).get(page_id) or "").strip()
    if not session_id and not resolved.is_shared:
        inputs = resolved.engine.storage.load_inputs(book.id)
        session_id = str(inputs.chat_session_id if inputs is not None else "").strip()
    return session_id


def _book_payload(book: Any, resolved: ResolvedBook) -> dict[str, Any]:
    data = book.model_dump(mode="json")
    data.update(resolved.capabilities())
    data["kb_refs"] = (
        book_registry.kb_refs_for(book.id, list(book.knowledge_bases))
        if resolved.record is not None else list(book.knowledge_bases)
    )
    if resolved.is_shared:
        # Own books already carry their sessions in the metadata dumped above.
        metadata = dict(data.get("metadata") or {})
        metadata["page_chat_sessions"] = _page_chat_sessions(resolved, book)
        data["metadata"] = metadata
    return data


def _created_book_payload(book: Any, engine: Any) -> dict[str, Any]:
    storage = getattr(engine, "storage", None) or get_book_storage()
    record = book_registry.find_book_by_location(
        storage.path_service.get_book_dir(), book_registry.dir_name_for(book.id),
    )
    return _book_payload(book, ResolvedBook(
        engine=engine, source="own", permission="edit", can_edit=True, can_delete=True,
        learning=storage, record=record,
    ))


def _claim_content_mutation(
    resolved: ResolvedBook,
    book_id: str,
    expected_revision: int | None,
    action: str,
    *,
    extra: dict[str, Any] | None = None,
    strict: bool = True,
) -> int:
    """Reserve the next canonical revision before a content mutation.

    Shared editors must prove which snapshot they edited. Personal books and
    admin operations stay backward compatible, but still advance the token so
    a later shared editor detects the intervening change.

    ``strict=False`` is for *generation commands* — compile this chapter,
    pause, resume. Those overwrite nobody's writing: a chapter shell fills
    itself in, and two readers both asking for it is not a conflict (the
    engine coalesces them onto one run). Guarding them was actively harmful,
    because generation advances the revision itself: ``confirm_spine`` claims
    a token and then queues the compile that the very next request has to
    match, so the first chapter of every book failed with a conflict against
    its own predecessor. They still claim a revision — a later shared edit
    should know the book moved — they just do not refuse a stale one.
    """

    storage = getattr(resolved.engine, "storage", None)
    if not resolved.can_edit or (storage is not None and not content_writable(storage)):
        raise HTTPException(status_code=404, detail="Book not found")
    book = resolved.engine.load_book(book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    current = max(1, int(book.revision or 1))
    if strict and resolved.is_shared and expected_revision is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "book_revision_required",
                "message": "Refresh the shared book before editing.",
                "current_revision": current,
            },
        )
    if strict and expected_revision is not None and expected_revision != current:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "book_revision_conflict",
                # A personal book has no collaborators, so blaming one was a
                # lie the reader could not act on. Same guard, honest cause:
                # their view is simply behind the book.
                "message": (
                    "The book was updated by another collaborator."
                    if resolved.is_shared
                    else "This book changed since your last action — reloading the latest version."
                ),
                "expected_revision": expected_revision,
                "current_revision": current,
            },
        )

    book.revision = current + 1
    book.updated_at = time.time()
    if storage is None:
        # Lightweight embedded engines used by SDK hosts may not expose their
        # persistence object; they are single-user and have no shared writes.
        return current
    storage.save_book(book)
    summary = {
        "book_id": book_id,
        "action": action,
        "before_revision": current,
        "after_revision": book.revision,
        **(extra or {}),
    }
    if resolved.is_shared:
        log_usage("book", book_id, "shared_edit", summary)
    elif get_current_user().is_admin:
        log_admin_action("book_edit", summary=summary)
    return book.revision


def _normalize_block_types(values: list[str]) -> list[str]:
    normalized = [BlockType.SECTION.value]
    seen = {BlockType.SECTION}
    for value in values:
        try:
            block_type = BlockType(value.strip().lower())
        except ValueError:
            continue
        if block_type in seen:
            continue
        seen.add(block_type)
        normalized.append(block_type.value)
    return normalized


def _persist_requested_block_types(
    resolved: ResolvedBook,
    book_id: str,
    requested: list[str] | None,
) -> None:
    if requested is None:
        return

    book = resolved.engine.load_book(book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    metadata = dict(book.metadata or {})
    if requested:
        metadata["block_types"] = _normalize_block_types(requested)
    else:
        metadata.pop("block_types", None)
    book.metadata = metadata
    book.updated_at = time.time()
    storage = getattr(resolved.engine, "storage", None) or get_book_storage()
    storage.save_book(book)


def _normalize_capture_text(value: str) -> str:
    return " ".join((value or "").strip().split())


def _build_capture_hash(book_id: str, page_id: str, block_id: str, locator: str, text: str) -> str:
    payload = "|".join(
        [
            book_id,
            page_id,
            block_id,
            locator,
            _normalize_capture_text(text),
        ],
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _coerce_capture_status(raw: str | None) -> LearningCaptureStatus | None:
    if raw is None:
        return None
    try:
        return LearningCaptureStatus(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid capture status: {raw}",
        ) from exc


_CAPTURE_TRANSITIONS: dict[LearningCaptureStatus, set[LearningCaptureStatus]] = {
    LearningCaptureStatus.CAPTURED: {
        LearningCaptureStatus.CAPTURED,
        LearningCaptureStatus.DRAFTED,
        LearningCaptureStatus.PENDING_CONFIRMATION,
        LearningCaptureStatus.APPROVED,
        LearningCaptureStatus.REJECTED,
    },
    LearningCaptureStatus.DRAFTED: {
        LearningCaptureStatus.DRAFTED,
        LearningCaptureStatus.PENDING_CONFIRMATION,
        LearningCaptureStatus.APPROVED,
        LearningCaptureStatus.REJECTED,
    },
    LearningCaptureStatus.PENDING_CONFIRMATION: {
        LearningCaptureStatus.PENDING_CONFIRMATION,
        LearningCaptureStatus.APPROVED,
        LearningCaptureStatus.REJECTED,
    },
    LearningCaptureStatus.APPROVED: {
        LearningCaptureStatus.APPROVED,
        LearningCaptureStatus.DELIVERED,
    },
    LearningCaptureStatus.DELIVERED: {
        LearningCaptureStatus.DELIVERED,
        LearningCaptureStatus.IMPORTED,
    },
    LearningCaptureStatus.IMPORTED: {LearningCaptureStatus.IMPORTED},
    LearningCaptureStatus.REJECTED: {LearningCaptureStatus.REJECTED},
}


def _is_capture_transition_allowed(
    current: LearningCaptureStatus,
    requested: LearningCaptureStatus,
) -> bool:
    return requested in _CAPTURE_TRANSITIONS.get(current, set())


def _derive_capture_title_values(
    resolved: ResolvedBook,
    book_id: str,
    page_id: str,
    block_id: str,
) -> tuple[str, str, str]:
    engine = resolved.engine
    book = engine.load_book(book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    page = engine.load_page(book_id, page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")

    spine = engine.load_spine(book_id)
    chapter_title = page.title
    if spine is not None and page.chapter_id:
        for chapter in spine.chapters:
            if chapter.id == page.chapter_id:
                chapter_title = chapter.title
                break

    base_locator = f"/books/{book_id}/pages/{page_id}"
    source_locator = f"{base_locator}/block/{block_id}" if block_id else base_locator
    return book.title, chapter_title, source_locator


def _find_capture_duplicate(
    storage: Any,
    book_id: str,
    page_id: str,
    content_hash: str,
) -> LearningCapture | None:
    for capture in storage.load_learning_captures(book_id):
        if capture.page_id != page_id:
            continue
        if capture.content_hash != content_hash:
            continue
        if capture.status == LearningCaptureStatus.REJECTED:
            continue
        return capture
    return None


class LearningCaptureCreateRequest(BaseModel):
    page_id: str
    block_id: str = ""
    source_text: str
    context_before: str = ""
    context_after: str = ""
    source_locator: str = ""
    book_title: str = ""
    chapter_title: str = ""
    user_note: str = ""
    status: str | None = None


class LearningCaptureUpdateRequest(BaseModel):
    status: str | None = None
    user_note: str | None = None
    rejected_reason: str | None = None


def _capture_payload(capture: LearningCapture) -> dict[str, object]:
    return capture.model_dump(mode="json")


# ─────────────────────────────────────────────────────────────────────────────
# REST endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/books/health")
async def health_check() -> dict[str, str]:
    return {"status": "healthy", "service": "book"}


@router.get("/books/estimate-basis")
async def estimate_basis(depth: str = "standard") -> dict[str, Any]:
    """Per-chapter generation cost, keyed by content type.

    The spine editor sums this over whatever chapters currently exist, so the
    estimate stays live while the user edits without a request per keystroke —
    and stays honest, because the numbers come from the same templates the
    architect plans from.
    """
    from deeptutor.book.estimate import chapter_basis

    return {"depth": depth, "basis": chapter_basis(depth)}


@router.get("/books/block-types")
async def block_types() -> dict[str, list[dict[str, str | bool]]]:
    from deeptutor.book.agents.page_planner import (
        PLANNABLE_BLOCK_TYPES,
        PLANNER_DEFAULT_BLOCK_TYPES,
    )

    return {
        "block_types": [
            {
                "value": block_type.value,
                "planner_default": block_type in PLANNER_DEFAULT_BLOCK_TYPES,
            }
            for block_type in sorted(PLANNABLE_BLOCK_TYPES, key=lambda item: item.value)
        ]
    }


@router.get("/books")
async def list_books() -> dict[str, Any]:
    def _collect() -> list[dict[str, Any]]:
        books: list[dict[str, Any]] = []
        for book, resolved in accessible_books():
            data = _book_payload(book, resolved)
            # Lets the library card say "continue reading" and show how far in
            # the reader is, instead of treating every book as untouched.
            data["reading"] = resolved.reading_summary(book)
            data["generation"] = resolved.engine.generation_overview(book)
            books.append(data)
        return books

    # One manifest read per book plus one progress read per book — off-loop.
    return {
        "books": await asyncio.to_thread(_collect),
        "can_create": can_create_book(),
    }


@router.get("/books/{book_id}/learning-captures")
async def list_learning_captures(
    book_id: str,
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    resolved = _resolve_book_or_404(_path_book_ref(book_id))
    book_id = book_registry.strip_ref(book_id)

    parsed_status = _coerce_capture_status(status) if status is not None else None
    storage = resolved.learning
    captures = storage.load_learning_captures(book_id, status=parsed_status)
    return {"captures": [_capture_payload(capture) for capture in captures]}


@router.post("/books/{book_id}/learning-captures")
async def create_learning_capture(
    book_id: str,
    req: LearningCaptureCreateRequest,
) -> dict[str, Any]:
    resolved = _resolve_book_or_404(_path_book_ref(book_id))
    book_id = book_registry.strip_ref(book_id)
    engine = resolved.engine

    page = engine.load_page(book_id, req.page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")

    source_text = _normalize_capture_text(req.source_text)
    if not source_text:
        raise HTTPException(status_code=400, detail="source_text is required")

    book_title, chapter_title, default_source_locator = _derive_capture_title_values(
        resolved=resolved,
        book_id=book_id,
        page_id=req.page_id,
        block_id=req.block_id,
    )

    source_locator = req.source_locator.strip() or default_source_locator
    status = _coerce_capture_status(req.status) or LearningCaptureStatus.CAPTURED

    content_hash = _build_capture_hash(
        book_id,
        req.page_id,
        req.block_id,
        source_locator,
        source_text,
    )

    storage = resolved.learning
    duplicate = _find_capture_duplicate(storage, book_id, req.page_id, content_hash)
    if duplicate is not None:
        return {"capture": _capture_payload(duplicate)}

    capture = LearningCapture(
        book_id=book_id,
        page_id=req.page_id,
        block_id=req.block_id,
        source_text=source_text,
        context_before=_normalize_capture_text(req.context_before),
        context_after=_normalize_capture_text(req.context_after),
        source_locator=source_locator,
        book_title=req.book_title or book_title,
        chapter_title=req.chapter_title or chapter_title,
        user_note=req.user_note.strip(),
        content_hash=content_hash,
        status=status,
    )
    storage.upsert_learning_capture(capture)
    return {"capture": _capture_payload(capture)}


@router.patch("/books/{book_id}/learning-captures/{capture_id}")
async def update_learning_capture(
    book_id: str,
    capture_id: str,
    req: LearningCaptureUpdateRequest,
) -> dict[str, Any]:
    resolved = _resolve_book_or_404(_path_book_ref(book_id))
    book_id = book_registry.strip_ref(book_id)
    storage = resolved.learning
    capture = storage.load_learning_capture(book_id, capture_id)
    if capture is None:
        raise HTTPException(status_code=404, detail="Learning capture not found")

    requested_status = _coerce_capture_status(req.status) if req.status is not None else None
    if requested_status is not None and not _is_capture_transition_allowed(
        capture.status,
        requested_status,
    ):
        raise HTTPException(
            status_code=400,
            detail=(f"Invalid state transition: {capture.status} -> {requested_status}"),
        )

    changed = False
    updated = capture.model_copy(deep=True)

    if requested_status is not None and requested_status != capture.status:
        updated.status = requested_status
        changed = True
    if req.user_note is not None and req.user_note != capture.user_note:
        updated.user_note = req.user_note
        changed = True
    if req.rejected_reason is not None and req.rejected_reason != capture.rejected_reason:
        updated.rejected_reason = req.rejected_reason
        changed = True

    if not changed:
        return {"capture": _capture_payload(capture)}

    updated.version = capture.version + 1
    updated.updated_at = time.time()
    storage.upsert_learning_capture(updated)
    return {"capture": _capture_payload(updated)}


def _page_summary(page) -> dict[str, Any]:
    """Page metadata without block payloads.

    A compiled page carries its full rendered content — SVG, Mermaid, prose —
    so a book's blocks run to hundreds of kilobytes. Views that only need the
    chapter list (sidebar, library, progress) ask for summaries instead.
    """
    data = page.model_dump(mode="json")
    blocks = data.pop("blocks", []) or []
    data["block_count"] = len(blocks)
    data["blocks"] = []
    return data


def _path_book_ref(book_id: str) -> str:
    """A teacher path's book (R9) is always read at its registered identity.

    The same holds for the reader's own state on it (B2-17): progress,
    bookmarks, quiz attempts and captures of a path book are kept in the
    canonical book's learning store — for a non-owner, their own overlay.

    ``enter_mastery_template_scope`` resolved the path's canonical book for
    this request; a same-id local copy of the reader's never stands in for it.
    """
    from deeptutor.teaching.access import original_book

    bound = original_book.get()
    if bound and book_registry.strip_ref(book_id) == bound:
        return f"{book_registry.REF_PREFIX}{bound}"
    return book_id


@router.get("/books/{book_id}")
async def get_book(book_id: str, include_blocks: bool = True) -> dict[str, Any]:
    resolved = _resolve_book_or_404(_path_book_ref(book_id))
    book_id = book_registry.strip_ref(book_id)
    engine = resolved.engine
    book = engine.load_book(book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")

    # Opening a book is the correctly-scoped moment to notice that its
    # compilation died with a previous process and pick it back up.
    if not resolved.is_shared and resolved.can_edit:
        await engine.maybe_resume_on_open(book_id)
    book = engine.load_book(book_id) or book

    def _read() -> tuple[Any, list[Any], Any]:
        return (
            engine.load_spine(book_id),
            engine.list_pages(book_id),
            resolved.load_progress(book_id),
        )

    # A compiled book is hundreds of KB across one file per page.
    spine, pages, progress = await asyncio.to_thread(_read)
    return {
        "book": _book_payload(book, resolved),
        "spine": spine.model_dump(mode="json") if spine else None,
        "pages": [
            (p.model_dump(mode="json") if include_blocks else _page_summary(p)) for p in pages
        ],
        "progress": progress.model_dump(mode="json"),
        "generation": engine.generation_summary(book_id, book=book, pages=pages),
    }


@router.get("/books/{book_id}/spine")
async def get_spine(book_id: str) -> dict[str, Any]:
    engine = _resolve_book_or_404(_path_book_ref(book_id)).engine
    book_id = book_registry.strip_ref(book_id)
    spine = engine.load_spine(book_id)
    if spine is None:
        raise HTTPException(status_code=404, detail="Spine not found")
    return {"spine": spine.model_dump(mode="json")}


@router.get("/books/{book_id}/pages/{page_id}")
async def get_page(book_id: str, page_id: str) -> dict[str, Any]:
    engine = _resolve_book_or_404(_path_book_ref(book_id)).engine
    book_id = book_registry.strip_ref(book_id)
    page = engine.load_page(book_id, page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")
    return {"page": page.model_dump(mode="json")}


def _reference_summary(
    record: book_registry.BookRecord, references: list[book_registry.BookReference],
) -> dict[str, Any]:
    """The owner's own references in full; other accounts' only as counts (rule R1)."""
    other: dict[str, int] = {}
    for ref in references:
        if ref.created_by != record.created_by:
            other[ref.referrer_kind] = other.get(ref.referrer_kind, 0) + 1
    return {
        "references": [ref.to_dict() for ref in references if ref.created_by == record.created_by],
        "other_references": {"total": sum(other.values()), "by_kind": dict(sorted(other.items()))},
    }


@router.delete("/books/{book_id}")
async def delete_book(
    book_id: str,
    force: bool = False,
    confirm: bool = False,
    expected_paths: int | None = Query(default=None, ge=0),
    expected_students: int | None = Query(default=None, ge=0),
) -> dict[str, Any]:
    """Rule R1: references never block the owner; teacher paths need ``confirm``.

    Every reference is revoked (``book_deleted``) with the deletion. When a
    published teacher path cites the book, the first request answers a
    counts-only 409 and nothing changes; a confirmation only covers the paths
    whose counts were shown (``expected_*``), otherwise fresh counts return.
    The registry transaction and the rename aside stay on the event loop
    (runtime finalization is bound to it); the teaching-store counting and
    the removal of the staged tree run in worker threads.
    """
    from deeptutor.teaching.access import (
        WITHDRAWAL_ROUNDS, _refusal, withdrawal_confirmation_detail, withdrawal_unsettled,
    )

    if force:
        return await _delete_book(book_id, force=True, confirmed=None)
    confirmed: frozenset[str] | None = None
    last: dict[str, Any] | None = None
    for _round in range(WITHDRAWAL_ROUNDS):
        try:
            return await _delete_book(book_id, force=False, confirmed=confirmed)
        except book_registry.BookWithdrawalNeedsConfirmation as exc:
            confirmed = frozenset(exc.referrer_ids)
            detail = await asyncio.to_thread(
                withdrawal_confirmation_detail, confirmed, resource="book",
            )
            refusal = _refusal(
                detail, confirm=confirm, expected_paths=expected_paths,
                expected_students=expected_students,
            )
            if refusal is not None:
                raise HTTPException(status_code=409, detail=refusal) from exc
            last = detail or last
    raise HTTPException(status_code=409, detail=withdrawal_unsettled("book", last=last))


async def _delete_book(
    book_id: str, *, force: bool, confirmed: frozenset[str] | None,
) -> dict[str, Any]:
    resolved = _resolve_book_or_404(book_id, delete=True)
    book_id = book_registry.strip_ref(book_id)
    user = get_current_user()
    if force and not user.is_admin:
        raise HTTPException(status_code=403, detail="Only admins can force book deletion.")
    references = []
    # Recovery stays inside the writer: failures rename the book back before
    # ROLLBACK; success finalizes runtimes/buses only after COMMIT.
    recovery = ExitStack()
    with book_sharing.shared_write(recovery=recovery) as conn:
        current = book_registry._get_book(conn, book_id)
        record = None
        if resolved.record is not None:
            record = _locked_writable(conn, resolved, resolved.record)
        elif current is not None and _record_matches_storage(resolved, current):
            record = _locked_writable(conn, resolved, current)
        if record is not None:
            references = book_registry._references_of(conn, record.book_id)
            if not force:
                book_registry.require_withdrawal_confirmation(
                    references, confirm=confirmed is not None, confirmed_referrers=confirmed,
                )
        staging = recovery.enter_context(resolved.engine.recoverable_deletion(book_id))
        if staging is None:
            raise HTTPException(status_code=404, detail="Book not found")
        if record is not None:
            book_registry._unregister(conn, book_id)
            book_sharing.sync_protection(conn, book_id)
    # Committed: the slow tree removal runs outside both writers, off the loop.
    await asyncio.to_thread(resolved.engine.storage.purge_deletion, staging)
    if user.is_admin:
        log_admin_action(
            "book_delete",
            summary={
                "book_id": book_id,
                "forced_references": len(references),
            },
        )
    return {"deleted": True, "book_id": book_id}


class BookVisibilityRequest(BaseModel):
    visibility: Literal["public", "private"]
    force: bool = False
    #: Rule R1: make private although published teacher paths cite the book, as
    #: long as the affected counts still equal the ones shown (when given).
    confirm: bool = False
    expected_paths: int | None = Field(default=None, ge=0)
    expected_students: int | None = Field(default=None, ge=0)
    kb_choices: dict[str, Literal["make_public", "reference_only"]] = Field(default_factory=dict)


def _record_matches_storage(resolved: ResolvedBook, record: book_registry.BookRecord) -> bool:
    return resolved.engine.storage.book_root(record.book_id).resolve() == (
        Path(record.base_dir) / record.dir_name
    ).resolve()


def _registered_writable(
    book_id: str, *, unpublish: bool = False,
) -> tuple[ResolvedBook, book_registry.BookRecord]:
    """``unpublish``: the request may only make the book private (B3-12)."""
    resolved = _resolve_book_or_404(book_id)
    record = resolved.record
    if record is None or not _record_matches_storage(resolved, record):
        raise HTTPException(
            status_code=409, detail="This location has no registered book identity. Reload and retry.",
        )
    editable = resolved.can_edit or (unpublish and resolved.admin_may_unpublish())
    if not editable or not book_registry.can_write_book(record, get_current_user()):
        raise HTTPException(
            status_code=403, detail="Only the book's creator or an admin can manage its sharing.",
        )
    return resolved, record


def _locked_writable(
    conn: sqlite3.Connection, resolved: ResolvedBook, record: book_registry.BookRecord,
    *, content_ok: bool | None = None, unpublish: bool = False,
) -> book_registry.BookRecord:
    """Final identity (409), owner (403) and source (403) checks, in that order.

    ``content_ok`` is ``content_writable`` computed before a read snapshot: it
    scans account directories, which must not run while holding SHARED locks.
    ``unpublish`` (the request may only make the book private): an admin
    skips the source check for a book that is public under this lock, since
    visibility is registry metadata (B3-12); an archived source stays frozen.
    """
    current = book_registry._get_book(conn, record.book_id)
    if current is None or not _record_matches_storage(resolved, current) or (
        current.base_dir, current.dir_name, current.created_at
    ) != (record.base_dir, record.dir_name, record.created_at):
        raise HTTPException(status_code=409, detail="Book location or identity changed; reload and retry.")
    if not book_registry._can_write(conn, current, get_current_user()):
        raise HTTPException(status_code=403, detail="Only the book's creator or an admin may manage it.")
    if unpublish and current.visibility == "public" and get_current_user().is_admin:
        return current
    if not (content_writable(resolved.engine.storage) if content_ok is None else content_ok):
        raise HTTPException(status_code=403, detail="This book's source is read-only.")
    return current


@contextmanager
def _sharing_snapshot(
    resolved: ResolvedBook, expected: book_registry.BookRecord, *, unpublish: bool = False,
) -> Iterator[tuple[sqlite3.Connection, book_registry.BookRecord]]:
    """Read-only joint snapshot for the sharing GETs; never waits on writers.

    Only registry queries run inside; the archive scan runs before it opens.
    """
    content_ok = content_writable(resolved.engine.storage)
    with book_sharing.shared_read() as conn:
        if conn is None:
            raise HTTPException(
                status_code=409,
                detail="This location has no registered book identity. Reload and retry.",
            )
        yield conn, _locked_writable(
            conn, resolved, expected, content_ok=content_ok, unpublish=unpublish,
        )


@router.get("/books/{book_id}/sharing")
async def get_book_sharing(book_id: str) -> dict[str, Any]:
    def read() -> dict[str, Any]:
        # Read-only views: an admin who may still make the book private sees them.
        resolved, expected = _registered_writable(book_id, unpublish=True)
        capabilities = resolved.capabilities()
        try:
            with _sharing_snapshot(resolved, expected, unpublish=True) as (conn, record):
                return {
                    "book_id": record.book_id,
                    "ref": record.ref,
                    "visibility": record.visibility,
                    "can_set_visibility": capabilities["can_set_visibility"],
                    "can_make_public": capabilities["can_make_public"],
                    "kbs": book_sharing._plan(conn, record).to_dict(),
                    **_reference_summary(record, book_registry._references_of(conn, record.book_id)),
                }
        except book_registry.BookRegistryError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Registry reads and the manifest/archive checks stay off the event loop.
    return await asyncio.to_thread(read)


@router.put("/books/{book_id}/visibility")
async def set_book_visibility(book_id: str, req: BookVisibilityRequest) -> dict[str, Any]:
    from deeptutor.multi_user.roles import is_teacher_or_admin

    unpublish = req.visibility == "private"
    resolved, record = _registered_writable(book_id, unpublish=unpublish)
    user = get_current_user()
    if req.force and not user.is_admin:
        raise HTTPException(status_code=403, detail="Only admins can force a visibility change.")
    if req.visibility == "public" and not is_teacher_or_admin():
        raise HTTPException(status_code=403, detail="Only teachers and admins can make a book public.")
    def apply(confirmed: frozenset[str] | None) -> tuple[book_registry.BookRecord, dict[str, Any]]:
        with book_sharing.shared_write() as conn:
            current = _locked_writable(conn, resolved, record, unpublish=unpublish)
            changed = book_sharing.change_visibility(
                conn, current, req.visibility, force=req.force, kb_choices=req.kb_choices,
                confirm=confirmed is not None, confirmed_referrers=confirmed,
            )
            return changed, book_sharing._plan(conn, changed).to_dict()

    def run() -> tuple[book_registry.BookRecord, dict[str, Any]]:
        from deeptutor.teaching.access import run_confirmed_withdrawal

        if req.force:
            return apply(None)
        return run_confirmed_withdrawal(
            apply, book_registry.BookWithdrawalNeedsConfirmation, confirm=req.confirm,
            expected_paths=req.expected_paths, expected_students=req.expected_students,
            resource="book",
        )

    from deeptutor.teaching.access import WithdrawalConsentRequired

    try:
        # Registry writers and the teaching-store counting stay off the event loop.
        updated, plan = await asyncio.to_thread(run)
    except WithdrawalConsentRequired as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    except book_registry.BookRegistryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if user.is_admin and record.created_by != user.id:
        log_admin_action(
            "book_visibility",
            summary={"book_id": record.book_id, "visibility": updated.visibility, "force": req.force},
        )
    return {
        "book_id": updated.book_id,
        "ref": updated.ref,
        "visibility": updated.visibility,
        "kbs": plan,
    }


@router.get("/books/{book_id}/references")
async def get_book_references(book_id: str) -> dict[str, Any]:
    def read() -> dict[str, Any]:
        # Read-only views: an admin who may still make the book private sees them.
        resolved, expected = _registered_writable(book_id, unpublish=True)
        try:
            with _sharing_snapshot(resolved, expected, unpublish=True) as (conn, record):
                return {
                    "book_id": record.book_id, "ref": record.ref,
                    **_reference_summary(record, book_registry._references_of(conn, record.book_id)),
                }
        except book_registry.BookRegistryError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return await asyncio.to_thread(read)


@router.post("/books")
async def create_book(req: CreateBookRequest) -> dict[str, Any]:
    """Stage 1: capture inputs + run IdeationAgent."""
    if not req.user_intent.strip():
        raise HTTPException(status_code=400, detail="user_intent is required")
    if not can_create_book():
        raise HTTPException(status_code=403, detail="Book creation is not allowed")
    engine = get_book_engine()
    try:
        book, proposal = await engine.create_book(
            user_intent=req.user_intent,
            source_refs=req.source_refs,
            chat_session_id=req.chat_session_id,
            chat_selections=req.chat_selections,
            notebook_refs=req.notebook_refs,
            knowledge_bases=req.knowledge_bases,
            question_categories=req.question_categories,
            question_entries=req.question_entries,
            language=req.language,
            fallback_language=req.fallback_language,
            depth=req.depth,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(f"create_book failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
    return {
        "book": _created_book_payload(book, engine),
        "proposal": proposal.model_dump(mode="json"),
    }


@router.post("/books/confirm-proposal")
async def confirm_proposal(req: ConfirmProposalRequest) -> dict[str, Any]:
    """Stage 2: user confirms (and possibly edits) the proposal → SpineAgent."""
    resolved = _resolve_book_or_404(req.book_id, edit=True)
    req.book_id = book_registry.strip_ref(req.book_id)
    engine = resolved.engine
    revision = _claim_content_mutation(
        resolved,
        req.book_id,
        req.expected_revision,
        "confirm_proposal",
    )
    edited: BookProposal | None = None
    if req.proposal:
        try:
            edited = BookProposal.model_validate(req.proposal)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid proposal: {exc}")
    try:
        book, spine = await engine.confirm_proposal(book_id=req.book_id, edited_proposal=edited)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.error(f"confirm_proposal failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
    return {
        "book": book.model_dump(mode="json"),
        "spine": spine.model_dump(mode="json"),
        "book_revision": revision,
    }


@router.post("/books/confirm-spine")
async def confirm_spine(req: ConfirmSpineRequest) -> dict[str, Any]:
    """Stage 3: user confirms the spine → create pending page shells."""
    resolved = _resolve_book_or_404(req.book_id, edit=True)
    req.book_id = book_registry.strip_ref(req.book_id)
    engine = resolved.engine
    revision = _claim_content_mutation(
        resolved,
        req.book_id,
        req.expected_revision,
        "confirm_spine",
    )
    edited: Spine | None = None
    if req.spine:
        try:
            edited = Spine.model_validate(req.spine)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid spine: {exc}")
    _persist_requested_block_types(resolved, req.book_id, req.block_types)
    try:
        pages = await engine.confirm_spine(
            book_id=req.book_id,
            edited_spine=edited,
            auto_compile=req.auto_compile,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.error(f"confirm_spine failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
    return {"pages": [p.model_dump(mode="json") for p in pages], "book_revision": revision}


@router.post("/books/compile-page")
async def compile_page(req: CompilePageRequest) -> dict[str, Any]:
    """Drive the compiler for the page the user just opened (current-page priority)."""
    resolved = _resolve_book_or_404(req.book_id, edit=True)
    req.book_id = book_registry.strip_ref(req.book_id)
    engine = resolved.engine
    revision = _claim_content_mutation(
        resolved,
        req.book_id,
        req.expected_revision,
        "compile_page",
        extra={"page_id": req.page_id, "force": req.force},
        # Generation command, not an edit — see `_claim_content_mutation`.
        strict=False,
    )
    try:
        page = await engine.compile_page(book_id=req.book_id, page_id=req.page_id, force=req.force)
    except BookPausedError as exc:
        raise _book_paused_http(exc)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.error(f"compile_page failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
    return {"page": page.model_dump(mode="json"), "book_revision": revision}


@router.post("/books/regenerate-block")
async def regenerate_block(req: RegenerateBlockRequest) -> dict[str, Any]:
    resolved = _resolve_book_or_404(req.book_id, edit=True)
    req.book_id = book_registry.strip_ref(req.book_id)
    engine = resolved.engine
    revision = _claim_content_mutation(
        resolved,
        req.book_id,
        req.expected_revision,
        "regenerate_block",
        extra={"page_id": req.page_id, "block_id": req.block_id},
    )
    try:
        block = await engine.regenerate_block(
            book_id=req.book_id,
            page_id=req.page_id,
            block_id=req.block_id,
            params_override=req.params_override,
        )
    except BookPausedError as exc:
        raise _book_paused_http(exc)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"regenerate_block failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
    if block is None:
        raise HTTPException(status_code=404, detail="Block not found")
    return {"block": block.model_dump(mode="json"), "book_revision": revision}


def _coerce_block_type(name: str) -> BlockType:
    try:
        return BlockType(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown block type: {name}") from exc


def _coerce_content_type(name: str) -> ContentType:
    try:
        return ContentType(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown content type: {name}") from exc


@router.post("/books/insert-block")
async def insert_block(req: InsertBlockRequest) -> dict[str, Any]:
    resolved = _resolve_book_or_404(req.book_id, edit=True)
    req.book_id = book_registry.strip_ref(req.book_id)
    engine = resolved.engine
    revision = _claim_content_mutation(
        resolved,
        req.book_id,
        req.expected_revision,
        "insert_block",
        extra={"page_id": req.page_id, "block_type": req.block_type},
    )
    block_type = _coerce_block_type(req.block_type)
    try:
        block = await engine.insert_block(
            book_id=req.book_id,
            page_id=req.page_id,
            block_type=block_type,
            params=req.params,
            position=req.position,
            compile_now=req.compile_now,
        )
    except BookPausedError as exc:
        raise _book_paused_http(exc)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"insert_block failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
    if block is None:
        raise HTTPException(status_code=404, detail="Page or chapter not found")
    return {"block": block.model_dump(mode="json"), "book_revision": revision}


@router.post("/books/delete-block")
async def delete_block(req: DeleteBlockRequest) -> dict[str, Any]:
    resolved = _resolve_book_or_404(req.book_id, edit=True)
    req.book_id = book_registry.strip_ref(req.book_id)
    engine = resolved.engine
    revision = _claim_content_mutation(
        resolved,
        req.book_id,
        req.expected_revision,
        "delete_block",
        extra={"page_id": req.page_id, "block_id": req.block_id},
    )
    ok = await engine.delete_block(book_id=req.book_id, page_id=req.page_id, block_id=req.block_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Block not found")
    return {"ok": True, "book_revision": revision}


@router.post("/books/move-block")
async def move_block(req: MoveBlockRequest) -> dict[str, Any]:
    resolved = _resolve_book_or_404(req.book_id, edit=True)
    req.book_id = book_registry.strip_ref(req.book_id)
    engine = resolved.engine
    revision = _claim_content_mutation(
        resolved,
        req.book_id,
        req.expected_revision,
        "move_block",
        extra={"page_id": req.page_id, "block_id": req.block_id},
    )
    ok = await engine.move_block(
        book_id=req.book_id,
        page_id=req.page_id,
        block_id=req.block_id,
        new_position=req.new_position,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Block not found")
    return {"ok": True, "book_revision": revision}


@router.post("/books/change-block-type")
async def change_block_type(req: ChangeBlockTypeRequest) -> dict[str, Any]:
    resolved = _resolve_book_or_404(req.book_id, edit=True)
    req.book_id = book_registry.strip_ref(req.book_id)
    engine = resolved.engine
    revision = _claim_content_mutation(
        resolved,
        req.book_id,
        req.expected_revision,
        "change_block_type",
        extra={"page_id": req.page_id, "block_id": req.block_id},
    )
    new_type = _coerce_block_type(req.new_type)
    try:
        block = await engine.change_block_type(
            book_id=req.book_id,
            page_id=req.page_id,
            block_id=req.block_id,
            new_type=new_type,
            params_override=req.params_override,
        )
    except BookPausedError as exc:
        raise _book_paused_http(exc)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"change_block_type failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
    if block is None:
        raise HTTPException(status_code=404, detail="Block not found")
    return {"block": block.model_dump(mode="json"), "book_revision": revision}


@router.post("/books/deep-dive")
async def deep_dive(req: DeepDiveRequest) -> dict[str, Any]:
    resolved = _resolve_book_or_404(req.book_id, edit=True)
    req.book_id = book_registry.strip_ref(req.book_id)
    engine = resolved.engine
    revision = _claim_content_mutation(
        resolved,
        req.book_id,
        req.expected_revision,
        "deep_dive",
        extra={"page_id": req.parent_page_id, "topic": req.topic},
    )
    content_type = _coerce_content_type(req.content_type)
    try:
        page = await engine.create_deep_dive_subpage(
            book_id=req.book_id,
            parent_page_id=req.parent_page_id,
            topic=req.topic,
            block_id=req.block_id,
            content_type=content_type,
        )
    except BookPausedError as exc:
        raise _book_paused_http(exc)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"deep_dive failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
    if page is None:
        raise HTTPException(status_code=404, detail="Parent page not found")
    return {"page": page.model_dump(mode="json"), "book_revision": revision}


@router.post("/books/quiz-attempt")
async def quiz_attempt(req: QuizAttemptRequest) -> dict[str, Any]:
    resolved = _resolve_book_or_404(_path_book_ref(req.book_id))
    req.book_id = book_registry.strip_ref(req.book_id)
    book = resolved.engine.load_book(req.book_id)
    page = next(
        (item for item in resolved.engine.list_pages(req.book_id) if item.id == req.page_id), None
    )
    block = page.block_by_id(req.block_id) if page is not None else None
    questions = (
        [item for item in block.payload.get("questions", []) if isinstance(item, dict)]
        if block is not None
        else []
    )
    resolved_question = _focus_check_question(questions, req.question_id, req.block_id)
    current_progress = resolved.load_progress(req.book_id)
    previous_attempts = len(current_progress.quiz_attempts)
    try:
        progress = progress_ops.record_attempt(
            current_progress,
            page_id=req.page_id,
            block_id=req.block_id,
            question_id=req.question_id,
            user_answer=req.user_answer,
            is_correct=req.is_correct,
            page_to_chapter={
                page.id: page.chapter_id
                for page in resolved.engine.list_pages(req.book_id)
                if page.chapter_id
            },
            submission_id=req.submission_id.strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if len(progress.quiz_attempts) != previous_attempts:
        resolved.learning.save_progress(progress)
    if req.is_correct is not None and resolved_question is not None and book is not None:
        question_id, question = resolved_question
        verified_grade = _verified_choice_grade(question, req.user_answer)
        trusted_linkage = verified_grade is not None and verified_grade == req.is_correct
        try:
            from deeptutor.services.session import get_sqlite_session_store

            store = get_sqlite_session_store()
            session_id = _page_chat_session_id(resolved, book, req.page_id)
            has_session = bool(session_id and await store.get_session(session_id) is not None)
            from deeptutor.learning.assessment import (
                AssessmentRecord,
                is_correct_to_result,
                record_assessment,
            )

            matching_attempts = [
                attempt
                for attempt in progress.quiz_attempts
                if attempt.block_id == req.block_id and attempt.question_id == req.question_id
            ]
            latest_attempt = next(
                (
                    attempt
                    for attempt in matching_attempts
                    if req.submission_id and attempt.submission_id == req.submission_id
                ),
                matching_attempts[-1],
            )
            attempt_count = matching_attempts.index(latest_attempt) + 1
            await record_assessment(
                AssessmentRecord(
                    session_id=session_id if has_session else "",
                    origin_type="conversation" if has_session else "document_analysis",
                    origin_ref=session_id if has_session else f"book:{req.book_id}",
                    turn_id=req.block_id,
                    question_id=question_id,
                    question=str(question.get("question") or block.title or "Focus check"),
                    question_type=str(question.get("question_type") or ""),
                    options=question.get("options") or {},
                    correct_answer=str(question.get("correct_answer") or ""),
                    explanation=str(question.get("explanation") or ""),
                    difficulty=str(question.get("difficulty") or ""),
                    user_answer=req.user_answer,
                    is_correct=bool(req.is_correct),
                    result=is_correct_to_result(bool(req.is_correct)),
                    source="book",
                    assessment_type="focus_check",
                    material_id=req.book_id,
                    material_title=book.title,
                    section_id=req.page_id,
                    section_title=page.title if page is not None else "",
                    mastery_path_id=(
                        str(question.get("mastery_path_id") or "") if trusted_linkage else ""
                    ),
                    knowledge_point_id=(
                        str(question.get("knowledge_point_id") or "") if trusted_linkage else ""
                    ),
                    attempt_count=attempt_count,
                    attempt_id=(
                        f"book:{req.book_id}:{req.block_id}:{question_id}:"
                        f"{latest_attempt.submission_id or f'{latest_attempt.timestamp:.9f}'}"
                    ),
                )
            )
        except Exception as exc:
            logger.warning(
                "Failed to sync Focus-Check %s to question bank for book %s",
                req.question_id,
                req.book_id,
                exc_info=True,
            )
            raise HTTPException(
                status_code=500, detail="Failed to save Focus-Check evidence"
            ) from exc
    return {"progress": progress.model_dump(mode="json")}


@router.post("/books/update-block")
async def update_block(req: UpdateBlockRequest) -> dict[str, Any]:
    """Edit a block's prose in place.

    Scoped deliberately narrow — title and body only. Fixing a typo shouldn't
    require regenerating a whole block and hoping for a better roll, but a book
    is not a document editor either; substantial rewrites belong in Co-Writer.
    """
    resolved = _resolve_book_or_404(req.book_id, edit=True)
    req.book_id = book_registry.strip_ref(req.book_id)
    engine = resolved.engine
    revision = _claim_content_mutation(
        resolved,
        req.book_id,
        req.expected_revision,
        "update_block",
        extra={"page_id": req.page_id, "block_id": req.block_id},
    )
    block = await engine.update_block(
        book_id=req.book_id,
        page_id=req.page_id,
        block_id=req.block_id,
        title=req.title,
        body=req.body,
    )
    if block is None:
        raise HTTPException(status_code=404, detail="Block not found or not editable")
    return {"block": block.model_dump(mode="json"), "book_revision": revision}


@router.post("/books/progress/visit")
async def mark_visited(req: ProgressRequest) -> dict[str, Any]:
    """Remember the reader's position so the book can be resumed later."""
    resolved = _resolve_book_or_404(_path_book_ref(req.book_id))
    req.book_id = book_registry.strip_ref(req.book_id)
    progress = resolved.load_progress(req.book_id)
    if progress_ops.mark_visited(progress, req.page_id):
        resolved.learning.save_progress(progress)
    return {"progress": progress.model_dump(mode="json")}


@router.post("/books/progress/bookmark")
async def toggle_bookmark(req: ProgressRequest) -> dict[str, Any]:
    resolved = _resolve_book_or_404(_path_book_ref(req.book_id))
    req.book_id = book_registry.strip_ref(req.book_id)
    progress = resolved.load_progress(req.book_id)
    progress_ops.toggle_bookmark(progress, req.page_id)
    resolved.learning.save_progress(progress)
    return {"progress": progress.model_dump(mode="json")}


@router.get("/books/{book_id}/export")
async def export_book(book_id: str) -> Response:
    """Download the whole book as a single Markdown file."""
    engine = _resolve_book_or_404(book_id).engine
    book_id = book_registry.strip_ref(book_id)
    book = engine.load_book(book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    markdown = await asyncio.to_thread(
        lambda: render_book_markdown(book, engine.load_spine(book_id), engine.list_pages(book_id))
    )
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": content_disposition(
                export_filename(book), disposition="attachment"
            )
        },
    )


@router.get("/books/{book_id}/health")
async def book_health(book_id: str) -> dict[str, Any]:
    resolved = _resolve_book_or_404(book_id)
    book_id = book_registry.strip_ref(book_id)
    engine = resolved.engine
    if not resolved.can_edit:
        # Drift detection persists stale-page state on the canonical Book. A
        # read-only collaborator may inspect it, but must not mutate it merely
        # by opening the health banner.
        book = engine.load_book(book_id)
        stale_page_ids = list(book.stale_page_ids) if book is not None else []
        drift = {
            "book_id": book_id,
            "has_drift": bool(stale_page_ids),
            "new_kbs": [],
            "removed_kbs": [],
            "changed_kbs": [],
            "stale_page_ids": stale_page_ids,
            "cached": True,
        }
    else:
        drift = engine.kb_drift_report(book_id)
    log = engine.log_health(book_id)
    generation = engine.generation_summary(book_id)
    return {"kb_drift": drift, "log_health": log, "generation": generation}


@router.post("/books/{book_id}/refresh-fingerprints")
async def refresh_fingerprints(
    book_id: str,
    force: bool = False,
    expected_revision: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    """Mark the current KB state as seen.

    409s while pages the last drift flagged are still awaiting recompilation.
    ``force=true`` dismisses them anyway — stale detection over-marks on
    purpose when an anchor cannot be resolved, so the user needs a way out.
    """
    resolved = _resolve_book_or_404(book_id, edit=True)
    book_id = book_registry.strip_ref(book_id)
    engine = resolved.engine
    revision = _claim_content_mutation(
        resolved,
        book_id,
        expected_revision,
        "refresh_fingerprints",
        extra={"force": force},
    )
    try:
        result = engine.refresh_kb_fingerprints(book_id, force=force)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Book not found")
    return {**result, "book_revision": revision}


@router.post("/books/supplement")
async def supplement(req: SupplementRequest) -> dict[str, Any]:
    resolved = _resolve_book_or_404(req.book_id, edit=True)
    req.book_id = book_registry.strip_ref(req.book_id)
    engine = resolved.engine
    revision = _claim_content_mutation(
        resolved,
        req.book_id,
        req.expected_revision,
        "supplement",
        extra={"page_id": req.page_id, "topic": req.topic},
    )
    try:
        block = await engine.supplement_for_weakness(
            book_id=req.book_id,
            page_id=req.page_id,
            topic=req.topic,
        )
    except BookPausedError as exc:
        raise _book_paused_http(exc)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"supplement failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
    if block is None:
        raise HTTPException(status_code=404, detail="Page not found")
    return {"block": block.model_dump(mode="json"), "book_revision": revision}


@router.post("/books/page-chat-session")
async def set_page_chat_session(req: PageChatSessionRequest) -> dict[str, Any]:
    resolved = _resolve_book_or_404(_path_book_ref(req.book_id))
    req.book_id = book_registry.strip_ref(req.book_id)
    engine = resolved.engine
    if resolved.is_shared:
        book = engine.load_book(req.book_id)
        page = engine.load_page(req.book_id, req.page_id)
        if book is not None and page is not None and req.session_id.strip():
            resolved.learning.set_page_chat_session(req.book_id, req.page_id, req.session_id)
            log_usage("book", req.book_id, "page_chat", {"page_id": req.page_id})
            book = book.model_copy(deep=True)
        else:
            book = None
    else:
        book = engine.set_page_chat_session(
            book_id=req.book_id,
            page_id=req.page_id,
            session_id=req.session_id,
        )
    if book is None:
        raise HTTPException(status_code=404, detail="Book or page not found")
    return {"book": _book_payload(book, resolved)}


@router.post("/books/resume")
async def resume_book(req: ResumeBookRequest) -> dict[str, Any]:
    """Re-queue unfinished pages without discarding what already compiled."""
    resolved = _resolve_book_or_404(req.book_id, edit=True)
    req.book_id = book_registry.strip_ref(req.book_id)
    engine = resolved.engine
    revision = _claim_content_mutation(
        resolved,
        req.book_id,
        req.expected_revision,
        "resume",
        strict=False,
    )
    try:
        pages = await engine.resume_book(book_id=req.book_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.error(f"resume_book failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
    return {"pages": [p.model_dump(mode="json") for p in pages], "book_revision": revision}


@router.post("/books/pause")
async def pause_book(req: PauseBookRequest) -> dict[str, Any]:
    """Persist a manual pause, cancel in-flight work, and keep completed output."""
    resolved = _resolve_book_or_404(req.book_id, edit=True)
    req.book_id = book_registry.strip_ref(req.book_id)
    engine = resolved.engine
    revision = _claim_content_mutation(
        resolved,
        req.book_id,
        req.expected_revision,
        "pause",
        strict=False,
    )
    try:
        pages = await engine.pause_book(book_id=req.book_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.error(f"pause_book failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
    return {"pages": [p.model_dump(mode="json") for p in pages], "book_revision": revision}


@router.post("/books/rebuild")
async def rebuild_book(req: RebuildBookRequest) -> dict[str, Any]:
    resolved = _resolve_book_or_404(req.book_id, edit=True)
    req.book_id = book_registry.strip_ref(req.book_id)
    engine = resolved.engine
    revision = _claim_content_mutation(
        resolved,
        req.book_id,
        req.expected_revision,
        "rebuild",
    )
    _persist_requested_block_types(resolved, req.book_id, req.block_types)
    try:
        pages = await engine.rebuild_book(book_id=req.book_id, auto_compile=req.auto_compile)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.error(f"rebuild_book failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))
    return {"pages": [p.model_dump(mode="json") for p in pages], "book_revision": revision}


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket – streamed Book events
# ─────────────────────────────────────────────────────────────────────────────


def _serialize_event(event) -> dict[str, Any]:
    return {
        "type": event.type.value if hasattr(event.type, "value") else str(event.type),
        "source": event.source,
        "stage": event.stage,
        "content": event.content,
        "metadata": event.metadata or {},
        "seq": event.seq,
        "timestamp": event.timestamp,
    }


#: A socket re-checks a book's authority at most once per this many seconds;
#: after a revocation it forwards events only until the cached result expires.
_STREAM_AUTH_TTL = 1.0
_stream_auth_clock: Callable[[], float] = time.monotonic


class _SocketFanout:
    """Forwards several buses into one socket, at most one task per bus.

    A client watching a book needs events from two places: the book's
    long-lived stream (background compilation) and, while a book is still being
    created, a connection-scoped stream (no book id exists yet). Both are
    attached here; neither producer needs to know a socket is listening.

    Attaching is idempotent — re-subscribing to a book already being forwarded
    is a no-op rather than a second, duplicating reader.
    """

    def __init__(self, send: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        self._send = send
        self._tasks: dict[int, asyncio.Task[None]] = {}

    def attach(
        self, bus: StreamBus, *, after_seq: int = 0,
        authorized: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        key = id(bus)
        existing = self._tasks.get(key)
        if existing is not None and not existing.done():
            return
        self._tasks[key] = asyncio.create_task(
            self._forward(bus, after_seq=after_seq, authorized=authorized)
        )

    def attach_book(self, book_id: str, resolved: ResolvedBook, *, after_seq: int = 0) -> None:
        from deeptutor.book.event_hub import get_book_bus

        actor = get_current_user()
        record = resolved.record
        storage = getattr(resolved.engine, "storage", None) or resolved.learning
        location = str(storage.path_service.get_book_dir().resolve())

        def check() -> bool:
            try:
                if record is not None:
                    current = book_registry.get_book(record.book_id)
                    if (
                        current is None or current.created_at != record.created_at
                        or current.base_dir != location or current.dir_name != record.dir_name
                    ):
                        return False
                    if resolved.is_shared:
                        return book_registry.can_read_book(record, actor)
                return resolved.engine.load_book(book_id) is not None
            except Exception:
                logger.warning("Book stream authorization unavailable", exc_info=True)
                return False

        checked_at: float | None = None
        allowed = False

        async def authorized() -> bool:
            # Hundreds of events per compile: reuse a check for the TTL and run
            # the registry/manifest reads in a worker thread. Fails closed.
            nonlocal checked_at, allowed
            now = _stream_auth_clock()
            if checked_at is not None and now - checked_at < _STREAM_AUTH_TTL:
                return allowed
            try:
                allowed = await asyncio.to_thread(check)
            except Exception:
                logger.warning("Book stream authorization unavailable", exc_info=True)
                allowed = False
            checked_at = now
            return allowed

        self.attach(
            get_book_bus(book_id, storage=storage), after_seq=after_seq, authorized=authorized,
        )

    async def _forward(
        self, bus: StreamBus, *, after_seq: int,
        authorized: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        async for event in bus.subscribe(after_seq=after_seq):
            if authorized is not None and not await authorized():
                return
            if event.source != BOOK_SOURCE:
                continue
            await self._send(_serialize_event(event))

    async def close(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        for task in self._tasks.values():
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()


@ws_router.websocket("/books")
async def book_websocket(ws: WebSocket) -> None:
    """Streaming endpoint.

    Two kinds of client message:

    **Subscribe** — attach this socket to a book's long-lived stream. Recent
    history is replayed on attach, so a reader who refreshes mid-compilation
    catches up instead of watching a frozen page::

        {"type": "subscribe", "book_id": "..."}

    **Actions** — run an engine operation and reply with a single result::

        {"type": "create",           ...CreateBookRequest fields}
        {"type": "confirm_proposal", "book_id": "...", "proposal": {...}}
        {"type": "confirm_spine",    "book_id": "...", "spine": {...}, "auto_compile": true}
        {"type": "compile_page",     "book_id": "...", "page_id": "...", "force": false}
        {"type": "regenerate_block", "book_id": "...", "page_id": "...", "block_id": "..."}

    Actions publish into the book's own stream (see :mod:`deeptutor.book.event_hub`),
    so their progress reaches *every* subscriber, and work they leave running in
    the background keeps streaming long after the action has replied. The socket
    only ever closes streams it created itself.
    """
    from deeptutor.api.routers.auth import ws_auth_failed, ws_require_auth
    from deeptutor.book.event_hub import get_book_bus
    from deeptutor.multi_user.context import reset_current_user

    user_token = await ws_require_auth(ws)
    if user_token is ws_auth_failed:
        return

    await ws.accept()
    closed = False

    async def send(data: dict[str, Any]) -> None:
        nonlocal closed
        if closed:
            return
        try:
            await ws.send_json(data)
        except Exception:
            closed = True

    fanout = _SocketFanout(send)
    # Book creation has no book id to stream into yet, so ideation events go
    # through a connection-scoped bus. It is the only bus this socket owns.
    creation_bus = StreamBus()
    fanout.attach(creation_bus)

    try:
        while not closed:
            try:
                data = await ws.receive_json()
            except WebSocketDisconnect:
                break
            except Exception as exc:
                await send({"type": "error", "content": f"Bad message: {exc}"})
                continue

            msg_type = str(data.get("type") or "").strip()
            if not msg_type:
                await send({"type": "error", "content": "Missing 'type' field"})
                continue

            book_id = str(data.get("book_id") or "").strip()
            resolved: ResolvedBook | None = None
            if book_id:
                try:
                    resolved = _resolve_book_or_404(book_id)
                    book_id = book_registry.strip_ref(book_id)
                except HTTPException:
                    await send({"type": "error", "content": f"Book not found: {book_id}"})
                    continue

            activity = None
            try:
                from deeptutor.services.workspace.activity import acquire_activity
                from deeptutor.services.workspace.context import (
                    current_workspace_id,
                    resolve_workspace_scope,
                )
                from deeptutor.services.workspace.models import WorkspaceError

                activity = acquire_activity()
                if (
                    msg_type != "subscribe"
                    and resolve_workspace_scope(current_workspace_id()).archived
                ):
                    raise WorkspaceError("Restore this workspace before changing its data.")
                if msg_type == "subscribe":
                    if not book_id:
                        await send({"type": "error", "content": "subscribe requires book_id"})
                    else:
                        storage = getattr(resolved.engine, "storage", None) or resolved.learning
                        bus = get_book_bus(book_id, storage=storage)
                        try:
                            requested_cursor = max(0, int(data.get("after_seq") or 0))
                        except (TypeError, ValueError):
                            requested_cursor = 0
                        reset_cursor = requested_cursor > bus.latest_seq
                        effective_cursor = 0 if reset_cursor else requested_cursor
                        # Acknowledge the effective cursor before replay starts,
                        # so the client can reset stale local state first.
                        await send(
                            {
                                "type": "subscribed",
                                "book_id": str(data.get("book_id") or "").strip(),
                                "latest_seq": bus.latest_seq,
                                "reset": reset_cursor,
                            }
                        )
                        fanout.attach_book(book_id, resolved, after_seq=effective_cursor)

                elif msg_type == "create":
                    if not can_create_book():
                        await send({"type": "error", "content": "Book creation is not allowed"})
                        continue
                    engine = get_book_engine()
                    book, proposal = await engine.create_book(
                        user_intent=str(data.get("user_intent") or ""),
                        source_refs=data.get("source_refs") or [],
                        chat_session_id=str(data.get("chat_session_id") or ""),
                        chat_selections=data.get("chat_selections") or [],
                        notebook_refs=data.get("notebook_refs") or [],
                        knowledge_bases=data.get("knowledge_bases") or [],
                        question_categories=[
                            int(c) for c in (data.get("question_categories") or [])
                        ],
                        question_entries=[int(e) for e in (data.get("question_entries") or [])],
                        language=str(data.get("language") or "en"),
                        fallback_language=str(data.get("fallback_language") or "en"),
                        depth=str(data.get("depth") or "standard"),
                        stream=creation_bus,
                    )
                    # From here on this book has a stream of its own.
                    storage = getattr(engine, "storage", None) or get_book_storage()
                    created_bus = get_book_bus(book.id, storage=storage)
                    created = ResolvedBook(
                        engine=engine, source="own", permission="edit", can_edit=True,
                        can_delete=True, learning=storage,
                        record=book_registry.find_book_by_location(
                            storage.path_service.get_book_dir(), book_registry.dir_name_for(book.id),
                        ),
                    )
                    fanout.attach_book(book.id, created, after_seq=created_bus.latest_seq)
                    await send(
                        {
                            "type": "create_result",
                            "book": _book_payload(book, created),
                            "proposal": proposal.model_dump(mode="json"),
                        }
                    )

                elif msg_type == "confirm_proposal":
                    if resolved is None or not resolved.can_edit:
                        raise ValueError("Book not found")
                    engine = resolved.engine
                    action_bus = get_book_bus(
                        book_id, storage=getattr(engine, "storage", None) or resolved.learning,
                    )
                    fanout.attach_book(book_id, resolved, after_seq=action_bus.latest_seq)
                    revision = _claim_content_mutation(
                        resolved,
                        book_id,
                        data.get("expected_revision"),
                        "confirm_proposal",
                    )
                    edited: BookProposal | None = None
                    if data.get("proposal"):
                        edited = BookProposal.model_validate(data["proposal"])
                    book, spine = await engine.confirm_proposal(
                        book_id=book_id,
                        edited_proposal=edited,
                    )
                    await send(
                        {
                            "type": "confirm_proposal_result",
                            "book": book.model_dump(mode="json"),
                            "spine": spine.model_dump(mode="json"),
                            "book_revision": revision,
                        }
                    )

                elif msg_type == "confirm_spine":
                    if resolved is None or not resolved.can_edit:
                        raise ValueError("Book not found")
                    engine = resolved.engine
                    action_bus = get_book_bus(
                        book_id, storage=getattr(engine, "storage", None) or resolved.learning,
                    )
                    fanout.attach_book(book_id, resolved, after_seq=action_bus.latest_seq)
                    revision = _claim_content_mutation(
                        resolved,
                        book_id,
                        data.get("expected_revision"),
                        "confirm_spine",
                    )
                    edited_spine: Spine | None = None
                    if data.get("spine"):
                        edited_spine = Spine.model_validate(data["spine"])
                    pages = await engine.confirm_spine(
                        book_id=book_id,
                        edited_spine=edited_spine,
                        auto_compile=bool(data.get("auto_compile", True)),
                    )
                    await send(
                        {
                            "type": "confirm_spine_result",
                            "pages": [p.model_dump(mode="json") for p in pages],
                            "book_revision": revision,
                        }
                    )

                elif msg_type == "compile_page":
                    if resolved is None or not resolved.can_edit:
                        raise ValueError("Book not found")
                    engine = resolved.engine
                    action_bus = get_book_bus(
                        book_id, storage=getattr(engine, "storage", None) or resolved.learning,
                    )
                    fanout.attach_book(book_id, resolved, after_seq=action_bus.latest_seq)
                    revision = _claim_content_mutation(
                        resolved,
                        book_id,
                        data.get("expected_revision"),
                        "compile_page",
                        extra={"page_id": str(data.get("page_id") or "")},
                        strict=False,
                    )
                    page = await engine.compile_page(
                        book_id=book_id,
                        page_id=str(data.get("page_id") or ""),
                        force=bool(data.get("force", False)),
                    )
                    await send(
                        {
                            "type": "compile_page_result",
                            "page": page.model_dump(mode="json"),
                            "book_revision": revision,
                        }
                    )

                elif msg_type == "regenerate_block":
                    if resolved is None or not resolved.can_edit:
                        raise ValueError("Book not found")
                    engine = resolved.engine
                    action_bus = get_book_bus(
                        book_id, storage=getattr(engine, "storage", None) or resolved.learning,
                    )
                    fanout.attach_book(book_id, resolved, after_seq=action_bus.latest_seq)
                    revision = _claim_content_mutation(
                        resolved,
                        book_id,
                        data.get("expected_revision"),
                        "regenerate_block",
                        extra={
                            "page_id": str(data.get("page_id") or ""),
                            "block_id": str(data.get("block_id") or ""),
                        },
                    )
                    block = await engine.regenerate_block(
                        book_id=book_id,
                        page_id=str(data.get("page_id") or ""),
                        block_id=str(data.get("block_id") or ""),
                        params_override=data.get("params_override"),
                    )
                    await send(
                        {
                            "type": "regenerate_block_result",
                            "block": block.model_dump(mode="json") if block else None,
                            "book_revision": revision,
                        }
                    )

                else:
                    await send({"type": "error", "content": f"Unknown message type: {msg_type}"})

            except BookPausedError as exc:
                await send(
                    {
                        "type": "error",
                        "content": str(exc),
                        "status": 409,
                        "code": "book_paused",
                    }
                )
            except HTTPException as exc:
                detail = exc.detail
                payload: dict[str, Any] = {
                    "type": "error",
                    "content": (
                        str(detail.get("message") or detail.get("code") or "Book action failed")
                        if isinstance(detail, dict)
                        else str(detail)
                    ),
                    "status": exc.status_code,
                }
                if isinstance(detail, dict):
                    payload.update(
                        {key: detail[key] for key in ("code", "current_revision") if key in detail}
                    )
                await send(payload)
            except Exception as exc:
                logger.error(f"book ws action {msg_type} failed: {exc}", exc_info=True)
                await send({"type": "error", "content": str(exc)})
            finally:
                if activity is not None:
                    activity.close()

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error(f"Book WS connection error: {exc}", exc_info=True)
    finally:
        closed = True
        await fanout.close()
        await creation_bus.close()
        try:
            await ws.close()
        except Exception:
            logger.debug("Book WebSocket was already closed", exc_info=True)
        if user_token is not None:
            try:
                reset_current_user(user_token)
            except Exception:
                logger.debug("Could not reset Book WebSocket user context", exc_info=True)
