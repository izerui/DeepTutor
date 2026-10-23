"""Registry-driven scheduling preserves physical scope and per-KB identity."""

from unittest.mock import AsyncMock

from fastapi import HTTPException
import pytest

from deeptutor.knowledge import registry
from deeptutor.multi_user.context import get_current_user
from deeptutor.multi_user.knowledge_access import current_kb_manager, resolve_kb
from deeptutor.services.github_source.sync_service import GitHubSourceSyncService
from deeptutor.services.web_source.repository import SQLiteWebSourceSyncRepository
from deeptutor.services.web_source.scheduler import WebSourceSyncScheduler
from deeptutor.services.web_source.sync import WebSyncResult
from deeptutor.services.workspace import ContentWorkspaceService
from deeptutor.services.workspace.context import workspace_context
from deeptutor.services.workspace.knowledge import qualified_kb_id


def _workspace_kb(as_user, make_kb, name="papers"):
    with as_user("u1"):
        service = ContentWorkspaceService()
        workspace = service.create_workspace("Research")["workspace_id"]
        with workspace_context(workspace):
            manager = current_kb_manager()
            make_kb(manager, name)
            record = registry.register_kb(
                base_dir=manager.base_dir, name=name, kind="uploaded", created_by="u1",
                visibility="public",
            )
            manager.add_web_source(name, "https://example.com/docs/")
            manager.add_github_source(name, "octo/docs")
    return manager, workspace, record


def test_indexed_roots_cover_workspace(as_user, make_kb):
    from deeptutor.knowledge.registry_scope import indexed_roots

    manager, _, _ = _workspace_kb(as_user, make_kb)
    [(root, owner)] = indexed_roots()
    assert root == str(manager.base_dir.resolve())
    assert (owner.id, owner.role) == ("u1", "user")


@pytest.mark.asyncio
async def test_schedulers_keep_each_kb_owner_in_mixed_root(
        as_user, make_kb, tmp_path, monkeypatch, seed_user,
):
    admin = seed_user("boss", role="admin")
    manager, _, _ = _workspace_kb(as_user, make_kb)
    for name, owner_id in [("transferred", admin["id"]), ("orphan", "deleted-user")]:
        make_kb(manager, name)
        registry.register_kb(
            base_dir=manager.base_dir, name=name, kind="uploaded", created_by=owner_id,
        )
        manager.add_web_source(name, "https://example.com/docs/")
        manager.add_github_source(name, "octo/docs")
    # Unregistered catalog entries must not inherit a neighboring KB's owner.
    make_kb(manager, "unregistered")
    manager.add_web_source("unregistered", "https://example.com/docs/")
    manager.add_github_source("unregistered", "octo/docs")
    seen = []

    async def sync(*, kb_name, base_dir, **_kwargs):
        user = get_current_user()
        seen.append((kb_name, base_dir, user.id, user.role))
        return WebSyncResult(ok=True)

    monkeypatch.setattr("deeptutor.services.web_source.sync.sync_source", sync)
    monkeypatch.setattr("deeptutor.services.github_source.sync.sync_source", sync)
    scheduler = WebSourceSyncScheduler(
        repository=SQLiteWebSourceSyncRepository(tmp_path / "jobs.sqlite"),
    )
    expected = {
        ("papers", str(manager.base_dir), "u1", "user"),
        ("transferred", str(manager.base_dir), admin["id"], "admin"),
        ("orphan", str(manager.base_dir), "deleted-user", "user"),
    }
    from deeptutor.knowledge.registry_scope import indexed_roots

    assert {(root, user.id) for root, user in indexed_roots()} == {
        (root, owner) for _, root, owner, _ in expected
    }
    with as_user("ambient"):
        await scheduler._synchronize_sources()
        for name, _, owner, _ in expected:
            [job] = scheduler.repo.list_jobs(owner, name)
            await scheduler._run_job(job)
        assert set(seen) == expected
        assert get_current_user().id == "ambient"
        seen.clear()
        await GitHubSourceSyncService()._sync_one_cycle()
        assert set(seen) == expected
        assert get_current_user().id == "ambient"


@pytest.mark.parametrize("caller", ["u1", "root"])
def test_archived_fixed_ids_and_qualified_writes_are_denied(as_user, make_kb, caller):
    manager, workspace, record = _workspace_kb(as_user, make_kb)
    with as_user("u1"):
        ContentWorkspaceService().update_workspace(workspace, archived=True)
        with pytest.raises(HTTPException):
            resolve_kb(qualified_kb_id("papers", workspace), require_write=True)
    with as_user(caller, role="admin" if caller == "root" else "user"):
        active = ContentWorkspaceService().create_workspace("Active")["workspace_id"]
        for scope in ("", active):
            with workspace_context(scope):
                assert resolve_kb(record.ref).read_only
                with pytest.raises(HTTPException):
                    resolve_kb(record.ref, require_write=True)
    with as_user("reader"):
        assert resolve_kb(record.ref).base_dir == manager.base_dir


@pytest.mark.asyncio
async def test_archive_after_scan_prevents_execution(as_user, make_kb, tmp_path, monkeypatch):
    from deeptutor.knowledge.registry_scope import indexed_roots

    _, workspace, _ = _workspace_kb(as_user, make_kb)
    scheduler = WebSourceSyncScheduler(
        repository=SQLiteWebSourceSyncRepository(tmp_path / "jobs.sqlite"),
    )
    await scheduler._synchronize_sources()
    [job] = scheduler.repo.list_jobs("u1", "papers")
    with as_user("u1"):
        ContentWorkspaceService().update_workspace(workspace, archived=True)
    sync = AsyncMock(return_value=WebSyncResult(ok=True))
    monkeypatch.setattr("deeptutor.services.web_source.sync.sync_source", sync)
    monkeypatch.setattr("deeptutor.services.github_source.sync.sync_source", sync)
    assert indexed_roots() == []
    await scheduler._run_job(job)
    await scheduler._synchronize_sources()
    await GitHubSourceSyncService()._sync_one_cycle()
    sync.assert_not_awaited()
    assert scheduler.repo.list_jobs("u1", "papers") == []


@pytest.mark.asyncio
async def test_retired_copy_is_readable_but_never_scheduled(
        as_user, make_kb, tmp_path, monkeypatch,
):
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    from deeptutor.knowledge.registry_scope import indexed_roots
    from deeptutor.multi_user.paths import get_admin_path_service

    root = (
            get_admin_path_service().workspace_root
            / "partners/p1/workspace/knowledge_bases/.retired/collision"
    )
    manager = KnowledgeBaseManager(base_dir=str(root))
    make_kb(manager, "legacy")
    record = registry.register_kb(
        base_dir=root, name="legacy", kind="uploaded", created_by="u1",
        visibility="public",
    )
    manager.add_web_source("legacy", "https://example.com/docs/")
    manager.add_github_source("legacy", "octo/docs")
    sync = AsyncMock()
    monkeypatch.setattr("deeptutor.services.github_source.sync.sync_source", sync)
    scheduler = WebSourceSyncScheduler(
        repository=SQLiteWebSourceSyncRepository(tmp_path / "jobs.sqlite"),
    )
    assert indexed_roots() == []
    await scheduler._synchronize_sources()
    await GitHubSourceSyncService()._sync_one_cycle()
    assert scheduler._sources == {}
    sync.assert_not_awaited()
    with as_user("reader"):
        assert resolve_kb(record.ref).name == "legacy"


@pytest.mark.asyncio
async def test_transferred_admin_jobs_match_route_lookup(
        as_user, make_kb, tmp_path, monkeypatch, seed_user,
):
    from deeptutor.api.routers import knowledge

    admin = seed_user("boss", role="admin")
    manager, _, record = _workspace_kb(as_user, make_kb)
    registry.transfer_ownership("u1", admin["id"])
    scheduler = WebSourceSyncScheduler(
        repository=SQLiteWebSourceSyncRepository(tmp_path / "jobs.sqlite"),
    )
    monkeypatch.setattr(knowledge, "get_web_source_sync_scheduler", lambda: scheduler)
    with as_user(admin["id"], role="admin"):
        info = await knowledge.add_web_source(
            record.ref, knowledge.AddWebSourceRequest(url="https://example.org/docs/"),
        )
        await scheduler._synchronize_sources()
        jobs = await knowledge.get_web_source_sync_jobs(record.ref)
    assert {job.source_id for job in jobs} == {
        source["id"] for source in manager.get_web_sources("papers")
    }
    assert info.id in {job.source_id for job in jobs}
    assert scheduler.repo.list_jobs("local-admin", "papers") == []


@pytest.mark.asyncio
async def test_archived_transferred_source_keeps_physical_archive_status(
        as_user, make_kb, tmp_path, seed_user,
):
    from deeptutor.knowledge.registry_scope import indexed_roots

    admin = seed_user("boss", role="admin")
    _, workspace, record = _workspace_kb(as_user, make_kb)
    registry.transfer_ownership("u1", admin["id"])
    with as_user("u1"):
        ContentWorkspaceService().update_workspace(workspace, archived=True)
    with as_user(admin["id"], role="admin"):
        assert resolve_kb(record.ref).read_only
        with pytest.raises(HTTPException):
            resolve_kb(record.ref, require_write=True)
    assert indexed_roots() == []
    scheduler = WebSourceSyncScheduler(
        repository=SQLiteWebSourceSyncRepository(tmp_path / "jobs.sqlite"),
    )
    await scheduler._synchronize_sources()
    assert not scheduler._sources


@pytest.mark.asyncio
async def test_same_legacy_job_key_in_two_workspaces_is_not_arbitrarily_routed(
        as_user, make_kb, tmp_path, caplog,
):
    _workspace_kb(as_user, make_kb)
    _workspace_kb(as_user, make_kb)
    scheduler = WebSourceSyncScheduler(
        repository=SQLiteWebSourceSyncRepository(tmp_path / "jobs.sqlite"),
    )
    await scheduler._synchronize_sources()
    assert scheduler._sources == {}
    assert scheduler.repo.list_jobs("u1", "papers") == []
    assert "Ambiguous web source job" in caplog.text


@pytest.mark.asyncio
async def test_one_failed_kb_scan_preserves_owner_jobs_and_other_roots(
        as_user, make_kb, tmp_path, monkeypatch,
):
    from deeptutor.knowledge.manager import KnowledgeBaseManager

    broken, _, _ = _workspace_kb(as_user, make_kb, "broken")
    _workspace_kb(as_user, make_kb, "working")
    scheduler = WebSourceSyncScheduler(
        repository=SQLiteWebSourceSyncRepository(tmp_path / "jobs.sqlite"),
    )
    await scheduler._synchronize_sources()
    [old] = scheduler.repo.list_jobs("u1", "broken")
    original = KnowledgeBaseManager.get_web_sources

    def get_sources(manager, name):
        if manager.base_dir == broken.base_dir:
            raise OSError("unreadable source metadata")
        return original(manager, name)

    monkeypatch.setattr(KnowledgeBaseManager, "get_web_sources", get_sources)
    await scheduler._synchronize_sources()
    assert scheduler.repo.list_jobs("u1", "broken") == [old]
    assert {key[1] for key in scheduler._sources} == {"working"}


def test_registry_scope_filters_connectors_missing_dirs_and_preserves_local_admin(
        as_user, make_kb, tmp_path,
):
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    from deeptutor.knowledge.registry_scope import indexed_records, owner_user
    from deeptutor.multi_user.paths import get_admin_path_service

    with as_user("local-admin", role="admin"):
        manager = current_kb_manager()
        make_kb(manager, "legacy")
        legacy = registry.register_kb(
            base_dir=manager.base_dir, name="legacy", kind="uploaded", created_by="local-admin",
        )
        # A non-admin's physical placement must never grant admin privileges.
        make_kb(manager, "ordinary")
        ordinary = registry.register_kb(
            base_dir=manager.base_dir, name="ordinary", kind="uploaded", created_by="missing",
        )
    missing = registry.register_kb(
        base_dir=tmp_path / "absent", name="gone", kind="uploaded", created_by="u1",
    )
    other = KnowledgeBaseManager(base_dir=str(tmp_path / "connectors"))
    make_kb(other, "remote")
    connector = registry.register_kb(
        base_dir=other.base_dir, name="remote", kind="obsidian", created_by="u1",
    )
    records = indexed_records()
    assert legacy in records and ordinary in records
    assert missing not in records and connector not in records
    assert owner_user(legacy).id == "local-admin"
    assert owner_user(legacy).is_admin
    assert owner_user(ordinary).id == "missing"
    assert not owner_user(ordinary).is_admin
    assert owner_user(ordinary).scope.root != get_admin_path_service().workspace_root


@pytest.mark.asyncio
async def test_registry_sync_respects_disabled_manual_and_fresh_sources(
        as_user, make_kb, tmp_path, monkeypatch,
):
    from datetime import datetime, timezone

    manager, _, _ = _workspace_kb(as_user, make_kb)
    [web] = manager.get_web_sources("papers")
    manager.update_web_source_state("papers", web["id"], auto_sync_enabled=False)
    [github] = manager.get_github_sources("papers")
    manager.update_github_source_state(
        "papers", github["id"], last_synced_at=datetime.now(timezone.utc).isoformat(),
    )
    sync = AsyncMock()
    monkeypatch.setattr("deeptutor.services.github_source.sync.sync_source", sync)
    scheduler = WebSourceSyncScheduler(
        repository=SQLiteWebSourceSyncRepository(tmp_path / "jobs.sqlite"),
    )
    await scheduler._synchronize_sources()
    await GitHubSourceSyncService()._sync_one_cycle()
    assert not scheduler._sources
    sync.assert_not_awaited()
    manager.update_web_source_state("papers", web["id"], auto_sync_enabled=True, enabled=False)
    manager.update_github_source_state("papers", github["id"], last_synced_at="", enabled=False)
    await scheduler._synchronize_sources()
    await GitHubSourceSyncService()._sync_one_cycle()
    assert not scheduler._sources
    sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_running_web_job_keeps_validated_directory_across_inventory_replacement(
        as_user, make_kb, tmp_path, monkeypatch,
):
    manager, _, _ = _workspace_kb(as_user, make_kb)
    scheduler = WebSourceSyncScheduler(
        repository=SQLiteWebSourceSyncRepository(tmp_path / "jobs.sqlite"),
    )
    await scheduler._synchronize_sources()
    [job] = scheduler.repo.list_jobs("u1", "papers")
    original_get = scheduler.repo.get

    def get_after_rescan(key):
        scheduler._source_dirs = {}
        return original_get(key)

    monkeypatch.setattr(scheduler.repo, "get", get_after_rescan)
    sync = AsyncMock(return_value=WebSyncResult(ok=True))
    monkeypatch.setattr("deeptutor.services.web_source.sync.sync_source", sync)
    await scheduler._run_job(job)
    sync.assert_awaited_once()
    assert sync.await_args.kwargs["base_dir"] == str(manager.base_dir)
