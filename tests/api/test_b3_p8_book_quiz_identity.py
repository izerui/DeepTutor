"""P8 item 1 — transferred book quiz writes to the new owner's overlay.

After ``transfer_book_ownership`` the new owner and admin answer quiz
questions through the canonical shared path. Each answer must land in
the reader's own overlay progress — never in the original author's
personal session.

P8 item 2 — quiz attempt uses real BookStorage + BookInputs.chat_session_id
when no page-level chat session exists (replacing the SimpleNamespace stub
in test_book_quiz_attempt_notebook.py:200).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from starlette.testclient import TestClient
import pytest

from deeptutor.api.routers import book as book_router
from deeptutor.book.learning_overlay import BookLearningOverlay
from deeptutor.book.models import (
    Block,
    BlockStatus,
    BlockType,
    Book,
    BookInputs,
    Chapter,
    Page,
    PageStatus,
    Progress,
    Spine,
)
from deeptutor.book.storage import BookStorage
import deeptutor.services.session as session_package
from deeptutor.services.session.sqlite_store import SQLiteSessionStore


# ── Helpers ──────────────────────────────────────────────────────────

QUIZ_QUESTION = {
    "question_id": "q1",
    "question": "Which chapter?",
    "question_type": "choice",
    "options": {"A": "One", "B": "Two"},
    "correct_answer": "B",
    "explanation": "The book has two chapters.",
    "difficulty": "easy",
}


def _write_book(storage: BookStorage, book_id: str, title: str) -> None:
    storage.save_book(Book(id=book_id, title=title, page_count=1))
    storage.save_spine(
        Spine(
            book_id=book_id,
            chapters=[Chapter(id="ch1", title=title, page_ids=["pg1"])],
        )
    )
    storage.save_page(
        Page(
            id="pg1",
            book_id=book_id,
            chapter_id="ch1",
            title="Page 1",
            status=PageStatus.READY,
            blocks=[
                Block(
                    id="quiz_blk",
                    type=BlockType.QUIZ,
                    status=BlockStatus.READY,
                    payload={"questions": [QUIZ_QUESTION]},
                ),
            ],
        )
    )


class _OverlayLearning:
    """Thin wrapper mimicking ResolvedBook.learning for shared books."""

    def __init__(self, overlay: BookLearningOverlay) -> None:
        self._overlay = overlay

    def load_progress(self, book_id: str) -> Progress:
        return self._overlay.load_progress(book_id) or Progress(book_id=book_id)

    def save_progress(self, progress: Progress) -> None:
        self._overlay.save_progress(progress)

    def load_page_chat_sessions(self, book_id: str) -> dict[str, str]:
        return self._overlay.load_page_chat_sessions(book_id)


# ── P8 item 1: transferred book quiz goes to the overlay ────────────


@pytest.mark.parametrize("reader_label", ["new_owner", "admin"])
def test_transferred_book_quiz_writes_to_reader_overlay(
    tmp_path: Path, monkeypatch, reader_label: str,
) -> None:
    """After transfer, each reader's quiz attempt lands in their own overlay."""
    from deeptutor.book.engine import BookEngine
    from deeptutor.services.path_service import PathService

    # Set up isolated book storage and engine for the original author.
    author_root = tmp_path / "author"
    author_root.mkdir()
    author_ps = PathService(workspace_root=author_root)
    author_ps.ensure_all_directories()
    author_storage = BookStorage(path_service=author_ps)
    _write_book(author_storage, "bk_xfer", "Transferred")

    # Set up the reader's overlay directory.
    reader_root = tmp_path / reader_label
    reader_root.mkdir()
    reader_ps = PathService(workspace_root=reader_root)
    reader_ps.ensure_all_directories()
    overlay = BookLearningOverlay(reader_ps)
    learning = _OverlayLearning(overlay)

    engine = BookEngine(storage=author_storage)
    resolved = SimpleNamespace(
        engine=engine,
        source="shared",
        is_shared=True,
        can_edit=reader_label == "admin",
        learning=learning,
    )
    resolved.load_progress = learning.load_progress

    store = SQLiteSessionStore(db_path=tmp_path / "sessions.db")
    monkeypatch.setattr(book_router, "_resolve_book_or_404", lambda *a, **kw: resolved)
    monkeypatch.setattr(session_package, "get_sqlite_session_store", lambda: store)

    app = FastAPI()
    app.include_router(book_router.router, prefix="/api")

    payload = {
        "book_id": "bk_xfer",
        "page_id": "pg1",
        "block_id": "quiz_blk",
        "question_id": "q1",
        "user_answer": "B",
        "is_correct": True,
    }
    with TestClient(app) as client:
        response = client.post("/api/books/quiz-attempt", json=payload)

    assert response.status_code == 200, response.text
    # Progress persisted in the reader's overlay.
    reader_progress = overlay.load_progress("bk_xfer")
    assert reader_progress is not None
    assert len(reader_progress.quiz_attempts) == 1
    assert reader_progress.quiz_attempts[0].question_id == "q1"
    # Author's own store must remain untouched.
    assert author_storage.load_progress("bk_xfer") is None


# ── P8 item 2: quiz with real BookStorage + BookInputs.chat_session_id ──


def test_quiz_uses_real_book_inputs_session_when_no_page_chat(
    tmp_path: Path, monkeypatch,
) -> None:
    """BookInputs.chat_session_id from real storage routes the assessment record."""
    from deeptutor.book.engine import BookEngine
    from deeptutor.services.path_service import PathService

    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    ps = PathService(workspace_root=ws_root)
    ps.ensure_all_directories()
    storage = BookStorage(path_service=ps)
    _write_book(storage, "bk_real", "Real Inputs")
    storage.save_inputs("bk_real", BookInputs(chat_session_id="real-session"))

    store = SQLiteSessionStore(db_path=tmp_path / "sessions.db")
    asyncio.run(store.create_session(session_id="real-session", title="Source session"))

    engine = BookEngine(storage=storage)
    resolved = SimpleNamespace(
        engine=engine,
        source="own",
        is_shared=False,
        can_edit=True,
        learning=storage,
    )
    resolved.load_progress = lambda book_id: storage.load_progress(book_id) or Progress(
        book_id=book_id
    )

    monkeypatch.setattr(book_router, "_resolve_book_or_404", lambda *a, **kw: resolved)
    monkeypatch.setattr(session_package, "get_sqlite_session_store", lambda: store)

    app = FastAPI()
    app.include_router(book_router.router, prefix="/api")
    with TestClient(app) as client:
        response = client.post(
            "/api/books/quiz-attempt",
            json={
                "book_id": "bk_real",
                "page_id": "pg1",
                "block_id": "quiz_blk",
                "question_id": "q1",
                "user_answer": "B",
                "is_correct": True,
            },
        )
    assert response.status_code == 200, response.text
    entries = asyncio.run(store.list_notebook_entries(source="book"))
    assert entries["total"] == 1
    entry = entries["items"][0]
    assert entry["session_id"] == "real-session"
    assert entry["session_title"] == "Source session"
