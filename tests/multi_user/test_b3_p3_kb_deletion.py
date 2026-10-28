"""B3-18: leftovers of B1-1's detach/purge deletion and connected-KB deletion routes."""

from __future__ import annotations

import json
from pathlib import Path
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from deeptutor.knowledge import manager as manager_module
from deeptutor.knowledge import registry
from deeptutor.knowledge.kb_types import LINKED_KB_TYPE, MARGINNOTE4_KB_TYPE
from deeptutor.multi_user.knowledge_access import current_kb_manager


@pytest.fixture
def knowledge():
    from deeptutor.api.routers import knowledge as module

    return module


def _kb(make_kb, name, owner="t1"):
    manager = current_kb_manager()
    folder = make_kb(manager, name)
    record = registry.register_kb(
        base_dir=manager.base_dir, name=name, kind="uploaded", created_by=owner
    )
    return manager, folder, record


def _leftover(manager, suffix: str, marker: dict | str | None) -> Path:
    path = Path(manager.base_dir) / f"{manager_module.DELETION_STAGING_PREFIX}{suffix}"
    (path / "raw").mkdir(parents=True)
    (path / "raw" / "doc.md").write_text("old", encoding="utf-8")
    if isinstance(marker, dict):
        (path / manager_module.DELETION_MARKER).write_text(json.dumps(marker), encoding="utf-8")
    elif isinstance(marker, str):
        (path / manager_module.DELETION_MARKER).write_text(marker, encoding="utf-8")
    return path


# ── B3-18.1: committed leftovers are removed after the next deletion ──


@pytest.mark.asyncio
async def test_committed_leftovers_are_purged_after_the_next_deletion(
    knowledge, as_user, make_kb, monkeypatch
):
    removed_on: dict[str, str] = {}
    real_rmtree = manager_module.shutil.rmtree

    def rmtree(path, *args, **kwargs):
        removed_on[Path(path).name] = threading.current_thread().name
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(manager_module.shutil, "rmtree", rmtree)
    with as_user("t1", role="teacher"):
        manager, _, physics = _kb(make_kb, "physics")
        _, _, chemistry = _kb(make_kb, "chemistry")
        committed = _leftover(manager, "committed", {"name": "gone", "kb_id": "a" * 32})
        # Controls: never removed automatically.
        still_registered = _leftover(
            manager, "registered", {"name": "old-chem", "kb_id": chemistry.kb_id}
        )
        name_in_config = _leftover(manager, "named", {"name": "chemistry", "kb_id": "b" * 32})
        no_marker = _leftover(manager, "nomarker", None)
        unreadable = _leftover(manager, "unreadable", "{not json")
        no_kb_id = _leftover(manager, "nokbid", {"name": "legacy", "kb_id": None})

        body = await knowledge.delete_knowledge_base(physics.ref)

    assert "deleted" in body["message"]
    assert not committed.exists()
    for kept in (still_registered, name_in_config, no_marker, unreadable, no_kb_id):
        assert (kept / "raw" / "doc.md").exists(), kept.name
    # Removed on a worker thread, never on the event loop's thread.
    assert removed_on[committed.name] != threading.main_thread().name
    assert registry.get_kb(chemistry.kb_id) is not None


def test_sync_deletion_also_purges_committed_leftovers(knowledge, as_user, make_kb):
    with as_user("t1", role="teacher"):
        manager, _, physics = _kb(make_kb, "physics")
        committed = _leftover(manager, "committed", {"name": "gone", "kb_id": "c" * 32})
        knowledge._delete_kb(physics.ref)
    assert not committed.exists()
    assert manager.interrupted_deletions() == []


# ── B3-18.2: a re-registered MarginNote store survives the old purge ──


def test_purge_keeps_a_store_reclaimed_by_a_new_marginnote_library(as_user, tmp_path):
    store = tmp_path / "mn" / "notes.db"
    store.parent.mkdir()
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        manager.register_marginnote4_kb("notes", db_path=str(store))
        store.write_bytes(b"old library")
        detached = manager.detach_knowledge_base("notes")
        # Committed; before the purge another library claims the same name and store.
        manager.register_marginnote4_kb("notes", db_path=str(store))
        store.write_bytes(b"new library")
        detached.purge()
    assert store.read_bytes() == b"new library"


def test_purge_still_removes_an_unclaimed_marginnote_store(as_user, tmp_path):
    store = tmp_path / "mn" / "notes.db"
    store.parent.mkdir()
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        manager.register_marginnote4_kb("notes", db_path=str(store))
        store.write_bytes(b"library")
        manager.detach_knowledge_base("notes").purge()
    assert not store.exists()


# ── B3-18.3: deletion routes for connected KBs ──


def _connect(kind: str, tmp_path: Path):
    manager = current_kb_manager()
    if kind == LINKED_KB_TYPE:
        external = tmp_path / "external-index"
        external.mkdir()
        (external / "index.json").write_text("{}", encoding="utf-8")
        manager.register_linked_kb("shelf", str(external), "llamaindex")
        data = external / "index.json"
    else:
        data = tmp_path / "mn" / "shelf.db"
        data.parent.mkdir()
        manager.register_marginnote4_kb("shelf", db_path=str(data))
        data.write_bytes(b"store")
    record = registry.register_kb(
        base_dir=manager.base_dir, name="shelf", kind=kind, created_by="t1"
    )
    registry.add_reference(
        record.kb_id, referrer_kind="partner", referrer_id="p1", created_by="t1"
    )
    return manager, record, data


@pytest.mark.parametrize("kind", [LINKED_KB_TYPE, MARGINNOTE4_KB_TYPE])
@pytest.mark.parametrize("route", ["path", "body"])
def test_delete_routes_unregister_connected_kbs(knowledge, as_user, tmp_path, kind, route):
    app = FastAPI()
    app.include_router(knowledge.router, prefix="/api")
    with as_user("t1", role="teacher"):
        manager, record, data = _connect(kind, tmp_path)
        with TestClient(app) as client:
            if route == "path":
                response = client.delete(f"/api/knowledge-bases/{record.ref}")
            else:
                response = client.post("/api/knowledge-bases/delete", json={"name": record.ref})
        assert response.status_code == 200, response.text
        assert "shelf" not in manager._load_config()["knowledge_bases"]
    assert registry.get_kb(record.kb_id) is None
    [revoked] = registry.list_unavailable_references("partner", "p1")
    assert (revoked.kb_id, revoked.reason) == (record.kb_id, "kb_deleted")
    if kind == LINKED_KB_TYPE:
        # A linked index is read in place; deleting the pointer never touches it.
        assert data.read_text(encoding="utf-8") == "{}"
    else:
        # A MarginNote library's store is DeepTutor's own data and is removed.
        assert not data.exists()
    assert manager.interrupted_deletions() == []
