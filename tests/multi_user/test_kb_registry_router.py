"""KB registration, guarded deletion, visibility and scoped configuration routes."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace

from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient
import pytest

from deeptutor.knowledge import registry
from deeptutor.multi_user.knowledge_access import current_kb_manager


@pytest.fixture
def knowledge():
    from deeptutor.api.routers import knowledge as module

    return module


@pytest.mark.asyncio
async def test_document_delete_follows_d18(knowledge, as_user, make_kb):
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        folder = make_kb(manager, "physics")
        (folder / "raw/other.md").write_text("more")
        record = registry.ensure_registered(manager.base_dir, "physics", created_by="t1")
        docs = {d.rel_path: d for d in registry.refresh_documents(manager.base_dir, "physics")}
        registry.add_reference(
            record.ref, referrer_kind="mastery_template", referrer_id="whole", created_by="t1"
        )
        registry.add_reference(
            record.ref, referrer_kind="mastery_template", referrer_id="chapter",
            doc_ids=[docs["doc.md"].doc_id], created_by="t1",
        )
        with pytest.raises(HTTPException) as cited:
            await knowledge.delete_kb_file("physics", "doc.md", confirm=True)
        assert cited.value.status_code == 409
        assert cited.value.detail["code"] == "document_referenced"
        assert [r["referrer_id"] for r in cited.value.detail["references"]] == ["chapter"]
        with pytest.raises(HTTPException) as forced:
            await knowledge.delete_kb_file("physics", "doc.md", force=True)
        assert forced.value.status_code == 403
        with pytest.raises(HTTPException) as ask:
            await knowledge.delete_kb_file("physics", "other.md")
        assert ask.value.status_code == 409
        assert ask.value.detail["requires_confirmation"]
        assert [r["referrer_id"] for r in ask.value.detail["references"]] == ["whole"]
        assert (await knowledge.delete_kb_file("physics", "other.md", confirm=True))["status"] == "ok"
        assert registry.find_document(record.ref, "other.md").deleted
    with as_user("root", role="admin"):
        assert (await knowledge.delete_kb_file(record.ref, "doc.md", force=True))["status"] == "ok"
    assert registry.find_document(record.ref, "doc.md").deleted


@pytest.mark.asyncio
@pytest.mark.parametrize("registered", [False, True])
async def test_document_force_requires_admin_without_references(
    knowledge, as_user, make_kb, registered
):
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        folder = make_kb(manager, "physics")
        if registered:
            registry.ensure_registered(manager.base_dir, "physics", created_by="t1")
        with pytest.raises(HTTPException) as error:
            await knowledge.delete_kb_file("physics", "doc.md", force=True)
        assert error.value.status_code == 403
        assert (folder / "raw/doc.md").exists()


def test_document_force_query_is_admin_only(knowledge, as_user, make_kb):
    with as_user("t1", role="teacher"):
        folder = make_kb(current_kb_manager(), "physics")
        app = FastAPI()
        app.include_router(knowledge.router, prefix="/api")
        with TestClient(app) as client:
            response = client.delete("/api/knowledge-bases/physics/files/doc.md?force=true")
        assert response.status_code == 403
        assert (folder / "raw/doc.md").exists()


def test_documents_are_versioned_whenever_indexing_finishes(as_user, make_kb):
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        folder = make_kb(manager, "physics")
        record = registry.ensure_registered(manager.base_dir, "physics", created_by="t1")
        manager.update_kb_status("physics", "ready")
        [first] = registry.list_documents(record.ref)
        (folder / "raw/doc.md").write_text("changed")
        (folder / "raw/web").mkdir()
        (folder / "raw/web/page.md").write_text("crawled")
        manager.update_kb_status("physics", "ready")
        docs = {d.rel_path: d for d in registry.list_documents(record.ref)}
        assert docs["doc.md"].doc_id == first.doc_id and docs["doc.md"].version == 2
        assert docs["web/page.md"].version == 1
        assert docs["web/page.md"].content_hash.startswith("sha256:")


def test_uploaded_files_are_recorded_as_documents(knowledge, as_user, make_kb):
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        folder = make_kb(manager, "physics")
        raw = folder / "raw"
        (raw / "new.md").write_text("fresh")
        knowledge._record_documents(
            manager.base_dir, "physics", raw, [str(raw / "new.md"), "/elsewhere/x.md"]
        )
        record = registry.find_by_location(manager.base_dir, "physics")
        assert [d.rel_path for d in registry.list_documents(record.ref)] == ["new.md"]
        assert record.created_by == "t1"


def test_upload_registration_preserves_admin_library_owner(knowledge, as_user, make_kb):
    from deeptutor.multi_user.models import LOCAL_ADMIN_ID

    with as_user("incidental-admin", role="admin"):
        manager = current_kb_manager()
        folder = make_kb(manager, "physics")
        knowledge._record_documents(
            manager.base_dir, "physics", folder / "raw", [str(folder / "raw/doc.md")]
        )
        assert registry.find_by_location(manager.base_dir, "physics").created_by == LOCAL_ADMIN_ID


@pytest.mark.asyncio
@pytest.mark.parametrize("folder_move", [False, True])
async def test_document_ids_follow_route_moves(knowledge, as_user, make_kb, folder_move):
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        folder = make_kb(manager, "physics")
        if folder_move:
            (folder / "raw/part").mkdir()
            (folder / "raw/doc.md").rename(folder / "raw/part/doc.md")
        record = registry.ensure_registered(manager.base_dir, "physics", created_by="t1")
        [first] = registry.refresh_documents(manager.base_dir, "physics")
        source = "part" if folder_move else "doc.md"
        result = await knowledge.move_kb_file(
            record.ref, knowledge.MoveFilePayload(source=source, dest_folder="archive")
        )
        assert result["path"] == f"archive/{source}"
        [moved] = registry.list_documents(record.ref)
        assert moved.rel_path == f"archive/{first.rel_path}"
        assert moved.doc_id == first.doc_id and moved.version == first.version
        manager.update_kb_status("physics", "ready")
        assert registry.list_documents(record.ref) == [moved]


def _fail_registry_commit(monkeypatch):
    original = registry._open

    class FailingCommit:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def execute(self, sql, *args):
            if sql == "COMMIT":
                raise sqlite3.OperationalError("injected commit failure")
            return self.connection.execute(sql, *args)

    monkeypatch.setattr(registry, "_open", lambda path: FailingCommit(original(path)))


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["move", "delete"])
@pytest.mark.parametrize("failure", ["commit", "filesystem"])
async def test_document_mutation_failure_restores_files_and_ids(
    knowledge, as_user, make_kb, monkeypatch, operation, failure
):
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        folder = make_kb(manager, "physics")
        record = registry.ensure_registered(manager.base_dir, "physics", created_by="t1")
        before = registry.refresh_documents(manager.base_dir, "physics")
        metadata = folder / "metadata.json"
        metadata.write_text(json.dumps({"file_hashes": {"doc.md": "old"}}))
        original_metadata = metadata.read_bytes()
        assets = knowledge.VisualAssetStore(folder)
        assets.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        assets.manifest_path.write_text(json.dumps({
            "version": 1, "assets": {"a": {"source_path": "doc.md"}}
        }))
        original_manifest = assets.manifest_path.read_bytes()
        if failure == "commit":
            _fail_registry_commit(monkeypatch)
        else:
            def fail(*args, **kwargs):
                raise OSError("injected filesystem failure")

            if operation == "move":
                monkeypatch.setattr(knowledge.VisualAssetStore, "move_source", fail)
            else:
                monkeypatch.setattr(knowledge, "remove_raw_document", fail)
        with pytest.raises((OSError, sqlite3.OperationalError), match="injected"):
            if operation == "move":
                await knowledge.move_kb_file(
                    record.ref, knowledge.MoveFilePayload(source="doc.md", dest_folder="archive")
                )
            else:
                await knowledge.delete_kb_file(record.ref, "doc.md")
        assert (folder / "raw/doc.md").read_text() == "hello"
        assert not (folder / "raw/archive/doc.md").exists()
        assert registry.list_documents(record.ref) == before
        assert metadata.read_bytes() == original_metadata
        assert assets.manifest_path.read_bytes() == original_manifest


@pytest.mark.asyncio
async def test_document_delete_holds_reference_writer_lock(
    knowledge, as_user, make_kb, monkeypatch
):
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        make_kb(manager, "physics")
        record = registry.ensure_registered(manager.base_dir, "physics", created_by="t1")
        registry.refresh_documents(manager.base_dir, "physics")
        original = knowledge.remove_raw_document
        checked = []

        def remove_while_locked(*args):
            other = sqlite3.connect(registry._db_path(), timeout=0)
            try:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    other.execute("BEGIN IMMEDIATE")
                checked.append(True)
            finally:
                other.close()
            return original(*args)

        monkeypatch.setattr(knowledge, "remove_raw_document", remove_while_locked)
        await knowledge.delete_kb_file(record.ref, "doc.md")
        assert checked == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("folder_move", [False, True])
@pytest.mark.parametrize("failure", [None, "commit", "filesystem", "rename", "unique"])
async def test_document_move_preserves_deleted_destination_identity(
    knowledge, as_user, make_kb, monkeypatch, folder_move, failure
):
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        folder = make_kb(manager, "physics")
        record = registry.ensure_registered(manager.base_dir, "physics", created_by="t1")
        raw = folder / "raw"
        source = "part" if folder_move else "doc.md"
        if folder_move:
            (raw / "part/nested").mkdir(parents=True)
            (raw / "doc.md").rename(raw / "part/doc.md")
            (raw / "part/nested/second.md").write_text("second")
        moving = registry.refresh_documents(manager.base_dir, "physics")
        old_docs = registry.upsert_documents(record.ref, {
            f"archive/{doc.rel_path}": "sha256:old" for doc in moving
        })
        old_docs = [
            registry.mark_document_deleted(record.ref, doc.rel_path) for doc in old_docs
        ]
        old = old_docs[0]
        registry.add_reference(
            record.ref, referrer_kind="mastery_template", referrer_id="pinned",
            created_by="t1", doc_ids=[old.doc_id],
        )
        before = registry.list_documents(record.ref, include_deleted=True)
        if failure == "commit":
            _fail_registry_commit(monkeypatch)
        elif failure == "filesystem":
            def fail(*args):
                raise OSError("injected filesystem failure")

            monkeypatch.setattr(knowledge.VisualAssetStore, "move_source", fail)
        elif failure == "rename":
            original_replace = knowledge.os.replace

            def fail_source_rename(src, dest):
                if Path(src) == raw / source:
                    raise OSError("injected rename failure")
                return original_replace(src, dest)

            monkeypatch.setattr(knowledge.os, "replace", fail_source_rename)
        elif failure == "unique":
            with registry._write() as conn:
                conn.execute(
                    "CREATE TRIGGER fail_move BEFORE UPDATE OF rel_path ON kb_documents "
                    "BEGIN SELECT RAISE(ABORT, 'injected UNIQUE failure'); END"
                )
        call = knowledge.move_kb_file(
            record.ref, knowledge.MoveFilePayload(source=source, dest_folder="archive")
        )
        if failure:
            with pytest.raises((OSError, sqlite3.Error), match="injected"):
                await call
            assert (raw / source).exists()
            assert not (raw / "archive" / source).exists()
            for doc in moving:
                assert (raw / doc.rel_path).read_text() == (
                    "second" if doc.rel_path.endswith("second.md") else "hello"
                )
            assert registry.list_documents(record.ref, include_deleted=True) == before
        else:
            await call
            assert not (raw / source).exists()
            for doc in moving:
                target = f"archive/{doc.rel_path}"
                assert (raw / target).read_text() == (
                    "second" if target.endswith("second.md") else "hello"
                )
                current = registry.find_document(record.ref, target)
                assert (current.doc_id, current.version, current.content_hash) == (
                    doc.doc_id, doc.version, doc.content_hash
                )
            history = registry.list_documents(record.ref, include_deleted=True)
            assert all(doc in history for doc in old_docs)
            assert len(history) == 2 * len(moving)
            registry.refresh_documents(manager.base_dir, "physics")
            assert registry.list_documents(record.ref, include_deleted=True) == history
        assert [ref.referrer_id for ref in registry.document_referrers(record.ref, old.doc_id)] == [
            "pinned"
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize("folder_move", [False, True])
async def test_document_move_keeps_active_registry_destination_protected(
    knowledge, as_user, make_kb, folder_move
):
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        folder = make_kb(manager, "physics")
        raw = folder / "raw"
        if folder_move:
            (raw / "part").mkdir()
            (raw / "doc.md").rename(raw / "part/doc.md")
        source = "part" if folder_move else "doc.md"
        record = registry.ensure_registered(manager.base_dir, "physics", created_by="t1")
        [moving] = registry.refresh_documents(manager.base_dir, "physics")
        registry.upsert_documents(record.ref, {f"archive/{moving.rel_path}": "sha256:live"})
        before = registry.list_documents(record.ref, include_deleted=True)
        with pytest.raises(HTTPException) as error:
            await knowledge.move_kb_file(
                record.ref, knowledge.MoveFilePayload(source=source, dest_folder="archive")
            )
        assert error.value.status_code == 409
        assert (raw / moving.rel_path).read_text() == "hello"
        assert not (raw / "archive" / source).exists()
        assert registry.list_documents(record.ref, include_deleted=True) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_commit", [False, True])
async def test_upload_hashing_is_off_loop_and_failure_restores_overwrites(
    knowledge, as_user, make_kb, monkeypatch, fail_commit
):
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        folder = make_kb(manager, "physics")
        record = registry.ensure_registered(manager.base_dir, "physics", created_by="t1")
        before = registry.refresh_documents(manager.base_dir, "physics")
        original = knowledge._record_documents
        called = []
        loop_thread = threading.get_ident()

        def record_on_worker(*args):
            assert threading.get_ident() != loop_thread
            called.append(True)
            original(*args)

        monkeypatch.setattr(knowledge, "_record_documents", record_on_worker)
        if fail_commit:
            _fail_registry_commit(monkeypatch)
        files = [
            UploadFile(filename="doc.md", file=BytesIO(b"changed")),
            UploadFile(filename="new.md", file=BytesIO(b"new")),
        ]
        try:
            call = knowledge._save_uploaded_files_off_loop(
                files, folder / "raw", registry_name="physics"
            )
            if fail_commit:
                with pytest.raises(sqlite3.OperationalError, match="injected"):
                    await call
                assert (folder / "raw/doc.md").read_text() == "hello"
                assert not (folder / "raw/new.md").exists()
                assert registry.list_documents(record.ref) == before
            else:
                await call
                current = registry.find_document(record.ref, "doc.md")
                assert current.doc_id == before[0].doc_id and current.version == 2
                assert registry.find_document(record.ref, "new.md").version == 1
            assert called == [True]
        finally:
            for file in files:
                await file.close()


def test_ready_registry_failure_does_not_publish_success(as_user, make_kb, monkeypatch):
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        make_kb(manager, "physics")
        registry.ensure_registered(manager.base_dir, "physics", created_by="t1")
        manager.update_kb_status("physics", "processing")
        _fail_registry_commit(monkeypatch)
        from deeptutor.knowledge.document_mutation import DocumentRecordingError

        with pytest.raises(DocumentRecordingError, match="injected") as failed:
            manager.update_kb_status("physics", "ready")
        assert isinstance(failed.value.__cause__, sqlite3.OperationalError)
        assert manager.get_kb_status("physics")["status"] == "processing"
        assert registry.list_documents(registry.find_by_location(manager.base_dir, "physics").ref) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("duplicates", [False, True])
async def test_upload_completion_hashing_stays_off_event_loop(
    knowledge, as_user, make_kb, monkeypatch, duplicates
):
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        folder = make_kb(manager, "physics")
        record = registry.ensure_registered(manager.base_dir, "physics", created_by="t1")
        loop_thread = threading.get_ident()
        original = registry.document_hashes
        threads = []
        started = threading.Event()
        release = threading.Event()
        publications = []

        def hash_on_worker(raw):
            threads.append(threading.get_ident())
            started.set()
            assert release.wait(5), "ready hashing was not awaited off-loop"
            return original(raw)

        class DuplicateAdder:
            def __init__(self, **kwargs):
                pass

            def add_documents(self, *args, **kwargs):
                return [] if duplicates else [folder / "raw/doc.md"]

            async def process_new_documents(self, files):
                return SimpleNamespace(has_failures=False, processed_count=1)

            def update_metadata(self, count):
                pass

        def published(*args, **kwargs):
            assert release.is_set()
            publications.append(registry.list_documents(record.ref))

        monkeypatch.setattr(registry, "document_hashes", hash_on_worker)
        monkeypatch.setattr(knowledge, "DocumentAdder", DuplicateAdder)
        monkeypatch.setattr(knowledge.get_task_stream_manager(), "emit_complete", published)
        task = asyncio.create_task(knowledge.run_upload_processing_task(
            "physics", str(manager.base_dir), [str(folder / "raw/doc.md")],
            "task13-upload-completion", rag_provider="llamaindex",
        ))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            assert not task.done()
            assert publications == []
            assert registry.list_documents(record.ref) == []
        finally:
            release.set()
            await task
        assert threads and all(thread != loop_thread for thread in threads)
        assert registry.list_documents(record.ref)[0].rel_path == "doc.md"
        assert publications and all(docs[0].version == 1 for docs in publications)


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["duplicates", "append", "bootstrap", "initialize"])
async def test_cli_ready_hashing_is_awaited_before_return(
    as_user, make_kb, monkeypatch, entrypoint
):
    import importlib

    adding = importlib.import_module("deeptutor.knowledge.add_documents")
    initializing = importlib.import_module("deeptutor.knowledge.initializer")
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        folder = make_kb(manager, "physics")
        record = registry.ensure_registered(manager.base_dir, "physics", created_by="t1")
        [first] = registry.refresh_documents(manager.base_dir, "physics")
        source = folder / "raw/doc.md"
        source.write_text("changed")
        manager.update_kb_status("physics", "processing")
        original = registry.document_hashes
        started = threading.Event()
        release = threading.Event()
        threads = []
        loop_thread = threading.get_ident()

        def hash_on_worker(raw):
            threads.append(threading.get_ident())
            started.set()
            assert release.wait(5), "ready hashing was not awaited off-loop"
            return original(raw)

        class Adder:
            rag_provider = "llamaindex"

            def __init__(self, **kwargs):
                if entrypoint == "bootstrap":
                    raise ValueError("not initialized")

            def add_documents(self, *args, **kwargs):
                return [] if entrypoint == "duplicates" else [source]

            async def process_new_documents(self, files):
                return SimpleNamespace(has_failures=False, processed_count=1)

            def update_metadata(self, count):
                pass

        class Service:
            def __init__(self, **kwargs):
                pass

            async def initialize(self, **kwargs):
                kwargs["indexed_file_callback"]([str(source)])
                return True

            def _resolve_provider(self, name):
                return "llamaindex"

        class Initializer:
            def __init__(self, **kwargs):
                pass

            def create_directory_structure(self):
                pass

            def copy_documents(self, files):
                return files

            async def process_documents(self):
                pass

        monkeypatch.setattr(registry, "document_hashes", hash_on_worker)
        monkeypatch.setattr(adding, "DocumentAdder", Adder)
        monkeypatch.setattr(adding, "RAGService", Service)
        monkeypatch.setattr(initializing, "KnowledgeBaseInitializer", Initializer)
        invoke = (
            initializing.initialize_knowledge_base
            if entrypoint == "initialize" else adding.add_documents
        )
        task = asyncio.create_task(invoke(
            "physics", [str(source)], base_dir=str(manager.base_dir)
        ))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            assert not task.done()
            assert registry.find_document(record.ref, "doc.md") == first
            assert manager.get_kb_status("physics")["status"] == "processing"
        finally:
            release.set()
            result = await task
        assert result == (0 if entrypoint == "duplicates" else 1)
        current = registry.find_document(record.ref, "doc.md")
        assert current.doc_id == first.doc_id and current.version == 2
        assert manager.get_kb_status("physics")["status"] == "ready"
        assert threads and all(thread != loop_thread for thread in threads)


async def _create(knowledge, monkeypatch, make_kb, name="physics", visibility="private", **kwargs):
    async def fake_create(*_args):
        record = knowledge._register_created_kb(name, _args[-1])
        make_kb(current_kb_manager(), name)
        return {"name": name, "kb_id": record.kb_id, "ref": record.ref}

    monkeypatch.setattr(knowledge, "_create_knowledge_base_owned", fake_create)
    return await knowledge.create_knowledge_base(
        BackgroundTasks(), name=name, files=[], rag_provider="llamaindex",
        pageindex_mode="", search_mode="", rel_paths=[], indexing_llm="",
        embedding_model="", storage_workspace_id=kwargs.get("storage_workspace_id"),
        visibility=visibility,
    )


@pytest.mark.asyncio
async def test_create_registers_and_replaces_stale_record(knowledge, as_user, make_kb, monkeypatch):
    with as_user("t1", role="teacher"):
        stale = registry.register_kb(
            base_dir=current_kb_manager().base_dir, name="physics", kind="uploaded",
            created_by="old",
        )
        registry.add_reference(
            stale.kb_id, referrer_kind="partner", referrer_id="old", created_by="old"
        )
        result = await _create(knowledge, monkeypatch, make_kb, visibility="public")
        private = await _create(knowledge, monkeypatch, make_kb, name="drafts")
    record = registry.get_kb(result["kb_id"])
    assert record.ref == result["ref"]
    assert record.created_by == "t1" and record.visibility == "public"
    assert registry.get_kb(private["kb_id"]).visibility == "private"
    assert registry.get_kb(stale.kb_id) is None
    assert registry.list_unavailable_references("partner", "old")[0].reason == "kb_deleted"


@pytest.mark.asyncio
@pytest.mark.parametrize(("visibility", "status"), [("public", 403), ("shared", 400)])
async def test_create_rejects_before_creating(
    knowledge, as_user, make_kb, monkeypatch, visibility, status
):
    with as_user("u1"):
        with pytest.raises(HTTPException) as error:
            await _create(knowledge, monkeypatch, make_kb, visibility=visibility)
        assert error.value.status_code == status
        assert "physics" not in current_kb_manager().list_knowledge_bases()
        assert registry.list_records() == []


@pytest.mark.asyncio
async def test_create_registers_in_destination_workspace(knowledge, as_user, make_kb, monkeypatch):
    from deeptutor.services.workspace import ContentWorkspaceService
    from deeptutor.services.workspace.context import workspace_context

    with as_user("t1", role="teacher"):
        home = current_kb_manager().base_dir
        wid = ContentWorkspaceService().create_workspace("Storage")["workspace_id"]
        result = await _create(
            knowledge, monkeypatch, make_kb, storage_workspace_id=wid
        )
        with workspace_context(wid):
            destination = current_kb_manager().base_dir
    record = registry.get_kb(result["kb_id"])
    assert Path(record.base_dir) == Path(destination)
    assert Path(record.base_dir) != Path(home)
    assert record.created_by == "t1"


async def _create_uploaded(knowledge, monkeypatch, visibility="private"):
    monkeypatch.setattr(knowledge, "_assert_provider_ready", lambda *_args: None)
    monkeypatch.setattr(knowledge, "_resolve_embedding_form", lambda *_args: (None, None))
    upload = UploadFile(filename="doc.txt", file=BytesIO(b"physics notes"))
    try:
        return await knowledge.create_knowledge_base(
            BackgroundTasks(), name="physics", files=[upload], rag_provider="llamaindex",
            pageindex_mode="", search_mode="", rel_paths=[], indexing_llm="",
            embedding_model="", storage_workspace_id=None, visibility=visibility,
        )
    finally:
        await upload.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("with_stale", [False, True])
@pytest.mark.parametrize("visibility", ["private", "public"])
async def test_listing_during_upload_preserves_creation_identity(
    knowledge, as_user, monkeypatch, with_stale, visibility
):
    from deeptutor.multi_user.knowledge_access import list_visible_knowledge_bases, resolve_kb

    with as_user("t1", role="teacher"):
        base_dir = current_kb_manager().base_dir
        stale = None
        if with_stale:
            stale = registry.register_kb(
                base_dir=base_dir, name="physics", kind="uploaded", created_by="old"
            )
            registry.add_reference(
                stale.kb_id, referrer_kind="partner", referrer_id="old", created_by="old"
            )
        original_save = knowledge._save_uploaded_files_off_loop
        listed = []

        async def save_then_list(*args, **kwargs):
            saved = await original_save(*args, **kwargs)
            # The actual creation helper is still awaiting its save operation.
            # Run the same access listing used by the threaded list endpoint.
            listed.extend(await asyncio.to_thread(list_visible_knowledge_bases))
            [item] = [row for row in listed if row["name"] == "physics"]
            if stale is not None:
                assert registry.get_kb(stale.kb_id) is None
                assert item["kb_id"] != stale.kb_id
                assert registry.list_unavailable_references("partner", "old")[0].reason == "kb_deleted"
            assert item["created_by"] == "t1"
            registry.add_reference(
                item["kb_id"], referrer_kind="partner", referrer_id="during-upload",
                created_by="t1",
            )
            return saved

        monkeypatch.setattr(knowledge, "_save_uploaded_files_off_loop", save_then_list)
        result = await _create_uploaded(knowledge, monkeypatch, visibility)
        [item] = [row for row in listed if row["name"] == "physics"]
        assert result["kb_id"] == item["kb_id"]
        assert result["ref"] == item["ref"]
        record = registry.get_kb(item["kb_id"])
        assert record.created_by == "t1" and record.visibility == visibility
        [doc] = registry.list_documents(record.ref)
        assert doc.rel_path == "doc.txt" and doc.version == 1
        assert resolve_kb(item["ref"]).name == "physics"
        assert [ref.referrer_id for ref in registry.list_references(record.kb_id)] == ["during-upload"]
        assert registry.list_unavailable_references("partner", "during-upload") == []


@pytest.mark.asyncio
async def test_duplicate_create_preserves_live_identity(
    knowledge, as_user, make_kb, monkeypatch
):
    with as_user("t1", role="teacher"):
        make_kb(current_kb_manager(), "physics")
        record = registry.ensure_registered(current_kb_manager().base_dir, "physics", created_by="t1")
        registry.add_reference(
            record.kb_id, referrer_kind="partner", referrer_id="live", created_by="t1"
        )
        with pytest.raises(HTTPException) as error:
            await _create_uploaded(knowledge, monkeypatch)
        assert error.value.status_code == 400
        assert registry.get_kb(record.kb_id) == record
        assert len(registry.list_references(record.kb_id)) == 1
        assert registry.list_unavailable_references("partner", "live") == []


@pytest.mark.asyncio
async def test_new_identity_is_committed_before_catalog_publication(
    knowledge, as_user, monkeypatch
):
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        stale = registry.register_kb(
            base_dir=manager.base_dir, name="physics", kind="uploaded", created_by="old"
        )
        registry.add_reference(
            stale.kb_id, referrer_kind="partner", referrer_id="old", created_by="old"
        )
        original_update = manager.update_kb_status
        observed = []

        def observe_publication(*args, **kwargs):
            # Independent connections must already see the replacement before
            # the first initializing entry becomes visible in kb_config.json.
            assert registry.get_kb(stale.kb_id) is None
            record = registry.find_by_location(manager.base_dir, "physics")
            assert record.created_by == "t1" and record.visibility == "public"
            assert registry.list_unavailable_references("partner", "old")[0].reason == "kb_deleted"
            observed.append(record.kb_id)
            return original_update(*args, **kwargs)

        monkeypatch.setattr(manager, "update_kb_status", observe_publication)
        result = await _create_uploaded(knowledge, monkeypatch, "public")
        assert observed and set(observed) == {result["kb_id"]}


@pytest.mark.asyncio
async def test_same_name_create_cannot_replace_in_progress_identity(
    knowledge, as_user, monkeypatch
):
    with as_user("t1", role="teacher"):
        original_save = knowledge._save_uploaded_files_off_loop
        observed = []

        async def save_with_competing_create(*args, **kwargs):
            record = registry.find_by_location(current_kb_manager().base_dir, "physics")
            with pytest.raises(HTTPException) as error:
                await _create_uploaded(knowledge, monkeypatch)
            assert error.value.status_code == 409
            assert registry.get_kb(record.kb_id) == record
            observed.append(record.kb_id)
            return await original_save(*args, **kwargs)

        monkeypatch.setattr(knowledge, "_save_uploaded_files_off_loop", save_with_competing_create)
        result = await _create_uploaded(knowledge, monkeypatch)
        assert observed == [result["kb_id"]]


@pytest.mark.asyncio
async def test_referenced_delete_revokes_without_force(knowledge, as_user, make_kb, monkeypatch):
    """Rule R1: a partner reference never blocks the owner; force stays admin-only."""
    with as_user("t1", role="teacher"):
        result = await _create(knowledge, monkeypatch, make_kb, visibility="public")
        registry.add_reference(
            result["kb_id"], referrer_kind="partner", referrer_id="ada", created_by="t2"
        )
        with pytest.raises(HTTPException) as error:
            knowledge._delete_kb(result["ref"], force=True)
        assert error.value.status_code == 403
        assert "physics" in current_kb_manager().list_knowledge_bases()
        knowledge._delete_kb(result["ref"])
    assert registry.get_kb(result["kb_id"]) is None
    [gone] = registry.list_unavailable_references("partner", "ada")
    assert gone.reason == "kb_deleted" and gone.kb_name == "physics"


@pytest.mark.asyncio
@pytest.mark.parametrize("registered", [True, False])
async def test_force_requires_admin_even_without_references(
    knowledge, as_user, make_kb, monkeypatch, registered
):
    with as_user("t1", role="teacher"):
        if registered:
            await _create(knowledge, monkeypatch, make_kb)
        else:
            make_kb(current_kb_manager(), "physics")
        with pytest.raises(HTTPException) as error:
            knowledge._delete_kb("physics", force=True)
        assert error.value.status_code == 403
        assert "physics" in current_kb_manager().list_knowledge_bases()


@pytest.mark.asyncio
@pytest.mark.parametrize("body_route", [True, False])
async def test_delete_routes_unregister(knowledge, as_user, make_kb, monkeypatch, body_route):
    with as_user("t1", role="teacher"):
        result = await _create(knowledge, monkeypatch, make_kb)
        if body_route:
            await knowledge.delete_knowledge_base_by_name(
                knowledge.DeleteKnowledgeBaseRequest(name=result["ref"])
            )
        else:
            await knowledge.delete_knowledge_base(result["ref"])
    assert registry.get_kb(result["kb_id"]) is None


@pytest.mark.asyncio
async def test_failed_delete_keeps_registration_and_references(
    knowledge, as_user, make_kb, monkeypatch
):
    with as_user("root", role="admin"):
        result = await _create(knowledge, monkeypatch, make_kb)
        registry.add_reference(
            result["kb_id"], referrer_kind="partner", referrer_id="ada", created_by="root"
        )

        def fail(*_args, **_kwargs):
            raise RuntimeError("delete failed")

        monkeypatch.setattr(current_kb_manager(), "detach_knowledge_base", fail)
        with pytest.raises(HTTPException) as error:
            knowledge._delete_kb(result["ref"], force=True)
        assert error.value.status_code == 500
    assert registry.get_kb(result["kb_id"]) is not None
    assert len(registry.list_references(result["kb_id"])) == 1
    assert registry.list_unavailable_references("partner", "ada") == []


@pytest.mark.asyncio
async def test_delete_serializes_concurrent_reference_writers(
    knowledge, as_user, make_kb, monkeypatch
):
    with as_user("t1", role="teacher"):
        result = await _create(knowledge, monkeypatch, make_kb)
        manager = current_kb_manager()
        original = manager.detach_knowledge_base
        started = threading.Event()

        def add_reference():
            started.set()
            registry.add_reference(
                result["kb_id"], referrer_kind="partner", referrer_id="racing", created_by="t1"
            )

        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = []

            def delete(*args, **kwargs):
                # A separate SQLite connection proves the DB lock, not only a
                # process-local mutex, covers detaching the KB from its name.
                with sqlite3.connect(registry.DB_PATH_OVERRIDE, timeout=0) as conn:
                    with pytest.raises(sqlite3.OperationalError, match="locked"):
                        conn.execute("BEGIN IMMEDIATE")
                pending.append(executor.submit(add_reference))
                assert started.wait(2)
                assert not pending[0].done()
                return original(*args, **kwargs)

            monkeypatch.setattr(manager, "detach_knowledge_base", delete)
            knowledge._delete_kb(result["ref"])
            with pytest.raises(registry.KbReferenceError):
                pending[0].result(timeout=5)
    assert registry.list_references(result["kb_id"]) == []


@pytest.mark.asyncio
async def test_visibility_and_reference_permissions(knowledge, as_user, make_kb, monkeypatch):
    with as_user("t1", role="teacher"):
        result = await _create(knowledge, monkeypatch, make_kb)
        body = await knowledge.set_kb_visibility(
            "physics", knowledge.KbVisibilityRequest(visibility="public")
        )
        assert body == {key: result[key] for key in ("kb_id", "ref")} | {"visibility": "public"}
        registry.add_reference(
            result["kb_id"], referrer_kind="partner", referrer_id="ada", created_by="t2"
        )
        refs = await knowledge.list_kb_references("physics")
        assert refs["kb_id"] == result["kb_id"] and refs["ref"] == result["ref"]
        # Other accounts' referrers only as counts (rule R1).
        assert refs["references"] == []
        assert refs["other_references"] == {"total": 1, "by_kind": {"partner": 1}}
        with pytest.raises(HTTPException) as error:
            await knowledge.set_kb_visibility(
                "physics", knowledge.KbVisibilityRequest(visibility="private", force=True)
            )
        assert error.value.status_code == 403
    with as_user("s1", role="student"):
        for operation in (
            knowledge.list_kb_references(result["ref"]),
            knowledge.set_kb_visibility(result["ref"], knowledge.KbVisibilityRequest(visibility="private")),
        ):
            with pytest.raises(HTTPException) as error:
                await operation
            assert error.value.status_code == 403
    with as_user("t1", role="teacher"):
        await knowledge.set_kb_visibility(
            result["ref"], knowledge.KbVisibilityRequest(visibility="private")
        )
    assert registry.list_unavailable_references("partner", "ada")[0].reason == "made_private"


@pytest.mark.asyncio
async def test_owner_references_allow_private_and_delete_revokes_them(
    knowledge, as_user, make_kb, monkeypatch
):
    with as_user("t1", role="teacher"):
        result = await _create(knowledge, monkeypatch, make_kb, visibility="public")
        registry.add_reference(
            result["kb_id"], referrer_kind="partner", referrer_id="own", created_by="t1"
        )
        await knowledge.set_kb_visibility(
            result["ref"], knowledge.KbVisibilityRequest(visibility="private")
        )
        assert len(registry.list_references(result["kb_id"])) == 1
        knowledge._delete_kb(result["ref"])
    assert registry.list_unavailable_references("partner", "own")[0].reason == "kb_deleted"


@pytest.mark.asyncio
async def test_plain_users_and_connectors_cannot_publish(knowledge, as_user, make_kb, monkeypatch):
    with as_user("u1"):
        await _create(knowledge, monkeypatch, make_kb)
        with pytest.raises(HTTPException) as error:
            await knowledge.set_kb_visibility("physics", knowledge.KbVisibilityRequest(visibility="public"))
        assert error.value.status_code == 403
    with as_user("t1", role="teacher"):
        make_kb(current_kb_manager(), "vault", {"type": "obsidian"})
        with pytest.raises(HTTPException) as error:
            await knowledge.set_kb_visibility("vault", knowledge.KbVisibilityRequest(visibility="public"))
        assert error.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("admin_library", [True, False])
async def test_legacy_read_only_resources_never_become_callers_property(
    knowledge, as_user, make_kb, monkeypatch, admin_library
):
    from deeptutor.multi_user import knowledge_access
    from deeptutor.multi_user.models import LOCAL_ADMIN_ID, KnowledgeResource

    with as_user("owner", role="admin" if admin_library else "teacher"):
        make_kb(current_kb_manager(), "legacy")
        base_dir = current_kb_manager().base_dir
    resource = KnowledgeResource(
        id="legacy", name="legacy", base_dir=base_dir,
        source="admin" if admin_library else "user", assigned=True, read_only=True,
    )
    # An upstream legacy reader has granted only read access, as in the
    # registry's ownership regression tests. It must never confer ownership.
    monkeypatch.setattr(knowledge, "resolve_kb", lambda *_args, **_kwargs: resource)
    monkeypatch.setattr(knowledge_access, "resolve_kb", lambda *_args, **_kwargs: resource)
    with as_user("t1", role="teacher"):
        with pytest.raises(HTTPException) as error:
            await knowledge.set_kb_visibility(
                "legacy", knowledge.KbVisibilityRequest(visibility="public")
            )
        assert error.value.status_code == 403
    record = registry.find_by_location(base_dir, "legacy")
    if admin_library:
        assert record.created_by == LOCAL_ADMIN_ID
    else:
        assert record is None


@pytest.mark.asyncio
async def test_config_refs_preserve_permissions_and_target_catalog(
    knowledge, as_user, make_kb, monkeypatch
):
    with as_user("t1", role="teacher"):
        result = await _create(knowledge, monkeypatch, make_kb, visibility="public")
        await knowledge.update_kb_config(result["ref"], {"search_mode": "naive"})
        with pytest.raises(HTTPException) as error:
            await knowledge.update_kb_config(result["ref"], {"embedding_model": "replacement"})
        assert error.value.status_code == 409
    with as_user("s1", role="student"):
        make_kb(current_kb_manager(), "physics")
        await knowledge.update_kb_config("physics", {"search_mode": "local"})
        assert (await knowledge.get_kb_config(result["ref"]))["config"]["search_mode"] == "naive"
        assert (await knowledge.get_kb_config("physics"))["config"]["search_mode"] == "local"
        with pytest.raises(HTTPException) as error:
            await knowledge.update_kb_config(result["ref"], {"search_mode": "global"})
        assert error.value.status_code == 403
    with as_user("t1", role="teacher"):
        await knowledge.set_kb_visibility("physics", knowledge.KbVisibilityRequest(visibility="private"))
    with as_user("s1", role="student"):
        with pytest.raises(HTTPException) as error:
            await knowledge.get_kb_config(result["ref"])
        assert error.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("reference_kind", ["registry", "legacy", "bare"])
async def test_config_cannot_bypass_workspace_selection(
    knowledge, as_user, make_kb, monkeypatch, reference_kind
):
    from deeptutor.services.workspace import ContentWorkspaceService
    from deeptutor.services.workspace.context import workspace_context

    with as_user("t1", role="teacher"):
        result = await _create(knowledge, monkeypatch, make_kb)
        ref = {"registry": result["ref"], "legacy": "account:kb:physics", "bare": "physics"}[reference_kind]
        wid = ContentWorkspaceService().create_workspace(
            "Empty", resources={"knowledge_bases": []}
        )["workspace_id"]
        with workspace_context(wid):
            for operation in (
                knowledge.get_kb_config(ref),
                knowledge.update_kb_config(ref, {"search_mode": "naive"}),
            ):
                with pytest.raises(HTTPException) as error:
                    await operation
                assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_config_provider_guard_uses_referenced_location(
    knowledge, as_user, make_kb, monkeypatch
):
    from deeptutor.services.rag import index_probe

    with as_user("t1", role="teacher"):
        result = await _create(knowledge, monkeypatch, make_kb)
        expected = Path(registry.get_kb(result["kb_id"]).base_dir) / "physics"
    inspected = []

    def ready_index(kb_dir, provider):
        inspected.append((kb_dir, provider))
        return kb_dir == expected

    monkeypatch.setattr(index_probe, "has_ready_provider_index", ready_index)
    with as_user("root", role="admin"):
        with pytest.raises(HTTPException) as error:
            await knowledge.update_kb_config(result["ref"], {"rag_provider": "pageindex"})
        assert error.value.status_code == 409
        assert "ready llamaindex index" in error.value.detail
        assert inspected and set(inspected) == {(expected, "llamaindex")}
        assert (await knowledge.get_kb_config(result["ref"]))["config"]["rag_provider"] == "llamaindex"


@pytest.mark.asyncio
async def test_unknown_references_keep_404(knowledge, as_user):
    with as_user("t1", role="teacher"):
        for operation in (
            knowledge.get_kb_config("kb:missing"),
            knowledge.update_kb_config("kb:missing", {"search_mode": "naive"}),
            knowledge.delete_knowledge_base("kb:missing"),
            knowledge.list_kb_references("kb:missing"),
            knowledge.set_kb_visibility("kb:missing", knowledge.KbVisibilityRequest(visibility="public")),
        ):
            with pytest.raises(HTTPException) as error:
                await operation
            assert error.value.status_code == 404


def test_http_validation_and_force_forwarding(knowledge, as_user, monkeypatch):
    app = FastAPI()
    app.include_router(knowledge.router, prefix="/api")
    calls = []

    async def delete(name, *, force=False, confirm=False):
        calls.append((name, force, confirm))
        return {"message": "deleted"}

    monkeypatch.setattr(knowledge, "_delete_kb_off_loop", delete)
    with as_user("root", role="admin"), TestClient(app) as client:
        assert client.delete("/api/knowledge-bases/kb:abc?force=true").status_code == 200
        assert client.post("/api/knowledge-bases/delete", json={"name": "a/b", "force": True}).status_code == 200
        assert client.delete("/api/knowledge-bases/kb:c?confirm=true").status_code == 200
        assert client.post("/api/knowledge-bases/delete", json={"name": "d", "confirm": True}).status_code == 200
        assert client.put("/api/knowledge-bases/a/visibility", json={"visibility": "shared"}).status_code == 422
    assert calls == [
        ("kb:abc", True, False), ("a/b", True, False), ("kb:c", False, True), ("d", False, True),
    ]
