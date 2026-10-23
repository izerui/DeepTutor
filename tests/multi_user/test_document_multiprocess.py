"""Spawned workers share only temporary raw files, SQLite, and the native lock file."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from io import BytesIO
import multiprocessing
import os
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace

from fastapi import UploadFile
import pytest

from deeptutor.knowledge import document_mutation, registry
from deeptutor.services.rag.pipelines.lightrag import write_lock


def _receive(connection, expected):
    assert connection.poll(15), f"worker did not report {expected}"
    message = connection.recv()
    assert message == expected, message


def _process_job(base, database, operation, connection, *, pause=False, fail=False):
    """Use real mutation/rollback bodies with only identity and provider boundaries stubbed."""
    from deeptutor.api.routers import knowledge

    base = Path(base)
    raw = base / "kb/raw"
    registry.DB_PATH_OVERRIDE = Path(database)
    record = registry.find_by_location(base, "kb")
    manager = SimpleNamespace(
        base_dir=base, get_knowledge_base_path=lambda name: base / name,
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(knowledge, "_writable_kb", lambda name: (manager, "kb", record))
        patch.setattr(knowledge, "_load_kb_entry_or_404", lambda *args: {
            "rag_provider": "llamaindex", "status": "ready",
        })
        patch.setattr(knowledge, "get_current_user", lambda: SimpleNamespace(
            id="owner", is_admin=True,
        ))
        original_ownership = write_lock.write_ownership
        reported_busy = False

        @contextmanager
        def observed_ownership(kb_dir):
            nonlocal reported_busy
            try:
                with original_ownership(kb_dir):
                    yield
            except write_lock.WriteOwnershipBusy:
                if not reported_busy:
                    reported_busy = True
                    connection.send("busy")
                raise

        patch.setattr(document_mutation, "write_ownership", observed_ownership)

        def barrier():
            connection.send("paused")
            _receive(connection, "release")

        if operation in {"refresh", "sync_refresh"} and pause:
            original_sync = registry.sync_documents

            def paused_sync(*args):
                barrier()
                return original_sync(*args)

            patch.setattr(registry, "sync_documents", paused_sync)
        if operation.startswith("upload"):
            original_upsert = registry.upsert_documents

            def record_upload(*args):
                if pause:
                    barrier()
                if fail:
                    raise OSError("injected registration failure")
                return original_upsert(*args)

            patch.setattr(registry, "upsert_documents", record_upload)
        if operation in {"move", "delete"} and fail:
            original_open = registry._open
            original_replace = os.replace

            class FailingCommit:
                def __init__(self, connection):
                    self.connection = connection

                def __getattr__(self, name):
                    return getattr(self.connection, name)

                def execute(self, sql, *args):
                    if sql == "COMMIT":
                        raise OSError("injected registration failure")
                    return self.connection.execute(sql, *args)

            def restore(source, target):
                if Path(target) == raw / "doc.md":
                    barrier()
                return original_replace(source, target)

            patch.setattr(registry, "_open", lambda path: FailingCommit(original_open(path)))
            patch.setattr(os, "replace", restore)

        async def execute():
            if operation == "refresh":
                await document_mutation.run_document_worker(
                    base, "kb", registry.refresh_documents, base, "kb",
                )
            elif operation == "sync_refresh":
                registry.refresh_documents(base, "kb")
            elif operation.startswith("upload"):
                file = UploadFile(filename="doc.md", file=BytesIO(operation.encode()))
                try:
                    await knowledge._save_uploaded_files_off_loop(
                        [file], raw, registry_name="kb",
                    )
                finally:
                    await file.close()
            elif operation in {"move", "folder_move"}:
                await knowledge.move_kb_file("kb", knowledge.MoveFilePayload(
                    source="part" if operation == "folder_move" else "doc.md",
                    dest_folder="archive",
                ))
            elif operation == "delete":
                await knowledge.delete_kb_file("kb", "doc.md")
            elif operation.startswith("native"):
                from deeptutor.services.embedding.config import EmbeddingConfig
                from deeptutor.services.rag.pipelines.lightrag import indexing_policy
                from deeptutor.services.rag.pipelines.lightrag.pipeline import (
                    BatchOutcome, LightRagPipeline,
                )

                embedding = EmbeddingConfig(model="test", dim=3, api_key="fake")
                snapshot = SimpleNamespace(
                    embedding_config=embedding, target_bound=False,
                    persisted_policy=lambda: {"policy": "pinned", "fingerprint": "a" * 64},
                )
                patch.setattr("deeptutor.services.embedding.get_embedding_config", lambda: embedding)
                patch.setattr(
                    "deeptutor.services.rag.pipelines.lightrag.engine.installed_version",
                    lambda: "isolated-test",
                )
                patch.setattr(indexing_policy, "revalidate_snapshot", lambda value: value)
                pipeline = LightRagPipeline(str(base))
                patch.setattr(pipeline, "_ensure_available", lambda: None)

                async def indexed(root, *args, **kwargs):
                    (root / "kv_store_doc_status.json").write_text(
                        '{"doc": {"status": "processed"}}'
                    )
                    return BatchOutcome(
                        1, accepted=1, processed=("doc.md",),
                        indexing_policy=snapshot.persisted_policy(),
                    )

                def before_publish(root):
                    assert registry.find_document(record.ref, "doc.md").content_hash == (
                        registry.document_hashes(raw)["doc.md"]
                    )
                    assert not (root / "meta.json").exists()
                    barrier()

                patch.setattr(pipeline, "_run_indexing", indexed)
                invoke = (
                    pipeline.initialize if operation == "native_initialize"
                    else pipeline.add_documents
                )
                assert await invoke(
                    "kb", [str(raw / "doc.md")], indexing_snapshot=snapshot,
                    before_publish=before_publish,
                )
            else:
                raise AssertionError(operation)

        connection.send("ready")
        _receive(connection, "go")
        try:
            asyncio.run(execute())
        except OSError as exc:
            if not fail or str(exc) != "injected registration failure":
                raise
            connection.send("rolled_back")
        else:
            assert not fail
            connection.send("done")
    connection.close()


def _start(ctx, base, operation, **kwargs):
    parent, child = ctx.Pipe()
    process = ctx.Process(
        target=_process_job,
        args=(str(base), str(registry._db_path()), operation, child),
        kwargs=kwargs,
    )
    process.start()
    child.close()
    _receive(parent, "ready")
    parent.send("go")
    return process, parent


def _cleanup(workers):
    for process, connection in workers:
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
        connection.close()
    assert all(process.exitcode == 0 for process, _ in workers)


def _seed(base, *, folder=False):
    raw = base / "kb/raw"
    raw.mkdir(parents=True)
    path = raw / "part/doc.md" if folder else raw / "doc.md"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"original")
    record = registry.register_kb(
        base_dir=base, name="kb", kind="uploaded", created_by="owner",
    )
    [first] = registry.refresh_documents(base, "kb")
    return raw, record, first


@pytest.mark.parametrize("operation", ["move", "folder_move", "delete", "upload-B"])
@pytest.mark.parametrize("refresh", ["refresh", "sync_refresh"])
def test_process_refresh_snapshot_excludes_raw_mutations(tmp_path, operation, refresh):
    raw, record, first = _seed(tmp_path, folder=operation == "folder_move")
    ctx = multiprocessing.get_context("spawn")
    workers = []
    try:
        workers.append(_start(ctx, tmp_path, refresh, pause=True))
        _receive(workers[0][1], "paused")
        workers.append(_start(ctx, tmp_path, operation))
        _receive(workers[1][1], "busy")
        assert registry.list_documents(record.ref) == [first]
        assert (raw / first.rel_path).read_bytes() == b"original"
        with pytest.raises(write_lock.WriteOwnershipBusy):
            with write_lock.write_ownership(raw.parent):
                pytest.fail("Native writer bypassed the document owner in another process")
        with sqlite3.connect(registry._db_path(), timeout=0) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
        other_raw, other_record, _ = _seed(tmp_path / "unrelated")
        assert registry.list_documents(other_record.ref)[0].content_hash == (
            registry.document_hashes(other_raw)["doc.md"]
        )
        workers[0][1].send("release")
        for _, connection in workers:
            _receive(connection, "done")
    finally:
        _cleanup(workers)
    [current] = registry.list_documents(record.ref, include_deleted=True)
    assert current.doc_id == first.doc_id
    assert current.version == (2 if operation == "upload-B" else 1)
    assert current.deleted == (operation == "delete")
    assert current.rel_path == (
        f"archive/{first.rel_path}" if "move" in operation else first.rel_path
    )
    assert {doc.rel_path: doc.content_hash for doc in registry.list_documents(record.ref)} == (
        registry.document_hashes(raw)
    )


@pytest.mark.parametrize("failure", [None, "first", "second"])
def test_process_upload_compensation_cannot_restore_over_newer_success(tmp_path, failure):
    raw, record, first = _seed(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    workers = []
    try:
        workers.append(_start(ctx, tmp_path, "upload-A", pause=True, fail=failure == "first"))
        _receive(workers[0][1], "paused")
        workers.append(_start(ctx, tmp_path, "upload-B", fail=failure == "second"))
        _receive(workers[1][1], "busy")
        assert (raw / "doc.md").read_bytes() == b"upload-A"
        assert registry.list_documents(record.ref) == [first]
        workers[0][1].send("release")
        for index, (_, connection) in enumerate(workers):
            _receive(connection, "rolled_back" if failure == ("first", "second")[index] else "done")
    finally:
        _cleanup(workers)
    assert (raw / "doc.md").read_bytes() == (
        b"upload-A" if failure == "second" else b"upload-B"
    )
    [current] = registry.list_documents(record.ref)
    assert current.doc_id == first.doc_id
    assert current.version == (3 if failure is None else 2)
    assert current.content_hash == registry.document_hashes(raw)["doc.md"]


@pytest.mark.parametrize("operation", ["move", "delete"])
def test_process_failed_local_commit_retains_ownership_through_filesystem_restore(
    tmp_path, operation,
):
    raw, record, first = _seed(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    workers = []
    try:
        workers.append(_start(ctx, tmp_path, operation, fail=True))
        _receive(workers[0][1], "paused")
        # COMMIT has failed and SQLite has rolled back; filesystem compensation is pending.
        assert not (raw / "doc.md").exists()
        assert registry.list_documents(record.ref) == [first]
        with sqlite3.connect(registry._db_path(), timeout=0) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.rollback()
        workers.append(_start(ctx, tmp_path, "upload-B"))
        _receive(workers[1][1], "busy")
        workers[0][1].send("release")
        _receive(workers[0][1], "rolled_back")
        _receive(workers[1][1], "done")
    finally:
        _cleanup(workers)
    assert (raw / "doc.md").read_bytes() == b"upload-B"
    assert not (raw / "archive/doc.md").exists()
    [current] = registry.list_documents(record.ref)
    assert current.doc_id == first.doc_id and current.version == 2
    assert current.content_hash == registry.document_hashes(raw)["doc.md"]


@pytest.mark.parametrize("native", ["native_initialize", "native_bootstrap"])
@pytest.mark.parametrize("operation", ["refresh", "upload-B", "move", "delete"])
def test_process_native_publication_retains_ownership_after_nested_refresh(
    tmp_path, native, operation,
):
    from deeptutor.services.rag.pipelines.lightrag import storage

    raw, record, first = _seed(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    workers = []
    try:
        workers.append(_start(ctx, tmp_path, native))
        _receive(workers[0][1], "paused")
        workers.append(_start(ctx, tmp_path, operation))
        _receive(workers[1][1], "busy")
        assert storage.latest_published_root(raw.parent) is None
        assert registry.list_documents(record.ref) == [first]
        assert (raw / "doc.md").read_bytes() == b"original"
        workers[0][1].send("release")
        for _, connection in workers:
            _receive(connection, "done")
    finally:
        _cleanup(workers)
    assert storage.latest_published_root(raw.parent) is not None
    [current] = registry.list_documents(record.ref, include_deleted=True)
    assert current.doc_id == first.doc_id
    assert current.deleted == (operation == "delete")
    assert {doc.rel_path: doc.content_hash for doc in registry.list_documents(record.ref)} == (
        registry.document_hashes(raw)
    )


@pytest.mark.asyncio
async def test_native_owner_bypasses_waiting_local_admission_without_inheriting_to_child(
    tmp_path, monkeypatch,
):
    """The local waiter must not create a file-lock/local-lock inversion for native refresh."""
    _seed(tmp_path)
    busy = asyncio.Event()
    original = document_mutation.write_ownership

    @contextmanager
    def observed(path):
        try:
            with original(path):
                yield
        except write_lock.WriteOwnershipBusy:
            busy.set()
            raise

    monkeypatch.setattr(document_mutation, "write_ownership", observed)
    called = threading.Event()
    with write_lock.write_ownership(tmp_path / "kb"):
        assert write_lock.owns_write_ownership(tmp_path / "kb")
        task = asyncio.create_task(document_mutation.run_document_worker(
            tmp_path, "kb", called.set,
        ))
        try:
            await asyncio.wait_for(busy.wait(), 2)
            assert not called.is_set()
            assert not await asyncio.to_thread(write_lock.owns_write_ownership, tmp_path / "kb")
            async with asyncio.timeout(2):
                await document_mutation.run_document_worker(
                    tmp_path, "kb", registry.refresh_documents, tmp_path, "kb",
                )
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert not called.is_set()
    await document_mutation.run_document_worker(tmp_path, "kb", called.set)
    assert called.is_set()


@pytest.mark.asyncio
async def test_process_waiting_admission_leaves_tiny_executor_available(tmp_path):
    _seed(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    workers = []
    loop = asyncio.get_running_loop()
    await asyncio.to_thread(lambda: None)
    previous = loop._default_executor
    executor = ThreadPoolExecutor(max_workers=1)
    tasks = []
    try:
        workers.append(_start(ctx, tmp_path, "refresh", pause=True))
        _receive(workers[0][1], "paused")
        loop.set_default_executor(executor)
        for _ in range(6):
            tasks.append(asyncio.create_task(document_mutation.run_document_worker(
                tmp_path, "kb", registry.refresh_documents, tmp_path, "kb",
            )))
        await asyncio.sleep(0)
        assert await asyncio.wait_for(asyncio.to_thread(lambda: "free"), 2) == "free"
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        workers[0][1].send("release")
        _receive(workers[0][1], "done")
        await asyncio.wait_for(asyncio.gather(*tasks[1:]), 5)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        loop.set_default_executor(previous)
        executor.shutdown()
        _cleanup(workers)


@pytest.mark.asyncio
async def test_process_contender_waits_for_cancelled_upload_rollback(tmp_path, monkeypatch):
    from deeptutor.api.routers import knowledge

    raw, record, first = _seed(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original_replace = os.replace

    def fail_record(*args):
        raise OSError("injected registration failure")

    def restore(source, target):
        if Path(target) == raw / "doc.md" and Path(source).name == "0":
            entered.set()
            assert release.wait(15)
        return original_replace(source, target)

    monkeypatch.setattr(registry, "upsert_documents", fail_record)
    monkeypatch.setattr(os, "replace", restore)
    upload = UploadFile(filename="doc.md", file=BytesIO(b"failed-upload"))
    task = asyncio.create_task(knowledge._save_uploaded_files_off_loop(
        [upload], raw, registry_name="kb",
    ))
    workers = []
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        workers.append(_start(multiprocessing.get_context("spawn"), tmp_path, "upload-B"))
        _receive(workers[0][1], "busy")
        for _ in range(2):
            task.cancel()
            await asyncio.sleep(0)
        assert not task.done()
        assert not (raw / "doc.md").exists()
        assert registry.list_documents(record.ref) == [first]
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        _receive(workers[0][1], "done")
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await upload.close()
        _cleanup(workers)
    assert (raw / "doc.md").read_bytes() == b"upload-B"
    [current] = registry.list_documents(record.ref)
    assert current.doc_id == first.doc_id and current.version == 2
    assert current.content_hash == registry.document_hashes(raw)["doc.md"]


@pytest.mark.asyncio
async def test_invalid_lock_location_fails_instead_of_polling(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / ".lightrag-locks").symlink_to(real, target_is_directory=True)
    with pytest.raises(write_lock.IndexingPolicyError, match="symbolic link"):
        async with asyncio.timeout(2):
            await document_mutation.run_document_worker(tmp_path, "kb", lambda: None)
    # Failure also releases the process-local admission lock.
    lock = document_mutation._lock(tmp_path, "kb")
    assert lock.acquire(blocking=False)
    lock.release()
