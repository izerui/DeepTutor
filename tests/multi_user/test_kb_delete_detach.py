"""Whole-KB deletion keeps slow file removal outside the registry writer and the loop."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import contextvars
from pathlib import Path
import shutil
import threading

from fastapi import HTTPException
import pytest

from deeptutor.knowledge import manager as manager_module
from deeptutor.knowledge import registry
from deeptutor.knowledge.document_mutation import document_mutation
from deeptutor.multi_user.knowledge_access import current_kb_manager


@pytest.fixture
def knowledge():
    from deeptutor.api.routers import knowledge as module

    return module


def _kb(make_kb, name, owner="t1"):
    manager = current_kb_manager()
    folder = make_kb(manager, name)
    (folder / "version-1").mkdir()
    (folder / "version-1" / "docstore.json").write_text("{}", encoding="utf-8")
    record = registry.register_kb(
        base_dir=manager.base_dir, name=name, kind="uploaded", created_by=owner
    )
    return manager, folder, record


def _staged(base_dir: Path) -> list[Path]:
    return sorted(Path(base_dir).glob(".kb-deleting-*"))


@pytest.fixture
def slow_rmtree(monkeypatch):
    """Block the physical removal of a KB folder until the test releases it."""
    real = shutil.rmtree
    entered = threading.Event()
    release = threading.Event()
    released = []

    def rmtree(path, *args, **kwargs):
        name = Path(path).name
        if name == "physics" or name.startswith(".kb-deleting-"):
            entered.set()
            # False means nobody could run while the removal was blocking.
            released.append(release.wait(5))
        return real(path, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(manager_module.shutil, "rmtree", rmtree)
        try:
            yield entered, release, released
        finally:
            release.set()


@pytest.mark.asyncio
async def test_registry_writes_proceed_while_the_folder_is_removed(
    knowledge, as_user, make_kb, slow_rmtree
):
    entered, release, _ = slow_rmtree
    with as_user("t1", role="teacher"):
        _, folder, record = _kb(make_kb, "physics")
        _, _, other = _kb(make_kb, "chemistry")
        context = contextvars.copy_context()
    with ThreadPoolExecutor(max_workers=2) as pool:
        deletion = pool.submit(context.run, knowledge._delete_kb, record.ref)
        try:
            assert entered.wait(5)
            writer = pool.submit(registry.set_visibility, other.kb_id, "public")
            # The registry writer was committed before the removal started.
            assert writer.result(timeout=2).visibility == "public"
            assert registry.get_kb(record.kb_id) is None
            assert not folder.exists()
        finally:
            release.set()
        deletion.result(timeout=10)
    assert _staged(folder.parent) == []


@pytest.mark.asyncio
async def test_delete_route_keeps_the_event_loop_responsive(
    knowledge, as_user, make_kb, slow_rmtree
):
    entered, release, released = slow_rmtree
    ticks = 0

    async def ticker():
        nonlocal ticks
        while not entered.is_set():
            await asyncio.sleep(0.01)
        for _ in range(5):
            ticks += 1
            await asyncio.sleep(0.01)
        release.set()

    with as_user("t1", role="teacher"):
        _, folder, record = _kb(make_kb, "physics")
        ticking = asyncio.create_task(ticker())
        body = await asyncio.wait_for(knowledge.delete_knowledge_base(record.ref), 10)
        await asyncio.wait_for(ticking, 5)
    assert ticks == 5 and released == [True]
    assert "deleted" in body["message"]
    assert registry.get_kb(record.kb_id) is None
    assert not folder.exists() and _staged(folder.parent) == []


@pytest.mark.asyncio
async def test_registry_failure_after_detach_restores_the_folder(
    knowledge, as_user, make_kb, monkeypatch
):
    with as_user("t1", role="teacher"):
        manager, folder, record = _kb(make_kb, "physics")

        def fail(*_args, **_kwargs):
            # The detached folder must already be gone from its name here.
            assert not folder.exists()
            raise RuntimeError("registry commit failed")

        monkeypatch.setattr(registry, "_revoke", fail)
        with pytest.raises(HTTPException) as error:
            knowledge._delete_kb(record.ref)
        assert error.value.status_code == 500
        config = manager._load_config()
    assert (folder / "raw" / "doc.md").read_text(encoding="utf-8") == "hello"
    assert (folder / "version-1" / "docstore.json").exists()
    assert config["knowledge_bases"]["physics"]["description"] == "physics"
    assert registry.get_kb(record.kb_id) is not None
    assert _staged(folder.parent) == []


@pytest.mark.asyncio
async def test_failed_rename_changes_nothing(knowledge, as_user, make_kb, monkeypatch):
    with as_user("t1", role="teacher"):
        manager, folder, record = _kb(make_kb, "physics")

        def refuse(*_args, **_kwargs):
            raise PermissionError("in use")

        with monkeypatch.context() as scoped:
            scoped.setattr(manager_module.os, "rename", refuse)
            with pytest.raises(HTTPException) as error:
                knowledge._delete_kb(record.ref)
        assert error.value.status_code == 500
        assert "physics" in manager._load_config()["knowledge_bases"]
    assert (folder / "raw" / "doc.md").exists()
    assert registry.get_kb(record.kb_id) is not None
    assert _staged(folder.parent) == []


@pytest.mark.asyncio
async def test_delete_waits_for_document_write_ownership(knowledge, as_user, make_kb):
    with as_user("t1", role="teacher"):
        manager, folder, record = _kb(make_kb, "physics")
        held = threading.Event()
        release = threading.Event()

        def upload():
            with document_mutation(manager.base_dir, "physics"):
                held.set()
                release.wait(5)

        uploader = threading.Thread(target=upload)
        uploader.start()
        try:
            assert held.wait(5)
            deletion = asyncio.create_task(knowledge.delete_knowledge_base(record.ref))
            await asyncio.sleep(0.3)
            assert not deletion.done()
            assert (folder / "raw" / "doc.md").exists()
            assert registry.get_kb(record.kb_id) is not None
        finally:
            release.set()
            uploader.join(5)
        await asyncio.wait_for(deletion, 10)
    assert not folder.exists()
    assert registry.get_kb(record.kb_id) is None


@pytest.mark.asyncio
async def test_interrupted_deletions_are_reported(knowledge, as_user, make_kb):
    with as_user("t1", role="teacher"):
        manager, _, record = _kb(make_kb, "physics")
        leftover = Path(manager.base_dir) / ".kb-deleting-crashed"
        (leftover / "raw").mkdir(parents=True)
        assert manager.interrupted_deletions() == [leftover]
        knowledge._delete_kb(record.ref)
        # Never removed automatically: an uncommitted detach may still be recoverable.
        assert manager.interrupted_deletions() == [leftover]
        assert "physics" not in manager.list_knowledge_bases()
