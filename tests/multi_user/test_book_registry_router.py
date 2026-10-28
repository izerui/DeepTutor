"""Sharing routes and recoverable, canonical-location book deletion."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
import sqlite3

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from deeptutor.api.routers import book as router
from deeptutor.book import registry, sharing
from deeptutor.book.engine import _BookRuntime
from deeptutor.book.event_hub import get_book_bus, get_book_stream
from deeptutor.book.models import Book, Page
from deeptutor.book.storage import get_book_storage
from deeptutor.knowledge import registry as kbs
from deeptutor.multi_user import book_access


@pytest.fixture
def owned(as_user, tmp_path):
    with as_user("owner", role="teacher"):
        kb = kbs.register_kb(
            base_dir=tmp_path / "kbs", name="answers", kind="uploaded", created_by="owner",
        )
        store = get_book_storage()
        store.save_book(Book(id="bk_one", title="Waves", knowledge_bases=[kb.ref]))
        store.save_page(Page(id="pg_one", book_id="bk_one", title="Original page"))
        store.append_log("bk_one", "Original log")
        resolved = book_access.resolve_book("bk_one")
    return resolved, kb


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.asyncio
async def test_sharing_visibility_and_reference_contract(owned, as_user):
    resolved, kb = owned
    with as_user("owner", role="teacher"):
        body = await router.get_book_sharing("bk_one")
        assert body["ref"] == "book:bk_one" and body["visibility"] == "private"
        assert body["can_set_visibility"] is True
        assert body["references"] == []
        assert body["kbs"]["private_kbs"] == [
            {"kb_id": kb.kb_id, "name": "answers", "default": "make_public"},
        ]
        body = await router.set_book_visibility("book:bk_one", router.BookVisibilityRequest(
            visibility="public", kb_choices={kb.kb_id: "reference_only"},
        ))
        assert body["visibility"] == "public"
        assert kbs.get_kb(kb.kb_id).visibility == "private"
        registry.replace_book_references("course", "c1", ["bk_one"], created_by="other")
        refs = await router.get_book_references("book:bk_one")
        # Other accounts' referrers only as counts (rule R1).
        assert refs["references"] == []
        assert refs["other_references"] == {"total": 1, "by_kind": {"course": 1}}
        result = await router.set_book_visibility(
            "bk_one", router.BookVisibilityRequest(visibility="private"),
        )
        assert result["visibility"] == "private"
    assert registry.list_unavailable_book_references("course", "c1")[0].reason == "made_private"
    assert resolved.engine.load_book("bk_one").title == "Waves"


@pytest.mark.asyncio
async def test_sharing_denials_and_roles(owned, as_user):
    with as_user("stranger"):
        with pytest.raises(HTTPException) as hidden:
            await router.get_book_sharing("bk_one")
        assert hidden.value.status_code == 404
    with as_user("owner"):
        with pytest.raises(HTTPException) as denied:
            await router.set_book_visibility("bk_one", router.BookVisibilityRequest(visibility="public"))
        assert denied.value.status_code == 403
    with as_user("owner", role="teacher"):
        with pytest.raises(HTTPException) as denied:
            await router.set_book_visibility(
                "bk_one", router.BookVisibilityRequest(visibility="private", force=True),
            )
        assert denied.value.status_code == 403
        await router.set_book_visibility("bk_one", router.BookVisibilityRequest(visibility="public"))
    with as_user("reader"):
        for call in (router.get_book_sharing, router.get_book_references):
            with pytest.raises(HTTPException) as denied:
                await call("book:bk_one")
            assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_references_never_block_the_owners_delete_and_are_revoked(owned, as_user):
    """Rule R1: non-teacher-path references are revoked; force stays admin-only."""
    resolved, kb = owned
    with as_user("owner", role="teacher"):
        registry.replace_book_references("course", "c1", ["bk_one"], created_by="owner")
        with pytest.raises(HTTPException) as denied:
            await router.delete_book("bk_one", force=True)
        assert denied.value.status_code == 403
        assert kbs.list_referenced("book", "bk_one") == [kb]
        assert await router.delete_book("book:bk_one") == {
            "deleted": True, "book_id": "bk_one",
        }
    assert registry.get_book("bk_one") is None
    assert not kbs.list_referenced("book", "bk_one")
    assert not resolved.engine.storage.book_root("bk_one").exists()
    [revoked] = registry.list_unavailable_book_references("course", "c1")
    assert (revoked.reason, revoked.book_title) == ("book_deleted", "Waves")


@pytest.mark.asyncio
async def test_local_collision_management_denied_but_delete_is_local(owned, as_user):
    canonical, _kb = owned
    with as_user("owner", role="teacher"):
        registry.set_book_visibility("bk_one", "public")
        registry.replace_book_references("course", "c1", ["bk_one"], created_by="owner")
    with as_user("reader", role="teacher"):
        store = get_book_storage()
        store.save_book(Book(id="bk_one", title="Local collision"))
        local = book_access.resolve_book("bk_one")
        assert local.record is None
        for call in (router.get_book_sharing, router.get_book_references):
            with pytest.raises(HTTPException) as conflict:
                await call("bk_one")
            assert conflict.value.status_code == 409
        with pytest.raises(HTTPException) as conflict:
            await router.set_book_visibility("bk_one", router.BookVisibilityRequest(visibility="public"))
        assert conflict.value.status_code == 409
        with pytest.raises(HTTPException) as forbidden:
            await router.get_book_sharing("book:bk_one")
        assert forbidden.value.status_code == 403
        await router.mark_visited(router.ProgressRequest(book_id="book:bk_one", page_id="pg_one"))
        before = snapshot(canonical.engine.storage.book_root("bk_one"))
        record, refs = registry.get_book("bk_one"), registry.list_book_references("bk_one")
        kb_refs = kbs.list_referenced("book", "bk_one")
        bus = get_book_bus("bk_one", storage=canonical.engine.storage)
        local_bus = get_book_bus("bk_one", storage=local.engine.storage)
        frames = []

        async def send(frame):
            frames.append(frame)

        fanout = router._SocketFanout(send)
        fanout.attach_book("book:bk_one", book_access.resolve_book("book:bk_one"))
        try:
            assert (await router.delete_book("bk_one"))["deleted"]
            assert local_bus._closed and not bus._closed
            await get_book_stream("bk_one", storage=canonical.engine.storage).book_event("live", {})
            # Socket authorization runs in a worker thread (B3-1).
            for _ in range(20):
                await asyncio.sleep(0.005)
            assert frames[-1]["metadata"]["kind"] == "live"
            assert (await router.get_book("book:bk_one"))["progress"]["current_page_id"] == "pg_one"
        finally:
            await fanout.close()
        assert registry.get_book("bk_one") == record
        assert registry.list_book_references("bk_one") == refs
        assert kbs.list_referenced("book", "bk_one") == kb_refs
        assert snapshot(canonical.engine.storage.book_root("bk_one")) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["commit", "kb_cleanup"])
async def test_delete_failure_restores_contents_refs_and_live_runtime(owned, as_user, monkeypatch, failure):
    resolved, _kb = owned
    store = resolved.engine.storage
    root = store.book_root("bk_one")
    before = snapshot(root)
    with as_user("owner", role="teacher"):
        registry.replace_book_references("course", "c1", ["bk_one"], created_by="owner")
    record, refs = registry.get_book("bk_one"), registry.list_book_references("bk_one")
    kb_refs = kbs.list_referenced("book", "bk_one")
    bus = get_book_bus("bk_one", storage=store)
    original_sync = sharing.sync_protection
    if failure == "commit":
        # A deferred FK violation fails real COMMIT after physical deletion.
        with registry._write() as conn:
            conn.execute("CREATE TABLE deletion_parent(id INTEGER PRIMARY KEY)")
            conn.execute("""CREATE TABLE deletion_failure(
                    id INTEGER REFERENCES deletion_parent(id) DEFERRABLE INITIALLY DEFERRED
                )""")
            conn.execute("""
                CREATE TRIGGER reject_deletion AFTER DELETE ON book_registry
                BEGIN INSERT INTO deletion_failure VALUES (1); END
            """)
    else:
        def failed_sync(conn, book_id, **kwargs):
            original_sync(conn, book_id, **kwargs)
            raise RuntimeError("synthetic KB cleanup failure")

        monkeypatch.setattr(sharing, "sync_protection", failed_sync)
    worker = asyncio.create_task(asyncio.Event().wait())
    runtime = _BookRuntime(worker=worker)
    resolved.engine._runtimes["bk_one"] = runtime
    original_rename = Path.rename
    restored = []

    def guarded_rename(path, target):
        if path.name == "original":
            assert_writers_locked()
            restored.append(True)
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", guarded_rename)
    try:
        with as_user("root", role="admin"):
            with pytest.raises((HTTPException, OSError, sqlite3.IntegrityError, RuntimeError)):
                await router.delete_book("book:bk_one", force=True)
        assert restored == [True]
        assert snapshot(root) == before
        assert registry.get_book("bk_one") == record
        assert registry.list_book_references("bk_one") == refs
        assert kbs.list_referenced("book", "bk_one") == kb_refs
        assert registry.list_unavailable_book_references("course", "c1") == []
        assert resolved.engine._runtimes["bk_one"] is runtime
        assert not worker.cancelling()
        assert get_book_bus("bk_one", storage=store) is bus and not bus._closed
        await get_book_stream("bk_one", storage=store).book_event("still-live", {})
        assert not list(root.parent.glob(".book-delete-*"))
        if failure == "commit":
            with registry._write() as conn:
                conn.execute("DROP TRIGGER reject_deletion")
        monkeypatch.setattr(sharing, "sync_protection", original_sync)
        with as_user("root", role="admin"):
            assert (await router.delete_book("book:bk_one", force=True))["deleted"]
        assert "bk_one" not in resolved.engine._runtimes
        assert worker.cancelling()
        assert bus._closed
        assert not root.exists() and registry.get_book("bk_one") is None
    finally:
        resolved.engine._runtimes.pop("bk_one", None)
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_unreferenced_delete_unregisters(owned, as_user):
    with as_user("owner", role="teacher"):
        assert (await router.delete_book("bk_one"))["deleted"]
    assert registry.get_book("bk_one") is None


def assert_writers_locked():
    for path in (registry.DB_PATH_OVERRIDE, kbs.DB_PATH_OVERRIDE):
        conn = sqlite3.connect(path, timeout=0, isolation_level=None)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                conn.execute("BEGIN IMMEDIATE")
        finally:
            conn.close()


@pytest.mark.asyncio
async def test_delete_stages_the_book_while_holding_both_writers(owned, as_user, monkeypatch):
    resolved, _kb = owned
    root = resolved.engine.storage.book_root("bk_one")
    original = Path.rename
    checked = []

    def guarded_rename(path, target):
        if path == root:
            assert_writers_locked()
            checked.append(True)
        return original(path, target)

    monkeypatch.setattr(Path, "rename", guarded_rename)
    with as_user("owner", role="teacher"):
        await router.delete_book("bk_one")
    assert checked == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["sharing", "references", "visibility", "delete"])
@pytest.mark.parametrize("change", ["owner", "location", "identity"])
async def test_final_locked_target_rejects_changes(owned, as_user, monkeypatch, operation, change):
    resolved, _kb = owned
    before = snapshot(resolved.engine.storage.book_root("bk_one"))
    original = sharing.shared_write

    def mutate():
        with original() as conn:
            if change == "owner":
                conn.execute("UPDATE book_registry SET created_by='other' WHERE book_id='bk_one'")
            elif change == "location":
                conn.execute("UPDATE book_registry SET base_dir=base_dir || '/moved' WHERE book_id='bk_one'")
            else:
                conn.execute("UPDATE book_registry SET created_at='replacement' WHERE book_id='bk_one'")

    @contextmanager
    def changed_before_lock(**kwargs):
        mutate()
        with original(**kwargs) as conn:
            yield conn

    original_read = sharing.shared_read

    @contextmanager
    def changed_before_read():
        # The GETs read a snapshot (B3-3); the change commits just before it.
        mutate()
        with original_read() as conn:
            yield conn

    monkeypatch.setattr(sharing, "shared_write", changed_before_lock)
    monkeypatch.setattr(sharing, "shared_read", changed_before_read)
    with as_user("owner", role="teacher"):
        with pytest.raises(HTTPException) as denied:
            if operation == "visibility":
                await router.set_book_visibility("bk_one", router.BookVisibilityRequest(visibility="public"))
            else:
                await {
                    "sharing": router.get_book_sharing,
                    "references": router.get_book_references,
                    "delete": router.delete_book,
                }[operation]("bk_one")
    assert denied.value.status_code == (403 if change == "owner" else 409)
    assert registry.get_book("bk_one").visibility == "private"
    assert snapshot(resolved.engine.storage.book_root("bk_one")) == before


@pytest.mark.asyncio
async def test_local_edit_flag_does_not_authorize_registered_foreign_owner(owned, as_user):
    resolved, _kb = owned
    registry.transfer_book_ownership("owner", "other")
    with as_user("owner", role="teacher"):
        assert book_access.resolve_book("bk_one").can_edit
        for call in (router.get_book_sharing, router.get_book_references, router.delete_book):
            with pytest.raises(HTTPException) as denied:
                await call("bk_one")
            assert denied.value.status_code == 403
    assert resolved.engine.load_book("bk_one") is not None


@pytest.mark.asyncio
async def test_new_reference_before_delete_lock_blocks_deletion(owned, as_user, monkeypatch):
    resolved, _kb = owned
    original = sharing.shared_write

    @contextmanager
    def referenced_before_lock(**kwargs):
        # This independent transition commits between initial resolution and
        # the deletion guard. Its reference must be checked by the guard.
        with monkeypatch.context() as scoped:
            scoped.setattr(sharing, "shared_write", original)
            registry.replace_book_references(
                "mastery_template", "late", ["bk_one"], created_by="owner",
            )
        with original(**kwargs) as conn:
            yield conn

    monkeypatch.setattr(sharing, "shared_write", referenced_before_lock)
    monkeypatch.setattr(
        "deeptutor.teaching.access.withdrawal_impact", lambda ids: (len(tuple(ids)), 3),
    )
    with as_user("owner", role="teacher"):
        with pytest.raises(HTTPException) as blocked:
            await router.delete_book("bk_one")
    assert blocked.value.status_code == 409
    detail = blocked.value.detail
    assert (detail["code"], detail["affected_paths"], detail["affected_students"]) == (
        "referenced_by_teacher_paths", 1, 3,
    )
    assert "late" not in str(detail)
    assert resolved.engine.load_book("bk_one") is not None


@pytest.mark.asyncio
async def test_staging_failure_keeps_original(owned, as_user, monkeypatch):
    resolved, _kb = owned
    root = resolved.engine.storage.book_root("bk_one")
    before = snapshot(root)
    original = Path.rename

    def fail_rename(path, target):
        if path == root:
            raise OSError("synthetic rename failure")
        return original(path, target)

    monkeypatch.setattr(Path, "rename", fail_rename)
    with as_user("owner", role="teacher"):
        with pytest.raises(OSError, match="synthetic"):
            await router.delete_book("bk_one")
    assert snapshot(root) == before
    assert registry.get_book("bk_one") is not None
    assert not list(root.parent.glob(".book-delete-*"))


@pytest.mark.asyncio
async def test_private_kb_publication_and_connector_block(owned, as_user, tmp_path):
    resolved, kb = owned
    with as_user("owner", role="teacher"):
        result = await router.set_book_visibility("bk_one", router.BookVisibilityRequest(
            visibility="public", kb_choices={kb.kb_id: "make_public"},
        ))
        assert result["visibility"] == "public" and kbs.get_kb(kb.kb_id).visibility == "public"
        await router.set_book_visibility("bk_one", router.BookVisibilityRequest(visibility="private"))
        connector = kbs.register_kb(
            base_dir=tmp_path / "connector", name="personal",
            kind=sorted(kbs.CONNECTOR_KINDS)[0], created_by="owner",
        )
        registry.set_book_kb_links("bk_one", {connector.ref: connector.kb_id})
        with pytest.raises(HTTPException) as blocked:
            await router.set_book_visibility("bk_one", router.BookVisibilityRequest(visibility="public"))
        assert blocked.value.status_code == 409
    assert resolved.engine.load_book("bk_one") is not None


def test_http_models_and_routes(owned, as_user):
    app = FastAPI()
    app.include_router(router.router, prefix="/api")
    with TestClient(app) as client, as_user("owner", role="teacher"):
        assert client.get("/api/books/book:bk_one/sharing").status_code == 200
        for invalid in (
            {"visibility": "unknown"},
            {"visibility": "public", "kb_choices": {"kb_1": "invalid"}},
        ):
            assert client.put("/api/books/book:bk_one/visibility", json=invalid).status_code == 422
        result = client.put("/api/books/book:bk_one/visibility", json={"visibility": "public"})
        assert result.status_code == 200 and result.json()["ref"] == "book:bk_one"
        assert client.get("/api/books/book:bk_one/references").json()["references"] == []
        assert client.delete("/api/books/book:bk_one?force=true").status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", [False, True])
async def test_admin_collision_deletes_only_selected_location(owned, as_user, explicit):
    canonical, _kb = owned
    with as_user("owner", role="teacher"):
        registry.replace_book_references("course", "c1", ["bk_one"], created_by="owner")
    canonical_before = snapshot(canonical.engine.storage.book_root("bk_one"))
    record = registry.get_book("bk_one")
    with as_user("root", role="admin"):
        store = get_book_storage()
        store.save_book(Book(id="bk_one", title="Admin collision"))
        local_before = snapshot(store.book_root("bk_one"))
        local_bus = get_book_bus("bk_one", storage=store)
        canonical_bus = get_book_bus("bk_one", storage=canonical.engine.storage)
        await router.delete_book("book:bk_one" if explicit else "bk_one", force=True)
        if explicit:
            assert registry.get_book("bk_one") is None
            assert snapshot(store.book_root("bk_one")) == local_before
            assert canonical_bus._closed and not local_bus._closed
            assert registry.list_unavailable_book_references("course", "c1")[0].reason == "book_deleted"
        else:
            assert registry.get_book("bk_one") == record
            assert snapshot(canonical.engine.storage.book_root("bk_one")) == canonical_before
            assert local_bus._closed and not canonical_bus._closed
            assert registry.list_book_references("bk_one")[0].referrer_id == "c1"
            assert registry.list_unavailable_book_references("course", "c1") == []


@pytest.mark.asyncio
async def test_committed_delete_retains_staging_if_garbage_cleanup_fails(owned, as_user, monkeypatch):
    from deeptutor.book import storage

    resolved, _kb = owned
    root = resolved.engine.storage.book_root("bk_one")
    original = storage.shutil.rmtree

    def fail_garbage_cleanup(path, **kwargs):
        if Path(path).name.startswith(".book-delete-"):
            raise OSError("synthetic garbage cleanup failure")
        return original(path, **kwargs)

    monkeypatch.setattr(storage.shutil, "rmtree", fail_garbage_cleanup)
    with as_user("owner", role="teacher"):
        assert (await router.delete_book("bk_one"))["deleted"]
    assert registry.get_book("bk_one") is None and not root.exists()
    [staging] = list(root.parent.glob(".book-delete-*"))
    assert (staging / "original" / "manifest.json").is_file()
    assert resolved.engine.storage.list_book_ids() == []
