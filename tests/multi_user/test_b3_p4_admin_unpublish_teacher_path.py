"""B3-12 follow-up: an admin's set-private on an archived-source book still goes through R1.

A published teacher path cites Tom's public book, stored in a workspace he
then archives. The admin's first request answers the counts-only 409; the
confirmation with the counts shown succeeds and revokes the path's reference.
"""

from __future__ import annotations

import pytest

from deeptutor.book import registry as book_registry
from deeptutor.learning.models import TopicSource, TopicSourceKind
from tests.multi_user.test_owner_withdrawal_books import _assert_counts_only, _auth


@pytest.fixture
def archived_cited_book(as_account, teacher_draft, publish_template):
    from deeptutor.book.models import Book, Page
    from deeptutor.book.storage import get_book_storage
    from deeptutor.multi_user import book_access
    from deeptutor.services.workspace import ContentWorkspaceService
    from deeptutor.services.workspace.context import workspace_context

    with as_account("tom"):
        service = ContentWorkspaceService()
        source_id = service.create_workspace("Synthetic source")["workspace_id"]
        with workspace_context(source_id):
            store = get_book_storage()
            store.save_book(Book(id="bk_tom", title="Optics"))
            store.save_page(Page(id="pg_one", book_id="bk_tom", title="Lenses"))
            record = book_access.resolve_book("bk_tom").record
            book_registry.set_book_kb_links(record.book_id, {})
            book_registry.set_book_visibility(record.book_id, "public")
    teacher_draft(sources=(TopicSource(
        id="b0", kind=TopicSourceKind.BOOK, source_id="bk_tom", label="Optics", position=0,
    ),))
    result = publish_template(students=("sara", "sam"))
    assert result["warnings"] == []
    template_id = result["template"]["id"]
    assert book_registry.list_referenced_books("mastery_template", template_id)
    with as_account("tom"):
        service.update_workspace(source_id, archived=True)
    return template_id, store


def test_admin_unpublish_of_archived_book_needs_the_teacher_path_confirmation(
    archived_cited_book, teaching_http, teaching_users,
):
    template_id, store = archived_cited_book
    client = teaching_http.client
    manifest = (store.book_root("bk_tom") / "manifest.json").read_bytes()

    # The owner (not an admin) is still refused by the archived source.
    owner = client.put("/api/books/book:bk_tom/visibility", json={"visibility": "private"},
                       headers=_auth("tom"))
    assert owner.status_code == 403, owner.text

    first = client.put("/api/books/book:bk_tom/visibility", json={"visibility": "private"},
                       headers=_auth("adam"))
    assert first.status_code == 409, first.text
    _assert_counts_only(first.json()["detail"], teaching_users, template_id, paths=1, students=2)
    assert book_registry.get_book("bk_tom").visibility == "public"
    assert book_registry.list_unavailable_book_references("mastery_template", template_id) == []

    stale = client.put("/api/books/book:bk_tom/visibility", headers=_auth("adam"), json={
        "visibility": "private", "confirm": True, "expected_paths": 1, "expected_students": 5,
    })
    assert stale.status_code == 409 and stale.json()["detail"]["changed"] is True
    assert book_registry.get_book("bk_tom").visibility == "public"

    confirmed = client.put("/api/books/book:bk_tom/visibility", headers=_auth("adam"), json={
        "visibility": "private", "confirm": True, "expected_paths": 1, "expected_students": 2,
    })
    assert confirmed.status_code == 200, confirmed.text
    assert book_registry.get_book("bk_tom").visibility == "private"
    [revoked] = book_registry.list_unavailable_book_references("mastery_template", template_id)
    assert revoked.reason == "made_private"
    # The archived source stays frozen.
    assert (store.book_root("bk_tom") / "manifest.json").read_bytes() == manifest
