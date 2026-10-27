"""Startup backfill registers every existing KB, honors D16 once, and records D19."""

from __future__ import annotations

import json
from pathlib import Path

from deeptutor.knowledge import registry


def _seed_kb(root: Path, name: str, entry: dict | None = None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    if entry is None or "type" not in entry:
        (root / name / "raw").mkdir(parents=True, exist_ok=True)
    config = root / "kb_config.json"
    data = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
    data.setdefault("knowledge_bases", {})[name] = entry or {
        "path": name,
        "rag_provider": "llamaindex",
        "status": "ready",
    }
    config.write_text(json.dumps(data), encoding="utf-8")


def _setup(mu_isolated_root, seed_user):
    from deeptutor.multi_user.grants import save_grant
    from deeptutor.multi_user.paths import get_admin_path_service, scope_for_user

    seed_user("boss", role="admin")
    alice = seed_user("alice")
    admin_root = get_admin_path_service().get_knowledge_bases_root()
    _seed_kb(admin_root, "granted")
    _seed_kb(admin_root, "internal")
    _seed_kb(admin_root, "wiki", {"type": "weknora", "server_url": "http://localhost:8080"})
    alice_root = scope_for_user(alice["id"], is_admin=False).root / "knowledge_bases"
    _seed_kb(alice_root, "notes")
    _seed_kb(alice_root, "vault", {"type": "obsidian", "vault_path": str(mu_isolated_root)})
    save_grant(
        alice["id"],
        {
            "knowledge_bases": [
                {"resource_id": "admin:kb:granted", "name": "granted"},
                {"resource_id": "admin:kb:wiki", "name": "wiki"},
            ]
        },
    )
    return alice, admin_root, alice_root


def test_backfill_registers_every_root_and_publishes_granted_admin_kbs(
    mu_isolated_root, seed_user
):
    from deeptutor.knowledge.registry_backfill import backfill_registry

    alice, admin_root, alice_root = _setup(mu_isolated_root, seed_user)

    report = backfill_registry()

    granted = registry.find_by_location(admin_root, "granted")
    internal = registry.find_by_location(admin_root, "internal")
    wiki = registry.find_by_location(admin_root, "wiki")
    notes = registry.find_by_location(alice_root, "notes")
    vault = registry.find_by_location(alice_root, "vault")
    assert granted.visibility == "public" and granted.created_by == "local-admin"
    assert internal.visibility == "private"
    # D19: a granted connector stays private and is reported instead.
    assert wiki.visibility == "private" and wiki.kind == "weknora"
    assert notes.created_by == alice["id"] and notes.visibility == "private"
    assert notes.kind == "uploaded"
    assert vault.kind == "obsidian" and vault.visibility == "private"
    assert report["admin"] == 3 and report["account"] == 2
    assert report["promoted"] == 1 and report["connectors_disabled"] == 1

    items = {(item.kind, item.key): item.data for item in registry.list_migration_items()}
    assert items[("kb_made_public", granted.kb_id)] == {
        "kb_id": granted.kb_id,
        "name": "granted",
        "users": ["alice"],
    }
    assert items[("connector_grant_disabled", "wiki")] == {
        "name": "wiki",
        "kind": "weknora",
        "users": ["alice"],
    }


def test_backfill_is_idempotent_and_publishes_only_once(mu_isolated_root, seed_user):
    from deeptutor.knowledge.registry_backfill import backfill_registry

    _alice, admin_root, _alice_root = _setup(mu_isolated_root, seed_user)
    backfill_registry()
    first = registry.find_by_location(admin_root, "granted")
    # An admin withdraws it later; the next start must not publish it again.
    registry.set_visibility(first.kb_id, "private")

    report = backfill_registry()

    again = registry.find_by_location(admin_root, "granted")
    assert again.kb_id == first.kb_id
    assert again.visibility == "private"
    assert report["promoted"] == 0


def test_backfill_skips_accounts_without_a_workspace(mu_isolated_root, seed_user):
    from deeptutor.knowledge.registry_backfill import backfill_registry
    from deeptutor.multi_user.paths import scope_for_user

    seed_user("boss", role="admin")
    ghost = seed_user("ghost")
    backfill_registry()
    assert not scope_for_user(ghost["id"], is_admin=False).root.exists()


def test_one_broken_root_does_not_stop_the_backfill(mu_isolated_root, seed_user, monkeypatch):
    from deeptutor.knowledge import registry_backfill

    _alice, admin_root, alice_root = _setup(mu_isolated_root, seed_user)
    real_sync = registry_backfill._sync_root_unguarded

    def flaky(root, *, created_by):
        if root == admin_root:
            raise ValueError("damaged kb_config")
        return real_sync(root, created_by=created_by)

    monkeypatch.setattr(registry_backfill, "_sync_root_unguarded", flaky)

    report = registry_backfill.backfill_registry()

    assert report["admin"] == 0
    assert registry.find_by_location(alice_root, "notes") is not None


def test_startup_backfill_is_off_in_tests_and_on_when_enabled(mu_isolated_root, seed_user, monkeypatch):
    from deeptutor.knowledge import registry_backfill

    _alice, admin_root, _alice_root = _setup(mu_isolated_root, seed_user)
    assert registry_backfill.run_startup_backfill() == {}
    assert registry.find_by_location(admin_root, "granted") is None

    monkeypatch.setattr(registry_backfill, "STARTUP_BACKFILL_ENABLED", True)
    assert registry_backfill.run_startup_backfill()["knowledge_bases"]["admin"] == 3


def test_admin_sync_failure_leaves_d16_key_unset_and_retries(mu_isolated_root, seed_user, monkeypatch):
    """If the admin-root sync fails, the D16 meta key stays unset so the next startup retries."""
    from deeptutor.knowledge import registry_backfill
    from deeptutor.knowledge.registry_backfill import GRANTS_PROMOTED_KEY

    _alice, admin_root, _alice_root = _setup(mu_isolated_root, seed_user)
    real_sync = registry_backfill._sync_root_unguarded
    fail_admin = True

    def flaky(root, *, created_by):
        if fail_admin and root == admin_root:
            raise ValueError("damaged kb_config")
        return real_sync(root, created_by=created_by)

    monkeypatch.setattr(registry_backfill, "_sync_root_unguarded", flaky)

    # First run: admin sync fails -> D16 key must NOT be set.
    report1 = registry_backfill.backfill_registry()
    assert report1["admin"] == 0
    assert report1["promoted"] == 0
    assert registry.get_meta(GRANTS_PROMOTED_KEY) is None
    assert registry.find_by_location(admin_root, "granted") is None

    # Second run: admin sync succeeds -> D16 fires, KB becomes public.
    fail_admin = False
    monkeypatch.setattr(registry_backfill, "_sync_root_unguarded", real_sync)
    report2 = registry_backfill.backfill_registry()
    assert report2["admin"] == 3
    assert report2["promoted"] == 1
    assert registry.get_meta(GRANTS_PROMOTED_KEY) is not None
    granted = registry.find_by_location(admin_root, "granted")
    assert granted is not None and granted.visibility == "public"


def test_missing_dir_kb_skipped_and_does_not_block_promotion(mu_isolated_root, seed_user):
    """A registered admin KB whose directory was hand-deleted is skipped; others promote normally."""
    import shutil

    from deeptutor.knowledge.registry_backfill import GRANTS_PROMOTED_KEY, backfill_registry

    alice, admin_root, _alice_root = _setup(mu_isolated_root, seed_user)

    # Seed a second granted KB and give alice a grant to it.
    _seed_kb(admin_root, "vanished")
    from deeptutor.multi_user.grants import save_grant

    save_grant(
        alice["id"],
        {
            "knowledge_bases": [
                {"resource_id": "admin:kb:granted", "name": "granted"},
                {"resource_id": "admin:kb:wiki", "name": "wiki"},
                {"resource_id": "admin:kb:vanished", "name": "vanished"},
            ]
        },
    )

    # First backfill: registers all three admin KBs, then we delete "vanished" dir.
    # We need the sync to happen first so the KB is in the registry, then delete.
    # Actually, we need the directory to be missing *during freeze_targets*, so
    # delete it after sync_root but before promotion. Simplest: run the full
    # backfill once (which succeeds), reset the promotion key, delete the dir,
    # and run again — the second run re-freezes and hits the missing directory.
    # Better approach: do the registration, then delete the dir, then call
    # backfill_registry which does the D16 dance from scratch.

    # Step 1: Do a first run that will register all KBs AND complete promotion.
    report1 = backfill_registry()
    assert report1["admin"] == 4  # granted, internal, wiki, vanished
    assert registry.get_meta(GRANTS_PROMOTED_KEY) is not None

    # Step 2: Reset state — clear the promotion key and inventory so it re-runs.
    with registry._write() as conn:
        conn.execute("DELETE FROM main.registry_meta WHERE key = ?", (GRANTS_PROMOTED_KEY,))
        conn.execute(
            "DELETE FROM main.registry_meta WHERE key = ?",
            ("grants_inventory_v1",),
        )
        conn.execute("DELETE FROM main.registry_meta WHERE key LIKE 'grants_target_v1:%'")
        conn.execute("DELETE FROM migration_items")
        # Reset visibility of the promoted KB so promotion fires again.
        conn.execute("UPDATE kb_registry SET visibility = 'private'")

    # Step 3: Delete the directory of "vanished" KB (simulates hand-deletion).
    shutil.rmtree(admin_root / "vanished")

    # Step 4: Re-run — "vanished" is registered but directory missing.
    backfill_registry()

    # The normal granted KB should be promoted.
    granted = registry.find_by_location(admin_root, "granted")
    assert granted is not None and granted.visibility == "public"

    # The missing-dir KB must NOT be public (fail-closed).
    vanished = registry.find_by_location(admin_root, "vanished")
    assert vanished is not None and vanished.visibility == "private"

    # Migration must be marked complete (key set), not left pending.
    assert registry.get_meta(GRANTS_PROMOTED_KEY) is not None

    # A skip notice must be recorded.
    items = {(item.kind, item.key): item.data for item in registry.list_migration_items()}
    vanished_skips = [
        (k, key) for (k, key) in items if k == "kb_dir_missing_skipped"
    ]
    assert len(vanished_skips) >= 1

    # Step 5: Second run is a no-op (idempotent).
    report3 = backfill_registry()
    assert report3["promoted"] == 0


def test_a_failing_startup_step_does_not_stop_the_next(mu_isolated_root, monkeypatch):
    from deeptutor.knowledge import registry_backfill

    ran: list[str] = []

    def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(registry_backfill, "STARTUP_BACKFILL_ENABLED", True)
    monkeypatch.setattr(
        registry_backfill,
        "_startup_steps",
        lambda: [("first", broken), ("second", lambda: ran.append("second") or {"ok": 1})],
    )

    report = registry_backfill.run_startup_backfill()

    assert report == {"first": {"error": True}, "second": {"ok": 1}}
    assert ran == ["second"]
