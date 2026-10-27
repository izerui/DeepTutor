"""Rule R1 (B2-19) for books: the owner may always make private or delete a book.

Other accounts' references are revoked; published teacher paths need one
explicit confirmation, answered first with a counts-only 409.
"""

from __future__ import annotations

import json

import pytest

from deeptutor.book import registry as book_registry
from deeptutor.learning.models import TopicSource, TopicSourceKind

CODE = "referenced_by_teacher_paths"


def _auth(name: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {name}"}


def _assert_counts_only(detail, teaching_users, template_id: str, *, paths: int, students: int):
    assert detail["code"] == CODE and detail["requires_confirmation"] is True
    assert (detail["affected_paths"], detail["affected_students"]) == (paths, students)
    text = json.dumps(detail)
    ids = [teaching_users[name]["id"] for name in ("tina", "sara", "sam")]
    for foreign in [*ids, "tina", "sara", "sam", template_id]:
        assert foreign not in text


def _materials_unavailable(teaching_http, template_id: str) -> list[str]:
    rows = teaching_http.client.get(
        "/api/mastery-paths/teaching/templates", headers=_auth("tina")
    ).json()
    rows = rows.get("templates", rows) if isinstance(rows, dict) else rows
    [row] = [row for row in rows if row["id"] == template_id]
    return row["materials_unavailable"]


def _book_source(book_id: str, label: str) -> TopicSource:
    return TopicSource(id="b0", kind=TopicSourceKind.BOOK, source_id=book_id, label=label, position=0)


@pytest.fixture
def toms_book(teaching_users, as_account):
    """Tom's public book, stored in his account (real storage, so it can be deleted)."""
    from deeptutor.book.models import Book, Page
    from deeptutor.book.storage import get_book_storage
    from deeptutor.multi_user import book_access

    with as_account("tom"):
        store = get_book_storage()
        store.save_book(Book(id="bk_tom", title="Optics"))
        store.save_page(Page(id="pg_one", book_id="bk_tom", title="Lenses"))
        resolved = book_access.resolve_book("bk_tom")
        record = resolved.record
        assert record is not None
        from deeptutor.book import sharing

        sharing.set_visibility(record.book_id, "public", force=False, kb_choices={})
    return book_registry.get_book("bk_tom")


@pytest.fixture
def book_cited_by_tinas_path(toms_book, teacher_draft, publish_template):
    teacher_draft(sources=(_book_source(toms_book.book_id, "Optics"),))
    result = publish_template(students=("sara", "sam"))
    assert result["warnings"] == []
    assert book_registry.list_referenced_books("mastery_template", result["template"]["id"])
    return result["template"]["id"]


def test_book_course_references_do_not_block_the_owner(toms_book, teaching_http, teaching_users):
    book_registry.replace_book_references(
        "course", "c1", [toms_book.book_id], created_by=teaching_users["tina"]["id"]
    )
    response = teaching_http.client.put(
        "/api/books/bk_tom/visibility", json={"visibility": "private"}, headers=_auth("tom"),
    )
    assert response.status_code == 200, response.text
    [revoked] = book_registry.list_unavailable_book_references("course", "c1")
    assert revoked.reason == "made_private"


def test_withdrawing_a_book_cited_by_a_teacher_path_needs_confirmation(
    book_cited_by_tinas_path, teaching_http, teaching_users
):
    template_id = book_cited_by_tinas_path
    client = teaching_http.client
    first = client.put("/api/books/bk_tom/visibility", json={"visibility": "private"}, headers=_auth("tom"))
    assert first.status_code == 409
    _assert_counts_only(first.json()["detail"], teaching_users, template_id, paths=1, students=2)
    assert book_registry.get_book("bk_tom").visibility == "public"

    second = client.put(
        "/api/books/bk_tom/visibility", json={"visibility": "private", "confirm": True},
        headers=_auth("tom"),
    )
    assert second.status_code == 200, second.text
    [revoked] = book_registry.list_unavailable_book_references("mastery_template", template_id)
    assert revoked.reason == "made_private"
    assert _materials_unavailable(teaching_http, template_id) == ["Optics"]


def test_deleting_a_book_cited_by_a_teacher_path_needs_confirmation(
    book_cited_by_tinas_path, teaching_http, teaching_users
):
    template_id = book_cited_by_tinas_path
    client = teaching_http.client
    first = client.delete("/api/books/bk_tom", headers=_auth("tom"))
    assert first.status_code == 409
    _assert_counts_only(first.json()["detail"], teaching_users, template_id, paths=1, students=2)
    assert book_registry.get_book("bk_tom") is not None

    second = client.delete("/api/books/bk_tom?confirm=true", headers=_auth("tom"))
    assert second.status_code == 200, second.text
    assert book_registry.get_book("bk_tom") is None
    [revoked] = book_registry.list_unavailable_book_references("mastery_template", template_id)
    assert revoked.reason == "book_deleted"
    assert _materials_unavailable(teaching_http, template_id) == ["Optics"]


def test_book_references_and_sharing_list_only_the_owners_own_references(
    book_cited_by_tinas_path, teaching_http, teaching_users
):
    tina = teaching_users["tina"]["id"]
    for path in ("/api/books/bk_tom/references", "/api/books/bk_tom/sharing"):
        body = teaching_http.client.get(path, headers=_auth("tom")).json()
        assert body["references"] == []
        assert body["other_references"] == {"total": 1, "by_kind": {"mastery_template": 1}}
        text = json.dumps(body)
        assert book_cited_by_tinas_path not in text and tina not in text
