"""B3-12: an admin may make a public book private while its source workspace is archived.

Visibility is registry metadata, so only "set private" skips the archived
source's ``content_writable`` check, and only for admins. Making public and
every content edit still need a writable source; non-admins (the owner
included) keep their 403.
"""

from deeptutor.book import registry
from deeptutor.multi_user.context import get_current_user
from deeptutor.services.workspace import ContentWorkspaceService
from deeptutor.services.workspace.context import workspace_context
from tests.api.test_book_archived_source import (  # noqa: F401
    actor_scope,
    archive_env,
    headers,
    seed_book,
    snapshot,
)
from tests.api.test_book_shared_access import shared_env  # noqa: F401


def archived_public_book():
    with actor_scope("teacher"):
        service = ContentWorkspaceService()
        source_id = service.create_workspace("Synthetic source")["workspace_id"]
        with workspace_context(source_id):
            source = seed_book()
            record = registry.register_book(
                base_dir=source.path_service.get_book_dir(), dir_name="book_bk_archive",
                title="Archive", created_by=get_current_user().id, visibility="public",
            )
            registry.set_book_kb_links(record.book_id, {})
    with actor_scope("alice"):
        registry.replace_book_references(
            "course", "c1", [record.book_id], created_by=get_current_user().id,
        )
    with actor_scope("teacher"):
        service.update_workspace(source_id, archived=True)
    return source_id, source, record


def put_visibility(client, actor, visibility):
    return client.put("/api/books/book:bk_archive/visibility", headers=headers(actor),
                      json={"visibility": visibility})


def test_admin_makes_archived_public_book_private(archive_env):
    client, _ = archive_env
    _, source, record = archived_public_book()
    before = snapshot(source)

    # The owner (not an admin) keeps the archived source's 403.
    assert put_visibility(client, "teacher", "private").status_code == 403
    # Making public still needs a writable source, even for an admin.
    assert put_visibility(client, "root", "public").status_code == 403
    assert registry.get_book(record.book_id).visibility == "public"
    assert registry.list_unavailable_book_references("course", "c1") == []

    listing = client.get("/api/books", headers=headers("root")).json()["books"]
    listed = next(book for book in listing if book["id"] == record.book_id)
    assert listed["can_set_visibility"] is True
    assert listed["can_make_public"] is False
    assert listed["can_edit"] is False
    # B3-12 (a): the admin may delete it (unregister only; see test_b3_p12_*).
    assert listed["can_delete"] is True
    sharing = client.get("/api/books/book:bk_archive/sharing", headers=headers("root"))
    assert sharing.status_code == 200, sharing.text
    assert sharing.json()["can_set_visibility"] is True
    assert sharing.json()["can_make_public"] is False
    assert client.get("/api/books/book:bk_archive/references",
                      headers=headers("root")).status_code == 200

    response = put_visibility(client, "root", "private")
    assert response.status_code == 200, response.text
    assert response.json()["visibility"] == "private"
    assert registry.get_book(record.book_id).visibility == "private"
    [revoked] = registry.list_unavailable_book_references("course", "c1")
    assert revoked.reason == "made_private"
    # Archived storage stays frozen.
    assert snapshot(source) == before

    # Once private, nothing about the archived book is manageable any more.
    listing = client.get("/api/books", headers=headers("root")).json()["books"]
    listed = next((book for book in listing if book["id"] == record.book_id), None)
    assert listed is None or listed["can_set_visibility"] is False
    assert put_visibility(client, "root", "public").status_code in {403, 404}
    assert put_visibility(client, "root", "private").status_code in {403, 404}
    assert registry.get_book(record.book_id).visibility == "private"


def test_owner_capabilities_unchanged_for_archived_public_book(archive_env):
    client, _ = archive_env
    source_id, _, record = archived_public_book()
    for workspace in (source_id, ""):
        response = client.get("/api/books/book:bk_archive", headers=headers("teacher", workspace))
        assert response.status_code == 200, response.text
        book = response.json()["book"]
        assert book["can_set_visibility"] is False
        assert book["can_make_public"] is False
    assert client.get("/api/books/book:bk_archive/sharing",
                      headers=headers("teacher")).status_code == 403
    assert registry.get_book(record.book_id).visibility == "public"
