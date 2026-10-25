"""Physical archive admission through the real app, auth and workspace middleware."""

from contextlib import contextmanager
from pathlib import Path
import os

from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from deeptutor.api.routers import auth
from deeptutor.book import registry
from deeptutor.book.models import Block, BlockStatus, BlockType, Book, Page, PageStatus
from deeptutor.book.storage import get_book_storage
from deeptutor.multi_user.context import get_current_user, reset_current_user
from deeptutor.services.workspace import ContentWorkspaceService
from deeptutor.services.workspace.context import current_workspace_id, workspace_context
from tests.api.test_book_shared_access import shared_env  # noqa: F401


@contextmanager
def actor_scope(actor, workspace=""):
    token = auth._install_current_user(auth.decode_token(actor))
    try:
        with workspace_context(workspace):
            yield
    finally:
        reset_current_user(token)


def headers(actor, workspace=""):
    return {"Authorization": f"Bearer {actor}", "X-DeepTutor-Workspace": workspace}


def seed_book():
    storage = get_book_storage()
    storage.save_book(Book(id="bk_archive", title="Archive", page_count=1))
    storage.save_page(Page(
        id="pg_archive", book_id="bk_archive", title="Original", status=PageStatus.READY,
        blocks=[Block(id="blk_archive", type=BlockType.TEXT, status=BlockStatus.READY,
                      payload={"body": "Original"})],
    ))
    return storage


def snapshot(storage):
    root = storage.book_root("bk_archive")
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def rest_edit(client, actor, ref="book:bk_archive", workspace="", revision=1):
    return client.post("/api/books/update-block", headers=headers(actor, workspace), json={
        "book_id": ref, "page_id": "pg_archive", "block_id": "blk_archive",
        "body": "Edited", "expected_revision": revision,
    })


def ws_edit(client, actor, ref="book:bk_archive", workspace="", revision=1):
    with client.websocket_connect(f"/ws/books?token={actor}&dt_workspace={workspace}") as ws:
        ws.send_json({"type": "subscribe", "book_id": ref})
        assert ws.receive_json()["type"] == "subscribed"
        ws.send_json({
            "type": "confirm_spine", "book_id": ref, "auto_compile": False,
            "expected_revision": revision,
            "spine": {"book_id": "bk_archive", "chapters": [], "exploration_summary": "Edited"},
        })
        for _ in range(30):
            result = ws.receive_json()
            if result["type"] in {"confirm_spine_result", "error"}:
                return result
    raise AssertionError("No action response")


@pytest.fixture
def archive_env(shared_env, monkeypatch, tmp_path):
    from deeptutor.api.main import app
    from deeptutor.services.workspace.activity import WorkspaceActivityMiddleware

    runtime = Path(os.environ["DEEPTUTOR_HOME"]).resolve()
    assert str(runtime).startswith("/private/tmp/deeptutor-verify.")
    assert tmp_path.resolve().is_relative_to(runtime)
    seen = []
    install = auth._install_request_workspace
    middleware_call = WorkspaceActivityMiddleware.__call__

    def observe_install(request):
        install(request)
        seen.append(("scope", request.scope["type"], get_current_user().username,
                     current_workspace_id()))

    async def observe_middleware(self, scope, receive, send):
        seen.append(("middleware", scope["type"]))
        return await middleware_call(self, scope, receive, send)

    monkeypatch.setattr(auth, "_install_request_workspace", observe_install)
    monkeypatch.setattr(WorkspaceActivityMiddleware, "__call__", observe_middleware)
    # No lifespan: no startup services, migrations or providers.
    return TestClient(app), seen


def archived_book(owner):
    with actor_scope(owner):
        service = ContentWorkspaceService()
        source_id = service.create_workspace("Synthetic source")["workspace_id"]
        with workspace_context(source_id):
            source = seed_book()
            record = registry.register_book(
                base_dir=source.path_service.get_book_dir(), dir_name="book_bk_archive",
                title="Archive", created_by=get_current_user().id, visibility="private",
            )
            registry.set_book_kb_links(record.book_id, {})
        service.update_workspace(source_id, archived=True)
    return source_id, source, record


def assert_read_only(client, actor, source, workspace=""):
    before = snapshot(source)
    response = client.get("/api/books/book:bk_archive", headers=headers(actor, workspace))
    assert response.status_code == 200, response.text
    book = response.json()["book"]
    assert book["can_edit"] is False
    assert book["can_delete"] is False
    assert book["can_set_visibility"] is False
    assert book["permission"] == "read"
    assert client.get("/api/books/book:bk_archive/pages/pg_archive",
                      headers=headers(actor, workspace)).status_code == 200
    assert client.get("/api/books/book:bk_archive/health",
                      headers=headers(actor, workspace)).status_code == 200
    assert rest_edit(client, actor, workspace=workspace).status_code == 404
    assert ws_edit(client, actor, workspace=workspace)["type"] == "error"
    assert client.delete("/api/books/book:bk_archive?force=true",
                         headers=headers(actor, workspace)).status_code == 404
    response = client.put("/api/books/book:bk_archive/visibility",
                          headers=headers(actor, workspace),
                          json={"visibility": "public", "force": actor == "root"})
    assert response.status_code in {403, 404}
    assert snapshot(source) == before
    assert source.load_book("bk_archive").revision == 1
    assert registry.get_book("bk_archive").visibility == "private"


@pytest.mark.parametrize("owner", ["teacher", "root"])
def test_owner_admin_archive_denial_and_restore(archive_env, owner):
    client, seen = archive_env
    source_id, source, record = archived_book(owner)
    assert client.get(f"/api/books/{record.ref}").status_code == 401
    with pytest.raises(WebSocketDisconnect) as denied:
        with client.websocket_connect("/ws/books"):
            pass
    assert denied.value.code == 4001
    assert client.get(f"/api/books/{record.ref}?dt_workspace=conflict",
                      headers=headers(owner, source_id)).status_code == 400
    for workspace in (source_id, ""):
        assert_read_only(client, owner, source, workspace)
    # Private source-workspace books are listed in their workspace, not General.
    listing = client.get("/api/books", headers=headers(owner, source_id)).json()["books"]
    listed = next(book for book in listing if book["id"] == record.book_id)
    assert not listed["can_edit"] and not listed["can_delete"]
    if owner != "root":
        assert_read_only(client, "root", source)
    before = snapshot(source)
    assert client.post("/api/books/progress/visit", headers=headers(owner), json={
        "book_id": record.ref, "page_id": "pg_archive",
    }).status_code == 200
    assert client.post("/api/books/page-chat-session", headers=headers(owner), json={
        "book_id": record.ref, "page_id": "pg_archive", "session_id": "reader-session",
    }).status_code == 200
    assert snapshot(source) == before
    for transport in ("http", "websocket"):
        assert ("middleware", transport) in seen
        assert ("scope", transport, owner, source_id) in seen
        assert ("scope", transport, owner, "") in seen

    with actor_scope(owner):
        ContentWorkspaceService().update_workspace(source_id, archived=False)
    restored = client.get(f"/api/books/{record.ref}", headers=headers(owner)).json()["book"]
    assert restored["can_edit"] and restored["can_delete"]
    assert rest_edit(client, owner).status_code == 200
    assert source.load_book(record.book_id).revision == 2
    assert source.load_page(record.book_id, "pg_archive").blocks[0].payload["body"] == "Edited"
    assert rest_edit(client, owner, revision=1).status_code == 409
    assert ws_edit(client, owner, revision=2)["type"] == "confirm_spine_result"
    assert source.load_book(record.book_id).revision == 3
    assert source.load_spine(record.book_id).exploration_summary == "Edited"
    assert registry.get_book(record.book_id).base_dir == record.base_dir


@pytest.mark.parametrize("local_owner", ["teacher", "root"])
def test_local_collision_does_not_authorize_archived_canonical_book(archive_env, local_owner):
    client, _ = archive_env
    _, source, record = archived_book("teacher")
    with actor_scope(local_owner):
        local = seed_book()
    before = snapshot(source)
    assert rest_edit(client, local_owner, ref="bk_archive").status_code == 200
    assert ws_edit(client, local_owner, ref="bk_archive", revision=2)["type"] == "confirm_spine_result"
    assert local.load_book(record.book_id).revision == 3
    assert_read_only(client, local_owner, source)
    assert snapshot(source) == before
    assert client.delete("/api/books/bk_archive", headers=headers(local_owner)).status_code == 200
    assert not local.book_exists(record.book_id)
    assert registry.get_book(record.book_id).base_dir == record.base_dir
    assert snapshot(source) == before


@pytest.mark.parametrize("delete_owner", [False, True])
def test_transferred_book_uses_retained_former_owner_catalog(archive_env, delete_owner):
    from deeptutor.multi_user.paths import user_context

    client, _ = archive_env
    source_id, source, record = archived_book("teacher")
    with actor_scope("teacher"):
        former_user = get_current_user()
    successor = auth.decode_token("root")
    if delete_owner:
        assert auth._delete_admin_user("teacher", successor) == (200, "")
    else:
        registry.transfer_book_ownership(record.created_by, successor.user_id)
    transferred = registry.get_book(record.book_id)
    assert transferred.created_by == successor.user_id
    assert transferred.base_dir == record.base_dir
    if not delete_owner:
        assert client.get("/api/books/book:bk_archive",
                          headers=headers("teacher")).status_code == 404
    assert_read_only(client, "root", source)
    # The retained physical catalog remains authoritative even without an account.
    with user_context(former_user), workspace_context(""):
        ContentWorkspaceService().update_workspace(source_id, archived=False)
    assert rest_edit(client, "root").status_code == 200
    assert ws_edit(client, "root", revision=2)["type"] == "confirm_spine_result"
    assert source.load_book(record.book_id).revision == 3


@pytest.mark.parametrize("damage", ["corrupt", "missing"])
def test_unavailable_source_catalog_denies_writes_but_preserves_reads(archive_env, damage):
    from deeptutor.multi_user.paths import get_account_path_service

    client, _ = archive_env
    _, source, _ = archived_book("teacher")
    with actor_scope("teacher"):
        catalog = get_account_path_service().get_runtime_state_dir() / "workspaces.sqlite3"
    original = catalog.read_bytes()
    # Use a healthy caller catalog; corrupting the caller's own catalog fails
    # workspace middleware before Book resolution and is a separate contract.
    registry.transfer_book_ownership(
        auth.decode_token("teacher").user_id, auth.decode_token("alice").user_id,
    )
    if damage == "corrupt":
        catalog.write_bytes(b"corrupt synthetic catalog")
    else:
        catalog.unlink()
    for actor in ("alice", "root"):
        assert_read_only(client, actor, source)
    catalog.write_bytes(original)
    assert_read_only(client, "alice", source)


@pytest.mark.asyncio
@pytest.mark.usefixtures("archive_env")
async def test_direct_engine_and_stale_revision_admission():
    from fastapi import HTTPException

    from deeptutor.api.routers.book import _claim_content_mutation, _locked_writable
    from deeptutor.book import sharing
    from deeptutor.book.models import Spine
    from deeptutor.multi_user.book_access import resolve_book
    from deeptutor.services.workspace.models import WorkspaceError

    source_id, source, _ = archived_book("teacher")
    with actor_scope("teacher"):
        service = ContentWorkspaceService()
        service.update_workspace(source_id, archived=False)
        resolved = resolve_book("book:bk_archive")
        assert resolved.can_edit
        service.update_workspace(source_id, archived=True)
        before = snapshot(source)
        with pytest.raises(HTTPException) as denied:
            _claim_content_mutation(resolved, "bk_archive", 1, "update_block")
        assert denied.value.status_code == 404
        with sharing.shared_write() as conn:
            with pytest.raises(HTTPException) as sharing_denied:
                _locked_writable(conn, resolved, resolved.record)
        assert sharing_denied.value.status_code == 403
        engine = resolved.engine
        with pytest.raises(WorkspaceError):
            await engine.update_block(book_id="bk_archive", page_id="pg_archive",
                                      block_id="blk_archive", body="Denied")
        with pytest.raises(WorkspaceError):
            await engine.confirm_spine(
                book_id="bk_archive", edited_spine=Spine(book_id="bk_archive", chapters=[]),
                auto_compile=False,
            )
        with pytest.raises(WorkspaceError):
            engine.delete_book("bk_archive")
        with pytest.raises(WorkspaceError):
            with engine.recoverable_deletion("bk_archive"):
                pytest.fail("Deletion must not begin")
        with pytest.raises(WorkspaceError):
            engine.refresh_kb_fingerprints("bk_archive", force=True)
        with pytest.raises(WorkspaceError):
            engine.set_page_chat_session(book_id="bk_archive", page_id="pg_archive",
                                         session_id="denied")
        assert not await engine.maybe_resume_on_open("bk_archive")
        assert engine.load_progress("bk_archive").book_id == "bk_archive"
        assert snapshot(source) == before
        service.update_workspace(source_id, archived=False)
        await engine.update_block(book_id="bk_archive", page_id="pg_archive",
                                  block_id="blk_archive", body="Restored direct edit")
        assert source.load_page("bk_archive", "pg_archive").blocks[0].payload["body"] == (
            "Restored direct edit"
        )
