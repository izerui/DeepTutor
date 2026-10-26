"""Shared quizzes bind evidence to the reader, never the author's chat IDs."""

import asyncio
from contextlib import contextmanager

import pytest

from deeptutor.api.routers import auth
from deeptutor.book.models import Block, BookInputs
from deeptutor.multi_user.context import reset_current_user
from deeptutor.services.session import get_sqlite_session_store
from tests.api.test_book_shared_access import shared_env  # noqa: F401


@contextmanager
def actor_scope(actor):
    token = auth._install_current_user(auth.decode_token(actor))
    try:
        yield get_sqlite_session_store()
    finally:
        reset_current_user(token)


@pytest.mark.parametrize("actor", ["alice", "reader"])
@pytest.mark.parametrize("linked", [False, True])
def test_shared_quiz_keeps_reader_evidence_and_progress_isolated(shared_env, actor, linked):
    client, canonical = shared_env
    book = canonical.load_book("bk_shared")
    book.metadata["page_chat_sessions"] = {"pg_1": "author-chat"}
    canonical.save_book(book)
    canonical.save_inputs("bk_shared", BookInputs(chat_session_id="author-legacy"))
    page = canonical.load_page("bk_shared", "pg_1")
    page.blocks.append(Block(id="quiz", type="quiz", status="ready", payload={
        "questions": [{"question_id": "q1", "question": "Two plus two?",
                       "question_type": "choice", "options": {"A": "4", "B": "5"},
                       "correct_answer": "A"}],
    }))
    canonical.save_page(page)
    headers = {"Authorization": f"Bearer {actor}"}
    with actor_scope(actor) as store:
        # A matching ID in the reader's store must not make the author's
        # metadata an authorized linkage to this unrelated conversation.
        asyncio.run(store.create_session(session_id="author-chat", title="Unrelated"))
        if linked:
            asyncio.run(store.create_session(session_id="reader-chat", title="Reader"))
    if linked:
        response = client.post("/api/books/page-chat-session", headers=headers, json={
            "book_id": "book:bk_shared", "page_id": "pg_1", "session_id": "reader-chat",
        })
        assert response.status_code == 200, response.text
    response = client.post("/api/books/quiz-attempt", headers=headers, json={
        "book_id": "book:bk_shared", "page_id": "pg_1", "block_id": "quiz",
        "question_id": "q1", "user_answer": "A", "is_correct": True,
        "submission_id": "reader-attempt",
    })
    assert response.status_code == 200, response.text
    assert len(response.json()["progress"]["quiz_attempts"]) == 1
    with actor_scope(actor) as store:
        entries = asyncio.run(store.list_notebook_entries(source="book"))
        assert entries["total"] == 1
        entry = entries["items"][0]
        assert entry["session_id"] == ("reader-chat" if linked else "")
        assert entry["origin_type"] == ("conversation" if linked else "document_analysis")
        assert entry["origin_ref"] == ("reader-chat" if linked else "book:bk_shared")
        assert asyncio.run(store.list_assessment_attempts("author-chat", question_id="q1")) == []
    for other in ("teacher", "bob"):
        detail = client.get("/api/books/book:bk_shared", headers={"Authorization": f"Bearer {other}"})
        assert detail.status_code == 200, detail.text
        assert detail.json()["progress"]["quiz_attempts"] == []
        with actor_scope(other) as store:
            assert asyncio.run(store.list_notebook_entries(source="book"))["total"] == 0
    assert canonical.load_book("bk_shared").metadata == book.metadata
    assert canonical.load_inputs("bk_shared").chat_session_id == "author-legacy"
