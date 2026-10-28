"""B3-17: hardening of B1-3's file-fingerprint caches."""

from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
from types import SimpleNamespace

import pytest

from deeptutor.knowledge import file_snapshot, registry, registry_scope
from deeptutor.knowledge.file_snapshot import RACY_WINDOW_NS, FileSnapshotCache
from deeptutor.multi_user.knowledge_access import current_kb_manager
from deeptutor.services.workspace import ContentWorkspaceService
from deeptutor.services.workspace.context import workspace_context


def _clock(monkeypatch, now_ns: int) -> None:
    monkeypatch.setattr(file_snapshot, "time", SimpleNamespace(time_ns=lambda: now_ns))


def _age(path: Path) -> None:
    old = time.time() - 3600
    os.utime(path, (old, old))


def _far_future(monkeypatch) -> None:
    """Every file looks long settled, so only the fingerprint decides."""
    _clock(monkeypatch, time.time_ns() + 3600 * 10**9)


# ── 1. racy check uses max(mtime, ctime) ──


def test_recent_ctime_with_old_mtime_is_not_cached(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text("{}", encoding="utf-8")
    _age(path)  # mtime an hour ago; utime itself just set ctime to now.
    info = path.stat()
    assert info.st_mtime_ns < info.st_ctime_ns - RACY_WINDOW_NS
    _clock(monkeypatch, info.st_ctime_ns + RACY_WINDOW_NS // 2)
    cache = FileSnapshotCache()
    assert cache.load(path, lambda p: json.loads(p.read_text())) == {}
    assert str(path) not in cache._entries


def test_settled_file_is_still_cached(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text("{}", encoding="utf-8")
    info = path.stat()
    _clock(monkeypatch, max(info.st_mtime_ns, info.st_ctime_ns) + 2 * RACY_WINDOW_NS)
    cache = FileSnapshotCache()
    cache.load(path, lambda p: json.loads(p.read_text()))
    assert str(path) in cache._entries


# ── 2. a WAL sidecar disables the cache ──


def _workspace_kb(as_user, make_kb, owner="teacher"):
    with as_user(owner):
        service = ContentWorkspaceService()
        workspace = service.create_workspace("Course")["workspace_id"]
        with workspace_context(workspace):
            manager = current_kb_manager()
            make_kb(manager, "lectures")
        catalog = service._catalog_file()
    return workspace, manager, catalog


def test_archive_committed_into_a_wal_sidecar_is_seen(as_user, make_kb, monkeypatch):
    workspace, manager, catalog = _workspace_kb(as_user, make_kb)
    root = str(Path(manager.base_dir).resolve())
    writer = sqlite3.connect(catalog, isolation_level=None)
    with closing(writer):
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        _age(catalog)
        _far_future(monkeypatch)
        assert root not in registry_scope.archived_source_roots()
        before = catalog.stat()

        (payload,) = writer.execute(
            "SELECT payload FROM workspaces WHERE id = ?", (workspace,)
        ).fetchone()
        row = json.loads(payload)
        row["archived"] = True
        writer.execute(
            "UPDATE workspaces SET payload = ? WHERE id = ?", (json.dumps(row), workspace)
        )
        # The commit lives only in the -wal file; the database file is untouched.
        assert Path(f"{catalog}-wal").stat().st_size > 0
        after = catalog.stat()
        assert (after.st_size, after.st_mtime_ns, after.st_ctime_ns) == (
            before.st_size, before.st_mtime_ns, before.st_ctime_ns,
        )
        assert root in registry_scope.archived_source_roots()


def test_load_without_sidecar_keeps_caching(tmp_path, monkeypatch):
    path = tmp_path / "workspaces.sqlite3"
    path.write_text("x", encoding="utf-8")
    _far_future(monkeypatch)
    cache = FileSnapshotCache()
    calls = []

    def loader(p):
        calls.append(p)
        return 1

    cache.load(path, loader, sidecars=("-wal",))
    cache.load(path, loader, sidecars=("-wal",))
    assert len(calls) == 1
    Path(f"{path}-wal").write_text("w", encoding="utf-8")
    cache.load(path, loader, sidecars=("-wal",))
    cache.load(path, loader, sidecars=("-wal",))
    assert len(calls) == 3 and str(path) not in cache._entries


# ── 3. bounded entries ──


def test_catalogs_of_removed_accounts_are_dropped(as_user, mu_isolated_root, monkeypatch):
    from deeptutor.multi_user.paths import USERS_ROOT

    catalogs = {}
    for user in ("s1", "s2"):
        with as_user(user):
            service = ContentWorkspaceService()
            service.create_workspace("Notes")
            catalogs[user] = service._catalog_file()
    _far_future(monkeypatch)
    registry_scope.archived_source_roots()
    entries = registry_scope._CATALOG_LOCATIONS._entries
    assert {str(catalogs["s1"]), str(catalogs["s2"])} <= set(entries)

    shutil.rmtree(USERS_ROOT / "s2")
    registry_scope.archived_source_roots()
    assert str(catalogs["s1"]) in entries
    assert str(catalogs["s2"]) not in entries


@pytest.mark.asyncio
async def test_deleting_a_kb_drops_its_cached_config_payload(as_user, make_kb, monkeypatch):
    from deeptutor.api.routers import knowledge

    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        make_kb(manager, "physics")
        record = registry.register_kb(
            base_dir=manager.base_dir, name="physics", kind="uploaded", created_by="t1"
        )
        key = str(Path(record.base_dir) / "kb_config.json")
        _far_future(monkeypatch)
        assert registry.config_kind(record) == registry.UPLOADED_KIND
        assert key in registry._CONFIG_PAYLOADS._entries
        await knowledge.delete_knowledge_base(record.ref)
    assert key not in registry._CONFIG_PAYLOADS._entries
