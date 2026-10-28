"""B3-P14 item 2: owner gets an accurate 403 for archived source visibility change.

When the owner (creator, not admin) tries to set an archived-source book
private, the 403 must say the workspace is archived, not claim the user lacks
permission.
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


def _archived_public_book():
    with actor_scope("teacher"):
        service = ContentWorkspaceService()
        source_id = service.create_workspace("P14 Source")["workspace_id"]
        with workspace_context(source_id):
            source = seed_book()
            record = registry.register_book(
                base_dir=source.path_service.get_book_dir(),
                dir_name="book_bk_archive",
                title="P14 Archive",
                created_by=get_current_user().id,
                visibility="public",
            )
            registry.set_book_kb_links(record.book_id, {})
    with actor_scope("teacher"):
        service.update_workspace(source_id, archived=True)
    return source_id, source, record


def test_owner_gets_source_archived_code(archive_env):
    """The owner's 403 carries code 'source_archived', not a generic permission message."""
    client, _ = archive_env
    _, _, record = _archived_public_book()

    response = client.put(
        "/api/books/book:bk_archive/visibility",
        headers=headers("teacher"),
        json={"visibility": "private"},
    )
    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    # The detail must be a structured object with a code, not a plain string.
    assert isinstance(detail, dict), f"Expected structured detail, got: {detail}"
    assert detail["code"] == "source_archived", f"Wrong code: {detail}"
    assert "archived" in detail["message"].lower()


def test_non_owner_still_gets_generic_403(archive_env):
    """A non-owner non-admin must still get the generic permission 403."""
    client, _ = archive_env
    _, _, record = _archived_public_book()

    response = client.put(
        "/api/books/book:bk_archive/visibility",
        headers=headers("alice"),
        json={"visibility": "private"},
    )
    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    # The generic message is a plain string, not a structured object.
    assert isinstance(detail, str), f"Expected plain string detail, got: {detail}"
    assert "creator" in detail.lower() or "admin" in detail.lower()


def test_admin_listing_shows_source_workspace_name(archive_env):
    """B3-P14 item 4: archived source book shows real workspace name in listing."""
    client, _ = archive_env
    _, _, record = _archived_public_book()

    listing = client.get("/api/books", headers=headers("root")).json()["books"]
    listed = next(book for book in listing if book["id"] == record.book_id)
    # The book payload must carry the source workspace name, not "".
    ws_name = listed.get("content_workspace_name", "")
    assert ws_name == "P14 Source", (
        f"Expected 'P14 Source' workspace name, got {ws_name!r}"
    )


def test_single_book_workspace_lookup_off_event_loop(archive_env, monkeypatch):
    """B3-P14 R1: _source_workspace_name must not run on the event-loop thread."""
    import asyncio

    from deeptutor.api.routers import book as book_module

    client, _ = archive_env
    _, _, record = _archived_public_book()

    on_loop: list[bool] = []
    real = book_module._source_workspace_name

    def recording(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real(*args, **kwargs)

    monkeypatch.setattr(book_module, "_source_workspace_name", recording)
    response = client.get(
        f"/api/books/book:{record.book_id}",
        headers=headers("root"),
    )
    assert response.status_code == 200, response.text
    if on_loop:
        assert all(not v for v in on_loop), (
            "_source_workspace_name ran on the event loop"
        )
