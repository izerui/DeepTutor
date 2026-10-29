"""B2-17 (R2): a student's own reading state on a teacher path's reference-only book.

Through the path (``X-DeepTutor-Mastery-Template``) an assigned student may
record reading progress, bookmarks, in-book quiz attempts and learning
captures for a book they cannot read on their own. Those writes land in the
student's own per-reader overlay keyed by the canonical book id — never in the
owner's book directory — and are refused once the path is closed or for a
book the applied revision does not cite.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from deeptutor.api.routers import mastery_path
from deeptutor.book import registry as book_registry
from deeptutor.learning.models import TopicSource, TopicSourceKind
from deeptutor.teaching.service import TeachingService

BOOK_ID = "bk_ref"
OTHER_BOOK_ID = "bk_unlisted"
QUIZ_QUESTION = {
    "question_id": "q_nodes",
    "question": "Which points of a standing wave stay still?",
    "question_type": "choice",
    "options": {"A": "Antinodes", "B": "Nodes"},
    "correct_answer": "B",
}


def _auth(name: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {name}"}


def _write_book(book_id: str, title: str) -> None:
    from deeptutor.book.models import (
        Block,
        BlockStatus,
        BlockType,
        Book,
        Chapter,
        Page,
        PageStatus,
        Spine,
    )
    from deeptutor.book.storage import get_book_storage

    storage = get_book_storage()
    storage.save_book(Book(id=book_id, title=title, page_count=2))
    storage.save_spine(
        Spine(
            book_id=book_id,
            chapters=[Chapter(id="ch_1", title=title, page_ids=["pg_1", "pg_2"])],
        )
    )
    for page_id in ("pg_1", "pg_2"):
        storage.save_page(
            Page(
                id=page_id,
                book_id=book_id,
                chapter_id="ch_1",
                title=f"Page {page_id}",
                status=PageStatus.READY,
                blocks=[
                    Block(
                        id=f"blk_{page_id}",
                        type=BlockType.TEXT,
                        status=BlockStatus.READY,
                        payload={"body": "Nodes stay still."},
                    ),
                    Block(
                        id=f"quiz_{page_id}",
                        type=BlockType.QUIZ,
                        status=BlockStatus.READY,
                        payload={"questions": [QUIZ_QUESTION]},
                    ),
                ],
            )
        )
    assert book_registry.resolve_book_ref(book_id) is not None


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture
def ref_book(teaching_users, as_account, teacher_draft, publish_template):
    """Tina's two private books; the path cites only ``bk_ref`` (reference only)."""
    from deeptutor.book.learning_overlay import BookLearningOverlay
    from deeptutor.book.storage import get_book_storage
    from deeptutor.multi_user.paths import get_current_path_service

    with as_account("tina"):
        _write_book(BOOK_ID, "Waves")
        _write_book(OTHER_BOOK_ID, "Unlisted")
        owner_dir = get_book_storage().book_root(BOOK_ID)
    teacher_draft(
        sources=(
            TopicSource(
                id="b0", kind=TopicSourceKind.BOOK, source_id=BOOK_ID, label="Waves", position=0
            ),
        )
    )
    template_id = publish_template(
        students=("sara",), book_choices={BOOK_ID: "reference_only"}
    )["template"]["id"]
    with as_account("sara"):
        asyncio.run(mastery_path.list_topics())
        overlay_dir = BookLearningOverlay(get_current_path_service()).book_root(BOOK_ID)
    assert book_registry.get_book(BOOK_ID).visibility == "private"
    assert book_registry.get_book(OTHER_BOOK_ID).visibility == "private"
    return SimpleNamespace(
        template_id=template_id,
        owner_dir=owner_dir,
        overlay_dir=overlay_dir,
        scoped={**_auth("sara"), "X-DeepTutor-Mastery-Template": template_id},
    )


def _write_all(client, headers, book_id: str = BOOK_ID) -> dict[str, int]:
    visit = client.post(
        "/api/books/progress/visit", json={"book_id": book_id, "page_id": "pg_1"}, headers=headers
    )
    bookmark = client.post(
        "/api/books/progress/bookmark",
        json={"book_id": book_id, "page_id": "pg_2"},
        headers=headers,
    )
    quiz = client.post(
        "/api/books/quiz-attempt",
        json={
            "book_id": book_id,
            "page_id": "pg_1",
            "block_id": "blk_pg_1",
            "question_id": "q1",
            "user_answer": "nodes",
            "submission_id": "sub-1",
        },
        headers=headers,
    )
    capture = client.post(
        f"/api/books/{book_id}/learning-captures",
        json={"page_id": "pg_1", "block_id": "blk_pg_1", "source_text": "Nodes stay still."},
        headers=headers,
    )
    return {
        "visit": visit.status_code,
        "bookmark": bookmark.status_code,
        "quiz": quiz.status_code,
        "capture": capture.status_code,
    }


def test_student_state_lands_in_the_students_own_store(ref_book, teaching_http):
    client = teaching_http.client
    before = _snapshot(ref_book.owner_dir)
    assert before, "the owner's book directory holds the book"

    statuses = _write_all(client, ref_book.scoped)
    assert statuses == {"visit": 200, "bookmark": 200, "quiz": 200, "capture": 200}

    listed = client.get(f"/api/books/{BOOK_ID}/learning-captures", headers=ref_book.scoped)
    assert listed.status_code == 200
    captures = listed.json()["captures"]
    assert [c["source_text"] for c in captures] == ["Nodes stay still."]
    patched = client.patch(
        f"/api/books/{BOOK_ID}/learning-captures/{captures[0]['id']}",
        json={"user_note": "mine"},
        headers=ref_book.scoped,
    )
    assert patched.status_code == 200
    assert patched.json()["capture"]["user_note"] == "mine"

    # Reads return the student's own state.
    detail = client.get(f"/api/books/{BOOK_ID}", headers=ref_book.scoped)
    assert detail.status_code == 200
    progress = detail.json()["progress"]
    assert progress["visited_page_ids"] == ["pg_1"]
    assert progress["bookmarked_page_ids"] == ["pg_2"]
    assert [a["question_id"] for a in progress["quiz_attempts"]] == ["q1"]

    # Stored in the student's overlay, keyed by the canonical book id ...
    assert (ref_book.overlay_dir / "progress.json").is_file()
    assert (ref_book.overlay_dir / "learning_captures.json").is_file()
    assert ref_book.overlay_dir.name == f"book_{BOOK_ID}"
    # ... and the owner's book directory is byte-for-byte unchanged.
    assert _snapshot(ref_book.owner_dir) == before
    owner_view = client.get(f"/api/books/{BOOK_ID}", headers=_auth("tina")).json()["progress"]
    assert owner_view["visited_page_ids"] == []
    assert owner_view["bookmarked_page_ids"] == []
    assert owner_view["quiz_attempts"] == []


def test_without_the_path_the_student_still_cannot_write(ref_book, teaching_http):
    statuses = _write_all(teaching_http.client, _auth("sara"))
    assert set(statuses.values()) == {404}
    assert not ref_book.overlay_dir.exists()


def test_an_unassigned_student_naming_the_path_is_refused(ref_book, teaching_http):
    headers = {**_auth("sam"), "X-DeepTutor-Mastery-Template": ref_book.template_id}
    assert set(_write_all(teaching_http.client, headers).values()) == {404}


def test_a_book_the_revision_does_not_cite_is_refused(ref_book, teaching_http):
    before = _snapshot(ref_book.owner_dir.parent / f"book_{OTHER_BOOK_ID}")
    statuses = _write_all(teaching_http.client, ref_book.scoped, book_id=OTHER_BOOK_ID)
    assert set(statuses.values()) == {404}
    listed = teaching_http.client.get(
        f"/api/books/{OTHER_BOOK_ID}/learning-captures", headers=ref_book.scoped
    )
    assert listed.status_code == 404
    assert _snapshot(ref_book.owner_dir.parent / f"book_{OTHER_BOOK_ID}") == before


@pytest.mark.parametrize("close", ["revoke", "archive"])
def test_closing_the_path_refuses_writes_and_keeps_the_students_data(
    ref_book, teaching_http, as_account, teaching_users, close
):
    client = teaching_http.client
    assert set(_write_all(client, ref_book.scoped).values()) == {200}
    saved = (ref_book.overlay_dir / "progress.json").read_bytes()

    with as_account("tina"):
        if close == "revoke":
            TeachingService().revoke(ref_book.template_id, teaching_users["sara"]["id"])
        else:
            TeachingService().archive(ref_book.template_id)

    assert set(_write_all(client, ref_book.scoped).values()) == {404}
    assert (
        client.get(f"/api/books/{BOOK_ID}/learning-captures", headers=ref_book.scoped).status_code
        == 404
    )
    # The student's own data is retained, untouched.
    assert (ref_book.overlay_dir / "progress.json").read_bytes() == saved


def test_the_owner_naming_the_path_keeps_using_the_book_store(ref_book, teaching_http):
    """Owners are unchanged: their progress stays in their own book storage."""
    headers = {**_auth("tina"), "X-DeepTutor-Mastery-Template": ref_book.template_id}
    visit = teaching_http.client.post(
        "/api/books/progress/visit", json={"book_id": BOOK_ID, "page_id": "pg_1"}, headers=headers
    )
    assert visit.status_code == 200
    assert "progress.json" in _snapshot(ref_book.owner_dir)
    assert not ref_book.overlay_dir.exists()


# ---- Fix round 1: page chat binding and the graded quiz (review Minors 1, 2) ----


def test_page_chat_binding_lands_in_the_students_own_store(ref_book, teaching_http):
    client = teaching_http.client
    before = _snapshot(ref_book.owner_dir)
    body = {"book_id": BOOK_ID, "page_id": "pg_1", "session_id": "sara-chat-1"}

    assert client.post("/api/books/page-chat-session", json=body, headers=_auth("sara")).status_code == 404
    linked = client.post("/api/books/page-chat-session", json=body, headers=ref_book.scoped)
    assert linked.status_code == 200, linked.text
    assert linked.json()["book"]["metadata"]["page_chat_sessions"] == {"pg_1": "sara-chat-1"}

    assert (ref_book.overlay_dir / "page_chat_sessions.json").is_file()
    assert _snapshot(ref_book.owner_dir) == before
    detail = client.get(f"/api/books/{BOOK_ID}", headers=ref_book.scoped).json()
    assert detail["book"]["metadata"]["page_chat_sessions"] == {"pg_1": "sara-chat-1"}
    owner = client.get(f"/api/books/{BOOK_ID}", headers=_auth("tina")).json()
    assert "sara-chat-1" not in str(owner["book"].get("metadata"))

    other = client.post(
        "/api/books/page-chat-session",
        json={**body, "book_id": OTHER_BOOK_ID},
        headers=ref_book.scoped,
    )
    assert other.status_code == 404


def test_page_chat_binding_is_refused_after_revoke(
    ref_book, teaching_http, as_account, teaching_users
):
    body = {"book_id": BOOK_ID, "page_id": "pg_1", "session_id": "sara-chat-1"}
    client = teaching_http.client
    assert client.post("/api/books/page-chat-session", json=body, headers=ref_book.scoped).status_code == 200
    saved = (ref_book.overlay_dir / "page_chat_sessions.json").read_bytes()
    with as_account("tina"):
        TeachingService().revoke(ref_book.template_id, teaching_users["sara"]["id"])
    assert client.post(
        "/api/books/page-chat-session", json={**body, "session_id": "x"}, headers=ref_book.scoped
    ).status_code == 404
    assert (ref_book.overlay_dir / "page_chat_sessions.json").read_bytes() == saved


def test_a_graded_quiz_answer_lands_in_the_students_own_question_bank(
    ref_book, teaching_http, as_account
):
    from deeptutor.services.session import get_sqlite_session_store

    answered = teaching_http.client.post(
        "/api/books/quiz-attempt",
        json={
            "book_id": BOOK_ID,
            "page_id": "pg_1",
            "block_id": "quiz_pg_1",
            "question_id": "q_nodes",
            "user_answer": "B",
            "is_correct": True,
            "submission_id": "sub-graded",
        },
        headers=ref_book.scoped,
    )
    assert answered.status_code == 200, answered.text

    def entries(name: str) -> list[dict]:
        with as_account(name):
            store = get_sqlite_session_store()
            items = asyncio.run(store.list_notebook_entries(source="book"))["items"]
        return items

    mine = entries("sara")
    assert [(e["question_id"], e["user_answer"], e["is_correct"]) for e in mine] == [
        ("q_nodes", "B", True)
    ]
    assert mine[0]["material_id"] == BOOK_ID
    assert entries("tina") == []


def test_an_admin_cannot_use_a_teachers_private_path_book(ref_book, teaching_http):
    """Spec 2026-10-29 D2: an unassigned admin cannot read the teacher's private book."""
    before = _snapshot(ref_book.owner_dir)
    headers = {**_auth("adam"), "X-DeepTutor-Mastery-Template": ref_book.template_id}
    statuses = _write_all(teaching_http.client, headers)
    assert set(statuses.values()) == {404}
    assert _snapshot(ref_book.owner_dir) == before
