"""B14 review regressions: stable jobs, administrator controls, archive isolation."""

import asyncio
import json
import sqlite3
from unittest.mock import AsyncMock

from fastapi import HTTPException
import pytest

from deeptutor.knowledge import registry
from deeptutor.knowledge.manager import KnowledgeBaseManager
from deeptutor.multi_user.context import get_current_user
from deeptutor.multi_user.knowledge_access import current_kb_manager, resolve_kb
from deeptutor.services.web_source.repository import SQLiteWebSourceSyncRepository
from deeptutor.services.web_source.scheduler import WebSourceSyncScheduler
from deeptutor.services.web_source.sync import WebSyncResult
from deeptutor.services.workspace import ContentWorkspaceService
from deeptutor.services.workspace.context import workspace_context


def _create(as_user, make_kb, *, owner="u1", adopted=None):
    from deeptutor.multi_user.paths import get_admin_path_service

    with as_user(owner):
        if adopted:
            manager = KnowledgeBaseManager(base_dir=str(
                get_admin_path_service().workspace_root
                / "partners" / adopted / "workspace/knowledge_bases"
            ))
        else:
            workspace = ContentWorkspaceService().create_workspace("Research")["workspace_id"]
            with workspace_context(workspace):
                manager = current_kb_manager()
        make_kb(manager, "papers")
        record = registry.register_kb(
            base_dir=manager.base_dir, name="papers", kind="uploaded",
            created_by=f"partner_{adopted}" if adopted else owner,
        )
        if adopted:
            record = registry.adopt_partner_copy(record.ref, partner_id=adopted, owner_id=owner)
        source = manager.add_web_source("papers", "https://example.com/docs/")
    return manager, record, source


def _pairing(label):
    return {
        "pairing_id": label, "source_url": f"https://example.com/{label}",
        "target_url": f"https://example.com/zh/{label}",
    }


def _legacy_source(manager, source):
    """Reproduce source metadata written before registry-native job identities."""
    path = manager.base_dir / "papers" / "metadata.json"
    payload = json.loads(path.read_text())
    for item in payload["web_sources"]:
        if item["id"] == source["id"]:
            item.pop("sync_state_version", None)
    path.write_text(json.dumps(payload), encoding="utf-8")
    source.pop("sync_state_version", None)


@pytest.mark.asyncio
@pytest.mark.parametrize("adopted", [False, True])
async def test_independent_jobs_controls_pairings_and_legacy_state(
        as_user, make_kb, tmp_path, monkeypatch, adopted,
):
    from deeptutor.api.routers import knowledge

    first, one, source = _create(as_user, make_kb, adopted="p1" if adopted else None)
    second, two, other = _create(as_user, make_kb, adopted="p2" if adopted else None)
    _legacy_source(first, source)
    _legacy_source(second, other)
    assert source["id"] == other["id"]
    repo = SQLiteWebSourceSyncRepository(tmp_path / "jobs.sqlite")
    scheduler = WebSourceSyncScheduler(repository=repo)
    monkeypatch.setattr(knowledge, "get_web_source_sync_scheduler", lambda: scheduler)
    legacy = ("u1", "papers", source["id"])
    old = repo.ensure_source(legacy)
    claimed = repo.claim(old, runner_id="legacy", lease_until_ms=repo._now_ms() + 60000)
    repo.mark_failure(claimed, error="saved error", next_run_at_ms=987654321)
    before = repo.get(legacy)
    repo.record_pairings(*legacy, [_pairing("legacy")])
    first.update_web_source_schedule(
        "papers", source["id"], auto_sync_enabled=True, sync_interval_hours=6,
    )
    second.update_web_source_schedule(
        "papers", source["id"], auto_sync_enabled=True, sync_interval_hours=48,
    )

    await scheduler._synchronize_sources()
    keys = [("u1", record.ref, source["id"]) for record in (one, two)]
    for key in keys:
        job = repo.get(key)
        assert (job.state, job.attempt, job.error, job.next_run_at_ms) == (
            before.state, before.attempt, before.error, before.next_run_at_ms,
        )
        assert repo.list_pairings(*key)[0].pairing_id == "legacy"
    assert repo.get(legacy) == before

    seen = []

    async def sync(*, kb_name, base_dir, **kwargs):
        seen.append((kb_name, base_dir, get_current_user().id))
        return WebSyncResult(ok=True)

    monkeypatch.setattr("deeptutor.services.web_source.sync.sync_source", sync)
    for key in keys:
        await scheduler._run_job(repo.get(key))
    assert set(seen) == {
        ("papers", str(first.base_dir), "u1"), ("papers", str(second.base_dir), "u1"),
    }
    assert repo.get(keys[1]).next_run_at_ms - repo.get(keys[0]).next_run_at_ms > 40 * 3600000
    repo.record_pairings(*keys[0], [_pairing("first")])
    repo.record_pairings(*keys[1], [_pairing("second")])
    with as_user("u1"):
        assert (await knowledge.get_web_source_pairings(one.ref, source["id"]))[0].pairing_id == "first"
        assert (await knowledge.get_web_source_pairings(two.ref, source["id"]))[0].pairing_id == "second"
        await knowledge.cancel_web_source_sync(one.ref, source["id"])
        assert repo.get(keys[0]).state == "cancelled"
        assert repo.get(keys[1]).state == "pending"
        await knowledge.retry_web_source_sync(one.ref, source["id"])
        await knowledge.update_web_source_schedule(
            one.ref, source["id"],
            knowledge.WebSourceScheduleUpdate(auto_sync_enabled=False, sync_interval_hours=6),
        )
        assert repo.get(keys[0]) is None
        assert repo.get(keys[1]) is not None
        await knowledge.remove_web_source(one.ref, source["id"])
        assert repo.list_pairings(*keys[0]) == []
        assert repo.list_pairings(*keys[1])[0].pairing_id == "second"
        await knowledge.add_web_source(
            one.ref, knowledge.AddWebSourceRequest(url="https://example.com/docs/"),
        )
        assert repo.get(keys[0]).attempt == 0
        assert repo.get(keys[0]).error is None
        assert repo.list_pairings(*keys[0]) == []
    # Restarts do not recopy stale legacy errors or pairings.
    restarted = WebSourceSyncScheduler(repository=SQLiteWebSourceSyncRepository(repo.path))
    await restarted._synchronize_sources()
    assert repo.get(keys[0]).error is None
    assert repo.list_pairings(*keys[0]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["u1", "local-admin", "transferred"])
async def test_noncreator_admin_routes_address_authoritative_job(
        as_user, make_kb, tmp_path, monkeypatch, seed_user, owner,
):
    from deeptutor.api.routers import knowledge

    actor = seed_user("operator", role="admin")
    manager, record, source = _create(as_user, make_kb)
    if owner == "local-admin":
        registry.add_reference(record.ref, referrer_kind="partner", referrer_id="p", created_by="u1")
        registry.transfer_ownership("u1", "local-admin")
    elif owner == "transferred":
        recipient = seed_user("recipient", role="admin")
        registry.add_reference(record.ref, referrer_kind="partner", referrer_id="p", created_by="u1")
        registry.transfer_ownership("u1", recipient["id"])
    record = registry.get_kb(record.ref)
    scheduler = WebSourceSyncScheduler(
        repository=SQLiteWebSourceSyncRepository(tmp_path / "jobs.sqlite"),
    )
    monkeypatch.setattr(knowledge, "get_web_source_sync_scheduler", lambda: scheduler)
    if owner != "local-admin":
        # spec 2026-10-29 D2: another account's private KB is not the admin's to manage.
        with as_user(actor["id"], role="admin"):
            with pytest.raises(HTTPException) as hidden:
                await knowledge.add_web_source(
                    record.ref, knowledge.AddWebSourceRequest(url="https://example.com/docs/"),
                )
            assert hidden.value.status_code == 404
        return
    with as_user(actor["id"], role="admin"):
        info = await knowledge.add_web_source(
            record.ref, knowledge.AddWebSourceRequest(url="https://example.com/docs/"),
        )
        await scheduler._synchronize_sources()
        [job] = await knowledge.get_web_source_sync_jobs(record.ref)
        assert job.owner_id == record.created_by and job.kb_name == "papers"
        key = (record.created_by, record.ref, info.id)
        seen = []

        async def sync(**kwargs):
            seen.append((get_current_user().id, kwargs["base_dir"]))
            return WebSyncResult(ok=True)

        monkeypatch.setattr("deeptutor.services.web_source.sync.sync_source", sync)
        await scheduler._run_job(scheduler.repo.get(key))
        assert seen == [(record.created_by, str(manager.base_dir))]
        scheduler.repo.record_pairings(*key, [_pairing("owned")])
        assert (await knowledge.get_web_source_pairings(record.ref, info.id))[0].pairing_id == "owned"
        await knowledge.cancel_web_source_sync(record.ref, info.id)
        assert scheduler.repo.get(key).state == "cancelled"
        await knowledge.retry_web_source_sync(record.ref, info.id)
        assert scheduler.repo.get(key).state == "pending"
        await knowledge.remove_web_source(record.ref, info.id)
        assert scheduler.repo.get(key) is None
        assert scheduler.repo.list_pairings(*key) == []
    assert scheduler.repo.list_jobs(actor["id"], record.ref) == []


@pytest.mark.asyncio
async def test_stable_job_transfer_preserves_cancelled_state_and_pairings(
        as_user, make_kb, tmp_path, seed_user,
):
    _, record, source = _create(as_user, make_kb)
    admin = seed_user("boss", role="admin")
    registry.add_reference(record.ref, referrer_kind="partner", referrer_id="p", created_by="u1")
    repo = SQLiteWebSourceSyncRepository(tmp_path / "jobs.sqlite")
    scheduler = WebSourceSyncScheduler(repository=repo)
    await scheduler._synchronize_sources()
    old_key = ("u1", record.ref, source["id"])
    repo.request_cancel(old_key)
    repo.record_pairings(*old_key, [_pairing("before-transfer")])
    registry.transfer_ownership("u1", admin["id"])
    await scheduler._synchronize_sources()
    new_key = (admin["id"], record.ref, source["id"])
    assert repo.get(new_key).state == "cancelled"
    assert repo.list_pairings(*new_key)[0].pairing_id == "before-transfer"
    repo.retry(new_key)
    repo.record_pairings(*new_key, [_pairing("after-transfer")])
    registry.transfer_ownership(admin["id"], "u1")
    await scheduler._synchronize_sources()
    assert repo.get(old_key).state == "pending"
    assert repo.list_pairings(*old_key)[0].pairing_id == "after-transfer"


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["sqlite", "schema", "json", "unreadable"])
async def test_catalog_failure_does_not_revoke_reads_or_freeze_known_sources(
        as_user, make_kb, tmp_path, monkeypatch, damage,
):
    from deeptutor.knowledge import registry_scope

    manager, record, _ = _create(as_user, make_kb)
    with as_user("root", role="admin"):
        record = registry.set_visibility(record.ref, "public")
    with as_user("unrelated"):
        service = ContentWorkspaceService()
        service.create_workspace("Broken")
        catalog = service._catalog_file()
    if damage == "sqlite":
        catalog.write_bytes(b"broken SQLite file")
    elif damage == "unreadable":
        original = registry_scope.sqlite3.connect

        def connect(database, *args, **kwargs):
            if str(catalog) in str(database):
                raise sqlite3.OperationalError("injected inaccessible catalog")
            return original(database, *args, **kwargs)

        monkeypatch.setattr(registry_scope.sqlite3, "connect", connect)
    else:
        with sqlite3.connect(catalog) as conn:
            if damage == "schema":
                conn.execute("DROP TABLE workspaces")
            else:
                conn.execute("UPDATE workspaces SET payload='not-json'")
    # A known physical source remains writable and schedulable.
    from deeptutor.publishing.admin import admin_retiring

    # spec 2026-10-29 D10 (P4): the admin's fallback exists only while withdrawing or deleting.
    for uid, role in [("u1", "user"), ("root", "admin")]:
        with as_user(uid, role=role), admin_retiring(role == "admin"):
            assert not resolve_kb(record.ref).read_only
            assert not resolve_kb(record.ref, require_write=True).read_only
    assert record in registry_scope.indexed_records()
    with as_user("reader"):
        assert resolve_kb(record.ref).read_only
    scheduler = WebSourceSyncScheduler(
        repository=SQLiteWebSourceSyncRepository(tmp_path / "jobs.sqlite"),
    )
    await scheduler._synchronize_sources()
    assert scheduler.repo.list_jobs("u1", record.ref)
    # An external location without a resolvable source catalog remains readable,
    # but the failed catalog might own it: writes/scheduling must fail closed.
    external = KnowledgeBaseManager(base_dir=str(tmp_path / "external/knowledge_bases"))
    make_kb(external, "external")
    unresolved = registry.register_kb(
        base_dir=external.base_dir, name="external", kind="uploaded",
        created_by="u1", visibility="public",
    )
    for uid, role in [("u1", "user"), ("root", "admin"), ("reader", "user")]:
        with as_user(uid, role=role):
            assert resolve_kb(unresolved.ref).read_only
            with pytest.raises(HTTPException) as denied:
                resolve_kb(unresolved.ref, require_write=True)
            assert denied.value.status_code == 403
    assert unresolved not in registry_scope.indexed_records()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_owner", ["actor", "local-admin"])
async def test_admin_own_coincident_legacy_job_is_not_imported_into_users_kb(
        as_user, make_kb, tmp_path, seed_user, monkeypatch, legacy_owner,
):
    from deeptutor.api.routers import knowledge

    admin = seed_user("operator", role="admin")
    _, user_record, source = _create(as_user, make_kb)
    with as_user(admin["id"], role="admin"):
        manager = current_kb_manager()
        make_kb(manager, "papers")
        admin_record = registry.register_kb(
            base_dir=manager.base_dir, name="papers", kind="uploaded", created_by=admin["id"],
        )
        admin_source = manager.add_web_source("papers", "https://example.com/docs/")
        _legacy_source(manager, admin_source)
    repo = SQLiteWebSourceSyncRepository(tmp_path / "jobs.sqlite")
    scheduler = WebSourceSyncScheduler(repository=repo)
    monkeypatch.setattr(knowledge, "get_web_source_sync_scheduler", lambda: scheduler)
    legacy = (admin["id"] if legacy_owner == "actor" else "local-admin", "papers", source["id"])
    repo.ensure_source(legacy)
    repo.record_pairings(*legacy, [_pairing("admin-private")])
    await scheduler._synchronize_sources()
    assert repo.list_pairings("u1", user_record.ref, source["id"]) == []
    if legacy_owner == "actor":
        assert repo.list_pairings(admin["id"], admin_record.ref, source["id"])
    # The global legacy identity is ambiguous: retain it, without assigning its
    # private pairing data to either owner's registered KB.
    assert repo.list_pairings(*legacy)[0].pairing_id == "admin-private"
    # spec 2026-10-29 D2: the admin cannot address the user's private KB; its owner can.
    with as_user(admin["id"], role="admin"):
        with pytest.raises(HTTPException) as hidden:
            await knowledge.cancel_web_source_sync(user_record.ref, source["id"])
        assert hidden.value.status_code == 404
    with as_user("u1"):
        await knowledge.cancel_web_source_sync(user_record.ref, source["id"])
    assert repo.get(("u1", user_record.ref, source["id"])).state == "cancelled"
    assert repo.get((admin["id"], admin_record.ref, source["id"])).state == "pending"


@pytest.mark.asyncio
async def test_real_sync_records_pairings_by_registered_identity(
        as_user, make_kb, tmp_path, monkeypatch,
):
    from deeptutor.services.web_source.crawler import CrawlResult, CrawledPage
    from deeptutor.services.web_source.sync import sync_source

    manager, record, source = _create(as_user, make_kb)
    scheduler = WebSourceSyncScheduler(
        repository=SQLiteWebSourceSyncRepository(tmp_path / "jobs.sqlite"),
    )
    monkeypatch.setattr(
        "deeptutor.services.web_source.scheduler.get_web_source_sync_scheduler", lambda: scheduler,
    )
    crawl = CrawlResult(pages=[
        CrawledPage(url="https://example.com/docs/en/intro", title="Intro",
                    markdown="# Intro", content_hash="aaa", language="en"),
        CrawledPage(url="https://example.com/docs/zh/intro", title="Intro",
                    markdown="# Intro zh", content_hash="bbb", language="zh"),
    ])
    monkeypatch.setattr(
        "deeptutor.services.web_source.crawler.crawl_docs_site", AsyncMock(return_value=crawl),
    )
    monkeypatch.setattr(
        "deeptutor.knowledge.add_documents.add_documents", AsyncMock(return_value=2),
    )
    with as_user("operator", role="admin"):
        result = await sync_source("papers", source, base_dir=str(manager.base_dir))
    assert result.ok
    assert scheduler.repo.list_pairings("u1", record.ref, source["id"])
    assert scheduler.repo.list_pairings("operator", "papers", source["id"]) == []
    assert len(registry.list_documents(record.ref)) == 3


@pytest.mark.asyncio
async def test_relevant_catalog_corruption_preserves_reads_jobs_but_blocks_dispatch(
        as_user, make_kb, tmp_path, monkeypatch,
):
    from deeptutor.knowledge.registry_scope import indexed_records

    _, record, source = _create(as_user, make_kb)
    with as_user("root", role="admin"):
        record = registry.set_visibility(record.ref, "public")
    scheduler = WebSourceSyncScheduler(
        repository=SQLiteWebSourceSyncRepository(tmp_path / "jobs.sqlite"),
    )
    await scheduler._synchronize_sources()
    key = ("u1", record.ref, source["id"])
    before = scheduler.repo.get(key)
    with as_user("u1"):
        ContentWorkspaceService()._catalog_file().write_bytes(b"broken")
    for uid, role in [("u1", "user"), ("operator", "admin"), ("reader", "user")]:
        with as_user(uid, role=role):
            assert resolve_kb(record.ref).read_only
            with pytest.raises(HTTPException) as denied:
                resolve_kb(record.ref, require_write=True)
            assert denied.value.status_code == 403
    assert record not in indexed_records()
    sync = AsyncMock()
    monkeypatch.setattr("deeptutor.services.web_source.sync.sync_source", sync)
    await scheduler._run_job(before)
    sync.assert_not_awaited()
    await scheduler._synchronize_sources()
    assert scheduler.repo.get(key) == before
    assert not scheduler._sources
    # An already queued job also fails closed without raising out of the task.
    await scheduler._run_job(before)
    sync.assert_not_awaited()


def test_legacy_binding_upgrade_rolls_back_atomically(as_user, make_kb, tmp_path):
    manager, record, source = _create(as_user, make_kb)
    _legacy_source(manager, source)
    repo = SQLiteWebSourceSyncRepository(tmp_path / "jobs.sqlite")
    legacy = ("u1", "papers", source["id"])
    before = repo.ensure_source(legacy)
    repo.record_pairings(*legacy, [_pairing("preserved")])
    # Simulate the previous schema, then open it through the new repository.
    with sqlite3.connect(repo.path) as conn:
        conn.execute("DROP TABLE web_source_registry_bindings")
    repo = SQLiteWebSourceSyncRepository(repo.path)
    with sqlite3.connect(repo.path) as conn:
        conn.execute(
            "CREATE TRIGGER reject_binding BEFORE INSERT ON web_source_registry_bindings "
            "BEGIN SELECT RAISE(ABORT, 'injected binding failure'); END"
        )
    scheduler = WebSourceSyncScheduler(repository=repo)
    key = ("u1", record.ref, source["id"])
    with pytest.raises(sqlite3.IntegrityError, match="injected binding failure"):
        scheduler.source_scope(manager, "papers")
    assert repo.get(key) is None
    assert repo.list_pairings(*key) == []
    assert repo.get(legacy) == before
    assert repo.list_pairings(*legacy)[0].pairing_id == "preserved"
    with sqlite3.connect(repo.path) as conn:
        conn.execute("DROP TRIGGER reject_binding")
    scheduler.source_scope(manager, "papers")
    assert repo.get(key).next_run_at_ms == before.next_run_at_ms
    assert repo.list_pairings(*key)[0].pairing_id == "preserved"


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_state", ["cancelled", "error"])
@pytest.mark.parametrize("new_kb", [False, True])
@pytest.mark.parametrize("entrypoint", ["route", "manager"])
async def test_new_source_never_imports_retained_legacy_state(
        as_user, make_kb, tmp_path, monkeypatch, legacy_state, new_kb, entrypoint,
):
    from deeptutor.api.routers import knowledge

    old_manager, old_record, old_source = _create(as_user, make_kb)
    _legacy_source(old_manager, old_source)
    # This genuinely old R7 copy is registered/adopted only after the first
    # source migrates, as can happen when an earlier migration is retried.
    late_manager, late_record, late_source = _create(as_user, make_kb, adopted="late")
    _legacy_source(late_manager, late_source)
    registry.unregister_kb(late_record.ref)

    def create_empty():
        workspace = ContentWorkspaceService().create_workspace("New source")["workspace_id"]
        with workspace_context(workspace):
            manager = current_kb_manager()
            make_kb(manager, "papers")
            record = registry.ensure_registered(manager.base_dir, "papers", created_by="u1")
        return manager, record

    with as_user("u1"):
        if not new_kb:
            fresh_manager, fresh_record = create_empty()
    repo = SQLiteWebSourceSyncRepository(tmp_path / "jobs.sqlite")
    scheduler = WebSourceSyncScheduler(repository=repo)
    monkeypatch.setattr(knowledge, "get_web_source_sync_scheduler", lambda: scheduler)
    legacy_key = ("u1", "papers", old_source["id"])
    original = repo.ensure_source(legacy_key)
    if legacy_state == "cancelled":
        repo.request_cancel(legacy_key)
    else:
        claim = repo.claim(original, runner_id="old", lease_until_ms=repo._now_ms() + 60000)
        repo.mark_failure(claim, error="old error", next_run_at_ms=1)
    original = repo.get(legacy_key)
    repo.record_pairings(*legacy_key, [_pairing("old-only")])

    await scheduler._synchronize_sources()
    old_key = ("u1", old_record.ref, old_source["id"])
    assert repo.get(old_key).state == legacy_state
    assert repo.list_pairings(*old_key)[0].pairing_id == "old-only"
    # Changing the migrated old job must not update or consume the recovery row.
    repo.retry(old_key)
    repo.request_cancel(old_key)
    with as_user("u1"):
        if new_kb:
            fresh_manager, fresh_record = create_empty()
        if entrypoint == "route":
            await knowledge.add_web_source(
                fresh_record.ref, knowledge.AddWebSourceRequest(url=old_source["url"]),
            )
        else:
            fresh_manager.add_web_source("papers", old_source["url"])
    await scheduler._synchronize_sources()
    fresh_key = ("u1", fresh_record.ref, old_source["id"])
    fresh = repo.get(fresh_key)
    assert (fresh.state, fresh.attempt, fresh.error, fresh.cancel_requested) == (
        "pending", 0, None, False,
    )
    assert fresh.last_run_at_ms is None
    assert repo.list_pairings(*fresh_key) == []
    assert repo.get(legacy_key) == original
    assert repo.list_pairings(*legacy_key)[0].pairing_id == "old-only"

    late_record = registry.register_kb(
        base_dir=late_manager.base_dir, name="papers", kind="uploaded", created_by="partner_late",
    )
    late_record = registry.adopt_partner_copy(late_record.ref, partner_id="late", owner_id="u1")
    # Idempotent add of an existing legacy source must not stamp it as new.
    late_manager.add_web_source("papers", old_source["url"])
    await scheduler._synchronize_sources()
    late_key = ("u1", late_record.ref, old_source["id"])
    assert repo.get(late_key).state == legacy_state
    assert repo.list_pairings(*late_key)[0].pairing_id == "old-only"
    repo.retry(late_key)
    repo.request_cancel(late_key)

    sync = AsyncMock(return_value=WebSyncResult(ok=True))
    monkeypatch.setattr("deeptutor.services.web_source.sync.sync_source", sync)
    scheduler._start_due_jobs()
    await asyncio.gather(*list(scheduler._run_tasks.values()))
    sync.assert_awaited_once()
    assert sync.await_args.kwargs["base_dir"] == str(fresh_manager.base_dir)
    assert sync.await_args.kwargs["kb_name"] == "papers"
    assert repo.get(old_key).state == "cancelled"
    assert repo.get(late_key).state == "cancelled"
    assert repo.list_pairings(*fresh_key) == []
    assert repo.get(legacy_key) == original
