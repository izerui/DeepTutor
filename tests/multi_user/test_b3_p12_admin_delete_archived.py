"""B3-12 (user choice a): an admin deletes a public book whose source workspace is archived.

The deletion only retires the registry identity: the row, its intent and
every reference (revoked as ``book_deleted``) go in one write transaction,
while the archived files stay byte-for-byte frozen. Once the workspace is
restored, the owner's book comes back as a private local book under a fresh
registration. Non-admins (the owner included) keep their refusal.
"""

from __future__ import annotations

import hashlib

from deeptutor.book import registry as book_registry
from deeptutor.services.workspace import ContentWorkspaceService
from tests.api.test_b3_p4_admin_unpublish import archived_public_book
from tests.api.test_book_archived_source import (  # noqa: F401
    actor_scope,
    archive_env,
    headers,
)
from tests.api.test_book_shared_access import shared_env  # noqa: F401
from tests.multi_user.test_b3_p4_admin_unpublish_teacher_path import (  # noqa: F401
    archived_cited_book,
)
from tests.multi_user.test_owner_withdrawal_books import _assert_counts_only, _auth


def tree_hashes(storage) -> dict[str, str]:
    """Every file under the source workspace root, with its content hash."""
    root = storage.path_service.workspace_root
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def listed(client, actor, book_id, workspace=""):
    books = client.get("/api/books", headers=headers(actor, workspace)).json()["books"]
    return next((book for book in books if book["id"] == book_id), None)


def test_admin_deletes_archived_public_book_by_unregistering_only(archive_env):
    client, _ = archive_env
    source_id, source, record = archived_public_book()
    before = tree_hashes(source)
    assert before, "the archived workspace must hold files"

    # Capabilities: the admin may delete, the owner may not.
    assert listed(client, "root", record.book_id)["can_delete"] is True
    owner_view = client.get("/api/books/book:bk_archive", headers=headers("teacher"))
    assert owner_view.status_code == 200 and owner_view.json()["book"]["can_delete"] is False

    # The owner (not an admin) is still refused; nothing changes.
    for workspace in ("", source_id):
        denied = client.delete("/api/books/book:bk_archive", headers=headers("teacher", workspace))
        assert denied.status_code in {403, 404}, denied.text
    assert book_registry.get_book(record.book_id) is not None
    assert book_registry.list_unavailable_book_references("course", "c1") == []

    response = client.delete("/api/books/book:bk_archive", headers=headers("root"))
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": True, "book_id": record.book_id}
    assert book_registry.get_book(record.book_id) is None
    [revoked] = book_registry.list_unavailable_book_references("course", "c1")
    assert revoked.reason == "book_deleted"
    # The archive stays frozen: same files, same bytes, no staging directory.
    assert tree_hashes(source) == before
    book_dir = source.path_service.get_book_dir()
    assert not [p for p in book_dir.iterdir() if p.name.startswith(".book-delete-")]
    assert listed(client, "root", record.book_id) is None
    assert client.delete("/api/books/book:bk_archive", headers=headers("root")).status_code == 404

    # Restoring the workspace brings the book back as the owner's private local book.
    with actor_scope("teacher"):
        ContentWorkspaceService().update_workspace(source_id, archived=False)
    restored = listed(client, "teacher", record.book_id, source_id)
    assert restored is not None
    assert restored["source"] == "own" and restored["visibility"] == "private"
    assert restored["can_edit"] is True and restored["can_delete"] is True
    fresh = book_registry.get_book(record.book_id)
    assert fresh is not None and fresh.visibility == "private"
    assert fresh.base_dir == str(book_dir.resolve())
    assert fresh.created_by == record.created_by
    # Still revoked: the fresh identity does not silently re-admit old referrers.
    assert [r.reason for r in book_registry.list_unavailable_book_references("course", "c1")] == [
        "book_deleted"
    ]


def test_admin_delete_of_private_archived_book_is_still_refused(archive_env):
    client, _ = archive_env
    _, source, record = archived_public_book()
    made_private = client.put("/api/books/book:bk_archive/visibility", headers=headers("root"),
                              json={"visibility": "private"})
    assert made_private.status_code == 200, made_private.text
    before = tree_hashes(source)
    response = client.delete("/api/books/book:bk_archive", headers=headers("root"))
    assert response.status_code in {403, 404}, response.text
    assert book_registry.get_book(record.book_id) is not None
    assert tree_hashes(source) == before


def test_admin_delete_of_archived_book_needs_the_teacher_path_confirmation(
    archived_cited_book, teaching_http, teaching_users,
):
    template_id, store = archived_cited_book
    client = teaching_http.client
    before = tree_hashes(store)

    owner = client.delete("/api/books/book:bk_tom", headers=_auth("tom"))
    assert owner.status_code in {403, 404}, owner.text

    first = client.delete("/api/books/book:bk_tom", headers=_auth("adam"))
    assert first.status_code == 409, first.text
    _assert_counts_only(first.json()["detail"], teaching_users, template_id, paths=1, students=2)
    assert book_registry.get_book("bk_tom") is not None
    assert book_registry.list_unavailable_book_references("mastery_template", template_id) == []

    stale = client.delete(
        "/api/books/book:bk_tom?confirm=true&expected_paths=1&expected_students=5",
        headers=_auth("adam"),
    )
    assert stale.status_code == 409 and stale.json()["detail"]["changed"] is True
    assert book_registry.get_book("bk_tom") is not None

    confirmed = client.delete(
        "/api/books/book:bk_tom?confirm=true&expected_paths=1&expected_students=2",
        headers=_auth("adam"),
    )
    assert confirmed.status_code == 200, confirmed.text
    assert book_registry.get_book("bk_tom") is None
    [revoked] = book_registry.list_unavailable_book_references("mastery_template", template_id)
    assert revoked.reason == "book_deleted"
    assert tree_hashes(store) == before
