"""Deterministic Task13 publication, failure and mutation interleavings."""

import asyncio
from io import BytesIO
import json
import sqlite3
import threading
from types import SimpleNamespace

from fastapi import HTTPException, UploadFile
import pytest

from deeptutor.knowledge import document_mutation, registry
from deeptutor.multi_user.knowledge_access import current_kb_manager


@pytest.fixture
def knowledge():
    from deeptutor.api.routers import knowledge

    return knowledge


def _watch_second_lock(monkeypatch):
    attempted = threading.Event()
    original = document_mutation._lock
    calls = 0

    def observed(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            attempted.set()
        return original(*args)

    monkeypatch.setattr(document_mutation, "_lock", observed)
    return attempted


def _fail_document_recording(monkeypatch, failure):
    if failure == "hash":
        def fail(*args):
            raise OSError("injected hash failure")

        monkeypatch.setattr(registry, "document_hashes", fail)
        return
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
@pytest.mark.parametrize("operation", ["move", "folder_move", "delete", "upload"])
async def test_paused_refresh_serializes_raw_mutation(
    knowledge, as_user, make_kb, monkeypatch, operation
):
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        folder = make_kb(manager, "physics")
        source = "doc.md"
        if operation == "folder_move":
            (folder / "raw/part").mkdir()
            (folder / "raw/doc.md").rename(folder / "raw/part/doc.md")
            source = "part/doc.md"
        record = registry.ensure_registered(manager.base_dir, "physics", created_by="t1")
        [first] = registry.refresh_documents(manager.base_dir, "physics")
        entered, release = threading.Event(), threading.Event()
        original = registry.sync_documents
        loop_thread = threading.get_ident()

        def paused(*args):
            assert threading.get_ident() != loop_thread
            entered.set()
            assert release.wait(5)
            return original(*args)

        monkeypatch.setattr(registry, "sync_documents", paused)
        attempted = _watch_second_lock(monkeypatch)
        refresh = asyncio.create_task(asyncio.to_thread(
            registry.refresh_documents, manager.base_dir, "physics"
        ))
        mutation = None
        upload = UploadFile(filename="doc.md", file=BytesIO(b"replacement"))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            if operation == "upload":
                invoke = knowledge._save_uploaded_files_off_loop(
                    [upload], folder / "raw", registry_name="physics",
                )
            elif operation == "delete":
                invoke = knowledge.delete_kb_file(record.ref, source)
            else:
                invoke = knowledge.move_kb_file(
                    record.ref, knowledge.MoveFilePayload(
                        source="part" if operation == "folder_move" else source,
                        dest_folder="archive",
                    ),
                )
            mutation = asyncio.create_task(invoke)
            assert await asyncio.to_thread(attempted.wait, 5)
            assert not mutation.done()
            assert (folder / "raw" / source).exists()
        finally:
            release.set()
            await refresh
            if mutation is not None:
                await mutation
            await upload.close()
        documents = registry.list_documents(record.ref, include_deleted=True)
        assert len(documents) == 1
        [current] = documents
        assert current.doc_id == first.doc_id
        assert current.version == first.version + (operation == "upload")
        assert current.deleted == (operation == "delete")
        assert current.rel_path == (
            source if operation in {"delete", "upload"} else f"archive/{source}"
        )
        assert (folder / "raw" / source).exists() == (operation == "upload")
        if operation == "upload":
            assert (folder / "raw" / source).read_bytes() == b"replacement"
            assert current.content_hash == registry.document_hashes(folder / "raw")[source]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "first", "second"])
async def test_upload_publish_hash_commit_and_rollback_are_isolated(
    knowledge, as_user, make_kb, monkeypatch, failure
):
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        folder = make_kb(manager, "physics")
        record = registry.ensure_registered(manager.base_dir, "physics", created_by="t1")
        [first] = registry.refresh_documents(manager.base_dir, "physics")
        original = registry.upsert_documents
        entered, release = threading.Event(), threading.Event()
        calls = 0

        def paused(kb_id, hashes):
            nonlocal calls
            calls += 1
            invocation = calls
            if invocation == 1:
                entered.set()
                assert release.wait(5)
            if failure == ("first" if invocation == 1 else "second"):
                raise OSError("injected registration failure")
            return original(kb_id, hashes)

        monkeypatch.setattr(registry, "upsert_documents", paused)
        attempted = _watch_second_lock(monkeypatch)
        files = [UploadFile(filename="doc.md", file=BytesIO(value)) for value in (b"A", b"B")]
        tasks = []
        try:
            tasks.append(asyncio.create_task(knowledge._save_uploaded_files_off_loop(
                [files[0]], folder / "raw", registry_name="physics",
            )))
            assert await asyncio.to_thread(entered.wait, 5)
            tasks.append(asyncio.create_task(knowledge._save_uploaded_files_off_loop(
                [files[1]], folder / "raw", registry_name="physics",
            )))
            assert await asyncio.to_thread(attempted.wait, 5)
            assert not tasks[1].done()
            assert calls == 1
            assert (folder / "raw/doc.md").read_bytes() == b"A"
        finally:
            release.set()
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            for file in files:
                await file.close()
        assert [isinstance(result, OSError) for result in outcomes] == [
            failure == "first", failure == "second",
        ]
        assert (folder / "raw/doc.md").read_bytes() == (b"A" if failure == "second" else b"B")
        [current] = registry.list_documents(record.ref)
        assert current.content_hash == registry.document_hashes(folder / "raw")["doc.md"]
        assert current.doc_id == first.doc_id
        assert current.version == (3 if failure is None else 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("intervention", ["none", "reference", "whole_reference", "authority"])
async def test_provider_await_releases_global_db_and_revalidates_d18(
    knowledge, as_user, make_kb, monkeypatch, intervention
):
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        folder = make_kb(manager, "physics")
        record = registry.ensure_registered(manager.base_dir, "physics", created_by="t1")
        [document] = registry.refresh_documents(manager.base_dir, "physics")
        entered, release = asyncio.Event(), asyncio.Event()
        original_entry = knowledge._load_kb_entry_or_404
        monkeypatch.setattr(knowledge, "_load_kb_entry_or_404", lambda *a: {
            **original_entry(*a), "rag_provider": "pageindex",
        })

        async def remove(*args):
            entered.set()
            await release.wait()

        monkeypatch.setattr("deeptutor.services.rag.factory.get_pipeline",
                            lambda *a, **k: SimpleNamespace(remove_document=remove))
        task = asyncio.create_task(knowledge.delete_kb_file(record.ref, "doc.md"))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            # A zero-timeout probe avoids a 30-second loop stall on regression.
            with sqlite3.connect(registry._db_path(), timeout=0) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.rollback()
            other = registry.register_kb(
                base_dir=manager.base_dir, name="unrelated", kind="uploaded", created_by="t1"
            )
            await asyncio.wait_for(asyncio.to_thread(
                registry.upsert_documents, other.ref, {"other.md": "sha256:other"}
            ), 2)
            pulse = asyncio.Event()
            asyncio.get_running_loop().call_soon(pulse.set)
            await asyncio.wait_for(pulse.wait(), 1)
            if intervention in {"reference", "whole_reference"}:
                registry.add_reference(
                    record.ref, referrer_kind="mastery_template", referrer_id="late",
                    doc_ids=[document.doc_id] if intervention == "reference" else None,
                    created_by="t1",
                )
            elif intervention == "authority":
                with registry._write() as connection:
                    connection.execute(
                        "UPDATE kb_registry SET created_by = 'other', visibility = 'public' "
                        "WHERE kb_id = ?",
                        (record.kb_id,),
                    )
        finally:
            release.set()
            if intervention == "none":
                await task
            else:
                with pytest.raises(HTTPException) as denied:
                    await task
                assert denied.value.status_code == (403 if intervention == "authority" else 409)
                if intervention != "authority":
                    assert denied.value.detail["code"] == (
                        "document_referenced" if intervention == "reference"
                        else "requires_confirmation"
                    )
        assert (folder / "raw/doc.md").exists() == (intervention != "none")
        assert registry.find_document(record.ref, "doc.md").deleted == (intervention == "none")


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", ["duplicates", "llamaindex", "lightrag"])
@pytest.mark.parametrize("failure", ["hash", "commit"])
async def test_tracker_background_required_failure_never_emits_completion(
    knowledge, as_user, make_kb, monkeypatch, branch, failure
):
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        folder = make_kb(manager, "physics")
        record = registry.ensure_registered(manager.base_dir, "physics", created_by="t1")
        [first] = registry.refresh_documents(manager.base_dir, "physics")
        (folder / "raw/doc.md").write_text("changed")
        (folder / "raw/new.md").write_text("new linked-folder copy")

        _fail_document_recording(monkeypatch, failure)

        class Adder:
            def __init__(self, **kwargs):
                pass

            def add_documents(self, *args, **kwargs):
                return [] if branch == "duplicates" else [folder / "raw/doc.md"]

            async def process_new_documents(self, files):
                return SimpleNamespace(has_failures=False, processed_count=1)

            def update_metadata(self, count):
                pass

        complete, failed = [], []
        monkeypatch.setattr(knowledge, "DocumentAdder", Adder)
        stream = knowledge.get_task_stream_manager()
        monkeypatch.setattr(stream, "emit_complete", lambda *a, **k: complete.append(a))
        monkeypatch.setattr(stream, "emit_failed", lambda *a, **k: failed.append(a))
        task_id = knowledge.TaskIDManager.get_instance().generate_task_id(
            "kb_upload", str(folder)
        )
        await knowledge.run_upload_processing_task(
            "physics", str(manager.base_dir), [str(folder / "raw/new.md")], task_id,
            rag_provider="lightrag" if branch == "lightrag" else "llamaindex",
        )
        assert complete == [] and len(failed) == 1
        assert knowledge.TaskIDManager.get_instance().get_task_metadata(task_id)["status"] == "error"
        assert json.loads((folder / ".progress.json").read_text())["stage"] == "error"
        assert registry.list_documents(record.ref) == [first]


@pytest.mark.asyncio
@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("fail_recording", [False, True])
async def test_github_final_removal_refresh_precedes_source_success(
    as_user, make_kb, monkeypatch, mixed, fail_recording
):
    from deeptutor.services.github_source import sync

    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        folder = make_kb(manager, "physics")
        raw = folder / "raw"
        (raw / "kept.md").write_text("before")
        record = registry.ensure_registered(manager.base_dir, "physics", created_by="t1")
        before = {d.rel_path: d for d in registry.refresh_documents(manager.base_dir, "physics")}
        states = []
        loop_thread = threading.get_ident()
        original = registry.document_hashes

        def hashes(path):
            assert threading.get_ident() != loop_thread
            assert not (raw / "doc.md").exists()
            if fail_recording:
                raise OSError("injected final refresh")
            return original(path)

        class Client:
            async def get_latest_commit_sha(self, *args):
                return "new"

            async def compare_commits(self, *args):
                return [SimpleNamespace(path="doc.md", status="removed")] + (
                    [SimpleNamespace(path="kept.md", status="modified"),
                     SimpleNamespace(path="added.md", status="added")] if mixed else []
                )

            async def download_file(self, *args):
                return b"after"

        async def index(*args, **kwargs):
            assert (raw / "doc.md").exists(), "reproduce index-before-final-removal ordering"
            return 2

        def succeeded(*args, **kwargs):
            assert registry.find_document(record.ref, "doc.md").deleted
            states.append("success")

        monkeypatch.setattr(sync, "_index_files", index)
        monkeypatch.setattr(sync, "_record_sync_success", succeeded)
        monkeypatch.setattr(sync, "_record_sync_failure", lambda *a: states.append("failure"))
        monkeypatch.setattr(registry, "document_hashes", hashes)
        result = await sync.sync_source(
            "physics", {"id": "s", "repo": "owner/repo", "last_synced_sha": "old"},
            base_dir=str(manager.base_dir), client=Client(),
        )
        assert result.ok == (not fail_recording)
        assert states == (["failure"] if fail_recording else ["success"])
        if fail_recording:
            assert registry.find_document(record.ref, "doc.md") == before["doc.md"]
        else:
            assert result.files_removed == 1
            current = registry.find_document(record.ref, "kept.md")
            assert current.doc_id == before["kept.md"].doc_id
            assert current.version == (2 if mixed else 1)
            assert registry.find_document(record.ref, "doc.md").deleted
            if mixed:
                assert registry.find_document(record.ref, "added.md").version == 1
            assert {d.rel_path: d.content_hash for d in registry.list_documents(record.ref)} == original(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_recording", [False, True])
async def test_web_removal_and_changes_recorded_after_rebuild(
    as_user, make_kb, monkeypatch, fail_recording
):
    from deeptutor.services.web_source import sync

    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        folder = make_kb(manager, "physics")
        raw = folder / "raw"
        (raw / "kept.md").write_text("before")
        record = registry.ensure_registered(manager.base_dir, "physics", created_by="t1")
        before = {d.rel_path: d for d in registry.refresh_documents(manager.base_dir, "physics")}
        states, indexed = [], []
        loop_thread = threading.get_ident()
        original = registry.document_hashes

        async def crawl(*args, **kwargs):
            (raw / "kept.md").write_text("after")
            (raw / "added.md").write_text("new")
            return SimpleNamespace(
                ok=True, pages_removed=["doc.md"], pages_added=["added.md"],
                pages_updated=["kept.md"], pages_unchanged=[], changed_paths=[],
                page_hashes={}, page_count=2, navigation={}, pairings=[], url="https://example.test",
            )

        class Service:
            def __init__(self, **kwargs):
                pass

            async def initialize(self, **kwargs):
                assert not (raw / "doc.md").exists()
                indexed.extend(kwargs["file_paths"])
                return True

        def hashes(path):
            assert threading.get_ident() != loop_thread
            assert indexed
            if fail_recording:
                raise OSError("injected final refresh")
            return original(path)

        def state(self, **kwargs):
            states.append(kwargs["last_sync_status"])
            if states[-1] == "success":
                assert registry.find_document(record.ref, "doc.md").deleted
                assert registry.find_document(record.ref, "added.md").version == 1

        monkeypatch.setattr(sync, "crawl_and_diff", crawl)
        monkeypatch.setattr("deeptutor.services.rag.service.RAGService", Service)
        monkeypatch.setattr(type(manager), "update_web_source_state", state)
        monkeypatch.setattr(registry, "document_hashes", hashes)
        result = await sync.sync_source(
            "physics", {"id": "s", "url": "https://example.test"}, base_dir=str(manager.base_dir),
        )
        assert result.ok == (not fail_recording)
        assert states == (["error"] if fail_recording else ["success"])
        if not fail_recording:
            current = registry.find_document(record.ref, "kept.md")
            assert current.doc_id == before["kept.md"].doc_id and current.version == 2
            assert {d.rel_path: d.content_hash for d in registry.list_documents(record.ref)} == original(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["initialize", "reindex", "bootstrap"])
@pytest.mark.parametrize("failure", [None, "hash", "commit"])
async def test_native_publication_waits_for_document_versions(
    as_user, make_kb, monkeypatch, entrypoint, failure
):
    from deeptutor.knowledge.progress_tracker import ProgressStage, ProgressTracker
    from deeptutor.services.embedding.config import EmbeddingConfig
    from deeptutor.services.rag.pipelines.lightrag import indexing_policy, storage
    from deeptutor.services.rag.pipelines.lightrag.pipeline import BatchOutcome, LightRagPipeline

    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        folder = make_kb(manager, "physics")
        record = registry.ensure_registered(manager.base_dir, "physics", created_by="t1")
        [first] = registry.refresh_documents(manager.base_dir, "physics")
        (folder / "raw/doc.md").write_text("new native version")
        embedding = EmbeddingConfig(model="test", dim=3, api_key="fake")
        snapshot = SimpleNamespace(
            embedding_config=embedding, target_bound=False,
            persisted_policy=lambda: {"policy": "pinned", "fingerprint": "a" * 64},
        )
        monkeypatch.setattr("deeptutor.services.embedding.get_embedding_config", lambda: embedding)
        monkeypatch.setattr(
            "deeptutor.services.rag.pipelines.lightrag.engine.installed_version",
            lambda: "isolated-test",
        )
        monkeypatch.setattr(indexing_policy, "revalidate_snapshot", lambda value: value)
        pipeline = LightRagPipeline(str(manager.base_dir))
        monkeypatch.setattr(pipeline, "_ensure_available", lambda: None)
        previous = None
        if entrypoint == "reindex":
            previous = folder / "version-1"
            previous.mkdir()
            (previous / "kv_store_doc_status.json").write_text(
                '{"old": {"status": "processed"}}'
            )
            storage.write_meta(previous, indexing_policy=snapshot.persisted_policy(),
                               embedding_config=embedding)
        tracker = ProgressTracker("physics", manager.base_dir)
        tracker.update(ProgressStage.PROCESSING_DOCUMENTS)
        notifications, candidates, prepared = [], [], []
        tracker.set_callback(lambda progress: notifications.append(progress["stage"]))
        entered, release = threading.Event(), threading.Event()
        original_hashes = registry.document_hashes
        loop_thread = threading.get_ident()
        if failure == "commit":
            _fail_document_recording(monkeypatch, failure)

        def paused_hashes(raw):
            assert threading.get_ident() != loop_thread
            entered.set()
            assert release.wait(5)
            if failure == "hash":
                raise OSError("injected hash failure")
            return original_hashes(raw)

        async def indexed(root, *args, **kwargs):
            candidates.append(root)
            (root / "kv_store_doc_status.json").write_text(
                '{"new": {"status": "processed"}}'
            )
            return BatchOutcome(
                1, accepted=1, processed=("doc.md",),
                indexing_policy=snapshot.persisted_policy(),
            )

        def before_publish(root):
            current = registry.find_document(record.ref, "doc.md")
            assert current.doc_id == first.doc_id and current.version == 2
            tracker.update(
                ProgressStage.COMPLETED, current=1, total=1, indexed_count=1,
                index_changed=True, index_action=entrypoint, publication_version=root.name,
            )
            assert tracker.get_progress()["stage"] == "processing_documents"
            prepared.append(root)

        monkeypatch.setattr(pipeline, "_run_indexing", indexed)
        monkeypatch.setattr(registry, "document_hashes", paused_hashes)
        invoke = pipeline.add_documents if entrypoint == "bootstrap" else pipeline.initialize
        task = asyncio.create_task(invoke(
            "physics", [str(folder / "raw/doc.md")], indexing_snapshot=snapshot,
            before_publish=before_publish,
        ))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            assert not task.done()
            assert registry.find_document(record.ref, "doc.md") == first
            assert storage.latest_published_root(folder) == previous
            assert not storage.meta_is_native_published(candidates[0])
            assert tracker.get_progress()["stage"] == "processing_documents"
            assert prepared == [] and "completed" not in notifications
        finally:
            release.set()
            if failure:
                with pytest.raises(document_mutation.DocumentRecordingError, match="injected"):
                    await task
            else:
                assert await task is True
        if failure:
            assert registry.find_document(record.ref, "doc.md") == first
            assert storage.latest_published_root(folder) == previous
            assert prepared == []
            assert tracker.get_progress()["stage"] != "completed"
        else:
            assert prepared == candidates
            assert storage.latest_published_root(folder) == candidates[0]
            assert tracker.get_progress()["stage"] == "completed"
            current = registry.find_document(record.ref, "doc.md")
            assert current.doc_id == first.doc_id and current.version == 2
            assert current.content_hash == original_hashes(folder / "raw")["doc.md"]


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["initialize", "reindex"])
@pytest.mark.parametrize("failure", ["hash", "commit"])
async def test_published_background_job_propagates_required_recording_failure(
    knowledge, as_user, make_kb, monkeypatch, entrypoint, failure
):
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        folder = make_kb(manager, "physics")
        record = registry.ensure_registered(manager.base_dir, "physics", created_by="t1")
        [first] = registry.refresh_documents(manager.base_dir, "physics")
        (folder / "raw/doc.md").write_text("changed")
        _fail_document_recording(monkeypatch, failure)
        complete, failed = [], []
        stream = knowledge.get_task_stream_manager()
        monkeypatch.setattr(stream, "emit_complete", lambda *a, **k: complete.append(a))
        monkeypatch.setattr(stream, "emit_failed", lambda *a, **k: failed.append(a))
        task_id = knowledge.TaskIDManager.get_instance().generate_task_id(
            "kb_init", str(folder)
        )

        async def process():
            pass

        class Service:
            def __init__(self, **kwargs):
                pass

            async def initialize(self, **kwargs):
                return True

        monkeypatch.setattr("deeptutor.services.rag.service.RAGService", Service)
        if entrypoint == "initialize":
            initializer = SimpleNamespace(
                kb_name="physics", base_dir=manager.base_dir, raw_dir=folder / "raw",
                progress_tracker=None, index_published=True, process_documents=process,
            )
            await knowledge.run_initialization_task(initializer, task_id)
        else:
            await knowledge.run_reindex_task(
                "physics", str(manager.base_dir), task_id, "lightrag"
            )
        assert complete == [] and len(failed) == 1
        assert knowledge.TaskIDManager.get_instance().get_task_metadata(task_id)["status"] == "error"
        assert json.loads((folder / ".progress.json").read_text())["stage"] == "error"
        assert registry.list_documents(record.ref) == [first]


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["append", "bootstrap"])
@pytest.mark.parametrize("failure", ["hash", "commit"])
async def test_cli_lightrag_required_recording_failure_is_not_bookkeeping(
    as_user, make_kb, monkeypatch, entrypoint, failure
):
    import importlib

    adding = importlib.import_module("deeptutor.knowledge.add_documents")
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        folder = make_kb(manager, "physics")
        record = registry.ensure_registered(manager.base_dir, "physics", created_by="t1")
        [first] = registry.refresh_documents(manager.base_dir, "physics")
        source = folder / "raw/doc.md"
        source.write_text("changed")
        _fail_document_recording(monkeypatch, failure)

        class Adder:
            rag_provider = "lightrag"

            def __init__(self, **kwargs):
                if entrypoint == "bootstrap":
                    raise ValueError("not initialized")

            def add_documents(self, *args, **kwargs):
                return [source]

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
                return "lightrag"

        monkeypatch.setattr(adding, "DocumentAdder", Adder)
        monkeypatch.setattr(adding, "RAGService", Service)
        with pytest.raises(document_mutation.DocumentRecordingError, match="injected"):
            await adding.add_documents("physics", [str(source)], base_dir=str(manager.base_dir))
        assert manager.get_kb_status("physics")["status"] == "error"
        assert registry.list_documents(record.ref) == [first]


@pytest.mark.asyncio
async def test_cancelled_mutation_keeps_ownership_until_worker_exits(tmp_path):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def worker():
        entered.set()
        assert release.wait(5)
        finished.set()

    async def mutate():
        async with document_mutation.async_document_mutation(tmp_path, "kb"):
            await document_mutation.finish_document_worker(worker)

    task = asyncio.create_task(mutate())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        for _ in range(2):
            task.cancel()
            await asyncio.sleep(0)
        assert not task.done()
        lock = document_mutation._lock(tmp_path, "kb")
        acquired = lock.acquire(blocking=False)
        if acquired:
            lock.release()
        assert not acquired
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(finished.wait, 5)
    async with document_mutation.async_document_mutation(tmp_path, "kb"):
        assert finished.is_set()
