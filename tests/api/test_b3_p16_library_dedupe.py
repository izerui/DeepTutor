"""B3-P16 item 1: an owner's public book from another workspace is listed once.

The account library merges every workspace's book index. The owner's own
workspace lists the book locally (``source=own``) and General lists the same
registry record again as a public book (``source=shared``). Only the own row
(with its edit capabilities) must remain; shared rows with another ref stay.
"""

from deeptutor.book import registry
from deeptutor.book.models import Block, BlockStatus, BlockType, Book, Page, PageStatus
from deeptutor.book.storage import get_book_storage
from deeptutor.multi_user.context import get_current_user
from deeptutor.services.workspace import ContentWorkspaceService
from deeptutor.services.workspace.context import workspace_context
from tests.api.test_book_archived_source import (  # noqa: F401
    actor_scope,
    archive_env,
    headers,
)
from tests.api.test_book_shared_access import shared_env  # noqa: F401


def _public_book(book_id: str, title: str, workspace_name: str | None):
    """A public book in a new workspace of the current user (or in General)."""

    def _seed():
        storage = get_book_storage()
        storage.save_book(Book(id=book_id, title=title, page_count=1))
        storage.save_page(Page(
            id="pg_1", book_id=book_id, title="Page", status=PageStatus.READY,
            blocks=[Block(id="blk_1", type=BlockType.TEXT, status=BlockStatus.READY,
                          payload={"body": title})],
        ))
        record = registry.register_book(
            base_dir=storage.path_service.get_book_dir(),
            dir_name=f"book_{book_id}",
            title=title,
            created_by=get_current_user().id,
            visibility="public",
        )
        registry.set_book_kb_links(record.book_id, {})
        return record

    if workspace_name is None:
        return "", _seed()
    workspace_id = ContentWorkspaceService().create_workspace(workspace_name)["workspace_id"]
    with workspace_context(workspace_id):
        return workspace_id, _seed()


def _rows(client, actor, path):
    response = client.get(path, headers=headers(actor))
    assert response.status_code == 200, response.text
    return response.json()


def test_owner_library_lists_own_public_book_once(archive_env):
    client, _ = archive_env
    with actor_scope("teacher"):
        source_id, mine = _public_book("bk_p16_mine", "P16 Mine", "P16 Source")
    with actor_scope("editor"):
        _, other = _public_book("bk_p16_other", "P16 Other", None)

    items = _rows(client, "teacher", "/api/dashboard/learning-library/books")["items"]
    own_rows = [row for row in items if row.get("ref") == mine.ref]
    assert len(own_rows) == 1, [(r["source"], r["content_workspace_id"]) for r in own_rows]
    kept = own_rows[0]
    assert kept["source"] == "own"
    assert kept["content_workspace_id"] == source_id
    assert kept["can_edit"] is True
    assert kept["content_workspace_name"] == "P16 Source"
    # Another user's public book (different ref) is still listed from General.
    assert [row["source"] for row in items if row.get("ref") == other.ref] == ["shared"]

    # The source picker reads the same library.
    picked = _rows(client, "teacher", "/api/dashboard/source-library/books")["items"]
    assert [row["source"] for row in picked if row.get("ref") == mine.ref] == ["own"]

    index = _rows(client, "teacher", "/api/dashboard/learning-index")["sources"]["books"]
    assert [row["source"] for row in index if row.get("ref") == mine.ref] == ["own"]


def test_general_scoped_listing_still_shows_the_public_record(archive_env):
    """The workspace-scoped /api/books in General is unchanged (picker relies on it)."""
    client, _ = archive_env
    with actor_scope("teacher"):
        _, mine = _public_book("bk_p16_mine", "P16 Mine", "P16 Source")

    books = _rows(client, "teacher", "/api/books")["books"]
    assert [row["source"] for row in books if row.get("ref") == mine.ref] == ["shared"]


def test_other_readers_still_see_the_public_book(archive_env):
    client, _ = archive_env
    with actor_scope("teacher"):
        _, mine = _public_book("bk_p16_mine", "P16 Mine", "P16 Source")

    items = _rows(client, "alice", "/api/dashboard/learning-library/books")["items"]
    assert [row["source"] for row in items if row.get("ref") == mine.ref] == ["shared"]
