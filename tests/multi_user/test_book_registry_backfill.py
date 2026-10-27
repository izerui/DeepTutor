"""Synthetic D22 inventory, canonical binding and resumable publication cases."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from deeptutor.book import registry
from deeptutor.book import registry_backfill as backfill
from deeptutor.book.models import Book
from deeptutor.book.storage import BookStorage
from deeptutor.knowledge import registry as kbs
from deeptutor.multi_user import identity
from deeptutor.multi_user.paths import (
    get_admin_path_service, get_path_service_for_scope, local_admin_user, scope_for_user, user_context,
)


def _permission(username, value):
    users = identity.load_users()
    users[username]["book_permission"] = value
    identity._write_users(users)


def _setup(seed_user):
    seed_user("boss", role="admin")
    alice = seed_user("alice")
    bob = seed_user("bob")
    shelf = BookStorage(path_service=get_admin_path_service())
    for book_id in ("bk_read", "bk_edit", "bk_hidden"):
        shelf.save_book(Book(id=book_id, title=book_id.title()))
    own = BookStorage(path_service=get_path_service_for_scope(scope_for_user(alice["id"], is_admin=False)))
    own.save_book(Book(id="bk_alice", title="Alice notes"))
    _permission("alice", {"create": True, "default": "none", "books": {"bk_read": "read"}})
    _permission("bob", {"create": True, "books": {"bk_edit": "edit", "bk_hidden": "none"}})
    return shelf, alice, bob


def _items():
    return {(item.kind, item.key): item.data for item in kbs.list_migration_items()}


def _manifest(shelf, book_id):
    return shelf.path_service.get_book_manifest_file(book_id)


@pytest.mark.parametrize("legacy", [
    {"default": "read", "books": {"bk_edit": "edit"}},
    {"mode": "list", "book_ids": ["bk_read", "bk_edit"], "actions": {"delete": True}},
    {"mode": "all", "actions": {"create": False}},
])
def test_presnapshot_identity_failure_survives_normalization_and_create_save(seed_user, legacy):
    from deeptutor.multi_user.book_permission import BookPermission, legacy_book_shares

    shelf, _, _ = _setup(seed_user)
    _permission("alice", legacy)
    original = identity.USERS_FILE.read_bytes()
    identity.USERS_FILE.write_text("{")
    assert backfill.backfill_book_registry()["promoted"] == 0
    before = json.loads(registry.get_meta(backfill._INVENTORY_KEY))
    assert not before.get("grants")
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY) is None
    # The original identity is repaired before normal read/edit traffic resumes.
    identity.USERS_FILE.write_bytes(original)
    identity.get_user("alice")
    assert next(row for row in identity.list_user_info() if row["username"] == "alice")[
        "book_permission"
    ] == {"create": legacy.get("actions", {}).get("create", True)}
    assert identity.set_book_permission("alice", BookPermission(create=False))
    stored = json.loads(identity.USERS_FILE.read_text())["alice"]["book_permission"]
    assert legacy_book_shares(stored) == legacy_book_shares(legacy)
    assert backfill.backfill_book_registry()["promoted"] >= 2
    assert registry.get_book("bk_read").visibility == "public"
    assert registry.get_book("bk_edit").visibility == "public"
    after = json.loads(registry.get_meta(backfill._INVENTORY_KEY))
    assert after["started_ns"] == before["started_ns"]
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY)
    # Once migrated, an explicit withdrawal cannot be undone by retained evidence.
    with user_context(local_admin_user()):
        registry.set_book_visibility("bk_edit", "private")
    assert backfill.backfill_book_registry()["promoted"] == 0
    assert registry.get_book("bk_edit").visibility == "private"
    assert shelf.load_book("bk_edit") is not None


def test_every_scope_and_r1_notices(seed_user):
    shelf, alice, bob = _setup(seed_user)
    before = {path: path.read_bytes() for path in shelf.path_service.get_book_dir().rglob("*.json")}
    result = backfill.backfill_book_registry()
    assert result == {"admin": 3, "account": 1, "workspace": 0, "promoted": 2, "edit_grants": 1}
    assert registry.get_book("bk_read").visibility == "public"
    assert registry.get_book("bk_hidden").visibility == "private"
    assert registry.get_book("bk_read").created_by == "local-admin"
    assert registry.get_book("bk_alice").created_by == alice["id"]
    assert registry.get_book("bk_alice").visibility == "private"
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY)
    assert _items()[("book_auto_public", "bk_read")] == {
        "book_id": "bk_read", "title": "Bk_Read", "users": ["alice"],
    }
    assert _items()[("book_edit_grant_retired", f"{bob['id']}:bk_edit")] == {
        "user_id": bob["id"], "username": "bob", "book_id": "bk_edit", "title": "Bk_Edit",
    }
    assert not any(kind == "book_kb_unavailable" for kind, _ in _items())
    assert all(path.read_bytes() == data for path, data in before.items())


@pytest.mark.parametrize("permission,expected_edits", [
    ({"default": "read", "books": {}}, 1),
    ({"mode": "list", "book_ids": ["bk_hidden"], "actions": {"edit": True}}, 2),
    ({"mode": "all", "actions": {"delete": True}}, 1),
])
def test_legacy_shapes(seed_user, permission, expected_edits):
    _setup(seed_user)
    _permission("alice", permission)
    report = backfill.backfill_book_registry()
    assert registry.get_book("bk_hidden").visibility == "public"
    assert report["edit_grants"] == expected_edits


@pytest.mark.parametrize("raw", [None, [], "read", {"create": True}, {"mode": "unknown"},
                               {"default": "edit", "books": {"bk_read": "unknown"}},
                               {"default": [], "books": {"bk_read": {}}}])
def test_legacy_parser_fails_closed(raw):
    from deeptutor.multi_user.book_permission import legacy_book_shares

    assert legacy_book_shares(raw).level_for("bk_read") == "none"


def test_once_and_ghost_account(seed_user):
    _setup(seed_user)
    ghost = seed_user("ghost")
    backfill.backfill_book_registry()
    with user_context(local_admin_user()):
        registry.set_book_visibility("bk_read", "private")
    assert backfill.backfill_book_registry()["promoted"] == 0
    assert registry.get_book("bk_read").visibility == "private"
    assert not scope_for_user(ghost["id"], is_admin=False).root.exists()


@pytest.mark.parametrize("broken", ["json", "schema", "missing", "mismatch", "unreadable"])
def test_frozen_inventory_survives_bad_manifest_and_create_only_editor(seed_user, monkeypatch, broken):
    shelf, _, bob = _setup(seed_user)
    _permission("alice", {"default": "read"})
    path = _manifest(shelf, "bk_edit")
    original = path.read_bytes()
    load = BookStorage.load_book
    if broken == "json":
        path.write_text("{")
    elif broken == "schema":
        path.write_text("[]")
    elif broken == "missing":
        path.unlink()
    elif broken == "mismatch":
        data = json.loads(original)
        data["id"] = "bk_other"
        path.write_text(json.dumps(data))
    else:
        monkeypatch.setattr(BookStorage, "load_book", lambda self, uid: None if uid == "bk_edit" else load(self, uid))
    first = backfill.backfill_book_registry()
    assert first["promoted"] == 2
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY) is None
    assert ("book_auto_public", "bk_edit") not in _items()
    # Later private books are outside the inventory even under an old default.
    shelf.save_book(Book(id="bk_new", title="New private"))
    _permission("alice", {"create": False})
    _permission("bob", {"create": True})
    with user_context(local_admin_user()):
        registry.set_book_visibility("bk_read", "private")
    if broken == "unreadable":
        monkeypatch.setattr(BookStorage, "load_book", load)
    else:
        path.write_bytes(original)
    second = backfill.backfill_book_registry()
    assert second["promoted"] == 1
    assert registry.get_book("bk_new").visibility == "private"
    assert registry.get_book("bk_read").visibility == "private"
    assert registry.get_book("bk_edit").visibility == "public"
    assert _items()[("book_auto_public", "bk_edit")]["users"] == ["alice", "bob"]
    assert ("book_edit_grant_retired", f"{bob['id']}:bk_edit") in _items()
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY)


@pytest.mark.parametrize("kind", ["book_auto_public", "book_edit_grant_retired", "book_kb_unavailable"])
def test_notice_partial_commit_resumes_after_withdrawal(seed_user, make_kb, monkeypatch, kind):
    from deeptutor.multi_user.knowledge_access import current_kb_manager

    shelf, _, _ = _setup(seed_user)
    with user_context(local_admin_user()):
        manager = current_kb_manager()
        make_kb(manager, "wiki", {"type": "weknora", "server_url": "http://synthetic.invalid"})
        shelf.save_book(Book(id="bk_edit", title="Edit", knowledge_bases=["wiki"]))
    add = kbs.add_migration_item

    def fail(selected, key, data):
        if selected == kind:
            raise OSError("synthetic notice failure")
        return add(selected, key, data)

    monkeypatch.setattr(kbs, "add_migration_item", fail)
    backfill.backfill_book_registry()
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY) is None
    assert registry.get_book("bk_edit").visibility == "public"
    with user_context(local_admin_user()):
        registry.set_book_visibility("bk_edit", "private")
    shelf.save_book(Book(id="bk_new", title="New"))
    monkeypatch.setattr(kbs, "add_migration_item", add)
    result = backfill.backfill_book_registry()
    assert result["promoted"] == 0
    assert registry.get_book("bk_edit").visibility == "private"
    assert registry.get_book("bk_new").visibility == "private"
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY)
    connector = kbs.find_by_location(manager.base_dir, "wiki")
    assert connector.visibility == "private"
    assert kbs.list_references(connector.kb_id) == []
    assert _items()[("book_kb_unavailable", f"bk_edit:{connector.kb_id}")] == {
        "book_id": "bk_edit", "title": "Edit", "kb": "wiki", "reason": "connector",
    }
    assert ("book_auto_public", "bk_edit") in _items()


def test_publication_and_progress_failure_roll_back_together(seed_user, make_kb, monkeypatch):
    from deeptutor.multi_user.knowledge_access import current_kb_manager

    shelf, _, _ = _setup(seed_user)
    with user_context(local_admin_user()):
        manager = current_kb_manager()
        make_kb(manager, "physics")
        shelf.save_book(Book(id="bk_edit", title="Edit", knowledge_bases=["physics"]))
    put = backfill._put_progress

    def fail(conn, key, value):
        put(conn, key, value)
        if key.endswith("bk_edit"):
            raise RuntimeError("synthetic failure after progress write")

    monkeypatch.setattr(backfill, "_put_progress", fail)
    backfill.backfill_book_registry()
    kb = kbs.find_by_location(manager.base_dir, "physics")
    assert registry.get_book("bk_edit").visibility == "private"
    assert kbs.list_references(kb.kb_id) == []
    assert registry.get_meta(backfill._PROGRESS_PREFIX + "bk_edit") is None
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY) is None
    monkeypatch.setattr(backfill, "_put_progress", put)
    assert backfill.backfill_book_registry()["promoted"] == 1
    assert kbs.list_references(kb.kb_id)[0].referrer_id == "bk_edit"


def test_pending_binding_retries_without_rebinding_fixed_id(seed_user, make_kb, monkeypatch):
    from deeptutor.multi_user.knowledge_access import current_kb_manager

    shelf, _, _ = _setup(seed_user)
    with user_context(local_admin_user()):
        manager = current_kb_manager()
        make_kb(manager, "stable")
        make_kb(manager, "temporary")
        shelf.save_book(Book(id="bk_read", title="Read", knowledge_bases=["stable", "temporary"]))
    resolve = registry.resolve_kb_links

    def fail(refs):
        if refs == ["temporary"]:
            raise registry.BookKbResolutionError("synthetic transient")
        return resolve(refs)

    monkeypatch.setattr(registry, "resolve_kb_links", fail)
    backfill.backfill_book_registry()
    old = registry.book_kb_links("bk_read")["stable"]
    assert registry.has_kb_links("bk_read")
    assert not registry.kb_links_initialized("bk_read")
    assert registry.get_book("bk_read").visibility == "private"
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY) is None
    kbs.unregister_kb(old)
    # Recreate at the same name through the actual registry API.
    kbs.sync_base_dir(manager.base_dir, {"stable": manager.get_kb_entry("stable")}, created_by="local-admin")
    new = kbs.find_by_location(manager.base_dir, "stable")
    assert new.kb_id != old
    monkeypatch.setattr(registry, "resolve_kb_links", resolve)
    backfill.backfill_book_registry()
    assert registry.book_kb_links("bk_read")["stable"] == old
    assert registry.kb_links_initialized("bk_read")
    assert registry.get_book("bk_read").visibility == "public"
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY)


def test_bad_kb_catalog_does_not_become_confirmed_missing(seed_user):
    shelf, _, _ = _setup(seed_user)
    shelf.save_book(Book(id="bk_read", title="Read", knowledge_bases=["missing"]))
    root = get_admin_path_service().get_knowledge_bases_root()
    root.mkdir(parents=True, exist_ok=True)
    config = root / "kb_config.json"
    config.write_text("{")
    backfill.backfill_book_registry()
    assert config.read_text() == "{"
    assert not registry.kb_links_initialized("bk_read")
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY) is None
    config.write_text('{"knowledge_bases": {}}')
    backfill.backfill_book_registry()
    assert registry.book_kb_links("bk_read") == {"missing": ""}
    assert registry.kb_links_initialized("bk_read")
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY)


def test_owner_scope_and_workspace_are_canonical(seed_user, as_user, make_kb):
    from deeptutor.multi_user.knowledge_access import current_kb_manager
    from deeptutor.services.workspace import ContentWorkspaceService
    from deeptutor.services.workspace.context import workspace_context

    _, alice, _ = _setup(seed_user)
    with user_context(local_admin_user()):
        make_kb(current_kb_manager(), "physics")
    with as_user(alice["id"], username="alice"):
        workspace = ContentWorkspaceService().create_workspace("Research")["workspace_id"]
        with workspace_context(workspace):
            manager = current_kb_manager()
            make_kb(manager, "physics")
            BookStorage().save_book(Book(id="bk_workspace", title="Own", knowledge_bases=["physics"]))
            # An inherited caller scope must not redirect the startup scan.
            report = backfill.backfill_book_registry()
    assert report["workspace"] == 1
    record = registry.get_book("bk_workspace")
    kb = kbs.find_by_location(manager.base_dir, "physics")
    assert record.created_by == alice["id"]
    assert kb.created_by == alice["id"]
    assert registry.book_kb_links(record.book_id) == {"physics": kb.kb_id}
    assert record.visibility == "private"
    assert registry.get_book("bk_read").base_dir == str(get_admin_path_service().get_book_dir().resolve())


def test_collision_never_publishes_foreign_record(seed_user, caplog):
    shelf, alice, _ = _setup(seed_user)
    own = get_path_service_for_scope(scope_for_user(alice["id"], is_admin=False))
    registry.register_book(base_dir=own.get_book_dir(), dir_name="book_bk_read", title="Foreign", created_by=alice["id"])
    original = _manifest(shelf, "bk_read").read_bytes()
    backfill.backfill_book_registry()
    assert registry.get_book("bk_read").created_by == alice["id"]
    assert registry.get_book("bk_read").visibility == "private"
    assert "another location" in caplog.text
    assert _manifest(shelf, "bk_read").read_bytes() == original
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY) is None


@pytest.mark.parametrize("damage", ["json", "duplicate", "unsafe"])
def test_bad_identity_preflight_prevents_publication(seed_user, damage):
    shelf, _, _ = _setup(seed_user)
    _permission("alice", {"default": "read"})
    original = identity.USERS_FILE.read_bytes()
    if damage == "json":
        identity.USERS_FILE.write_text("{")
    else:
        data = json.loads(original)
        data["alice"]["id"] = data["bob"]["id"] if damage == "duplicate" else "../escape"
        identity.USERS_FILE.write_text(json.dumps(data))
    backfill.backfill_book_registry()
    assert registry.list_book_records() == []
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY) is None
    shelf.save_book(Book(id="bk_new", title="New during identity failure"))
    identity.USERS_FILE.write_bytes(original)
    assert backfill.backfill_book_registry()["promoted"] == 3
    assert registry.get_book("bk_new").visibility == "private"


def test_books_use_the_existing_startup_gate(seed_user, monkeypatch):
    from deeptutor.knowledge import registry_backfill as startup

    _setup(seed_user)
    assert startup.run_startup_backfill() == {}
    assert registry.get_book("bk_read") is None
    assert [name for name, _ in startup._startup_steps()][:2] == ["knowledge_bases", "books"]
    monkeypatch.setattr(startup, "STARTUP_BACKFILL_ENABLED", True)
    assert startup.run_startup_backfill()["books"]["promoted"] == 2


def test_empty_shelf_completes_without_count_heuristic(seed_user):
    seed_user("boss", role="admin")
    root = get_admin_path_service().get_book_dir()
    root.mkdir(parents=True)
    assert backfill.backfill_book_registry()["admin"] == 0
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY)


def test_workspace_catalog_failure_retries_without_republishing(seed_user, monkeypatch):
    from deeptutor.services.workspace import ContentWorkspaceService
    from deeptutor.services.workspace.models import WorkspaceError

    _setup(seed_user)
    catalog = ContentWorkspaceService._catalog

    def fail(self):
        raise WorkspaceError("synthetic unreadable workspace catalog")

    monkeypatch.setattr(ContentWorkspaceService, "_catalog", fail)
    backfill.backfill_book_registry()
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY) is None
    with user_context(local_admin_user()):
        registry.set_book_visibility("bk_read", "private")
    monkeypatch.setattr(ContentWorkspaceService, "_catalog", catalog)
    backfill.backfill_book_registry()
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY)
    assert registry.get_book("bk_read").visibility == "private"


def test_replaced_frozen_directory_is_not_a_legacy_target(seed_user, monkeypatch):
    shelf, _, _ = _setup(seed_user)
    load = BookStorage.load_book
    monkeypatch.setattr(BookStorage, "load_book", lambda self, uid: None if uid == "bk_edit" else load(self, uid))
    backfill.backfill_book_registry()
    original = shelf.book_root("bk_edit")
    # Retain the old inode so this case cannot pass through inode reuse.
    original.rename(original.with_name("saved_legacy"))
    shelf.save_book(Book(id="bk_edit", title="New same ID"))
    monkeypatch.setattr(BookStorage, "load_book", load)
    backfill.backfill_book_registry()
    assert registry.get_book("bk_edit").visibility == "private"
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY) is None
    assert ("book_auto_public", "bk_edit") not in _items()


def test_stale_registration_does_not_hide_invalid_required_manifest(seed_user):
    shelf, _, _ = _setup(seed_user)
    with user_context(local_admin_user()):
        backfill._sync_scope("local-admin")
    assert registry.kb_links_initialized("bk_read")
    _manifest(shelf, "bk_read").write_text("{")
    backfill.backfill_book_registry()
    assert registry.get_book("bk_read").visibility == "private"
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY) is None


def test_missing_bare_name_is_not_confirmed_when_admin_source_exists(seed_user, make_kb, as_user):
    from deeptutor.multi_user.knowledge_access import current_kb_manager

    _, alice, _ = _setup(seed_user)
    with user_context(local_admin_user()):
        make_kb(current_kb_manager(), "private_admin")
    with as_user(alice["id"], username="alice"):
        BookStorage().save_book(Book(id="bk_pending", title="Pending", knowledge_bases=["private_admin"]))
    backfill.backfill_book_registry()
    assert not registry.kb_links_initialized("bk_pending")
    assert registry.book_kb_links("bk_pending") == {}
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY) is None


def test_durable_outbox_survives_process_exit(seed_user, tmp_path):
    shelf, _, _ = _setup(seed_user)
    with user_context(local_admin_user()):
        backfill._sync_scope("local-admin")
    backfill._freeze_inventory(shelf.path_service.get_book_dir())
    users, raw = backfill._accounts()
    backfill._freeze_grants(users, raw)
    snapshot = backfill._resume_inventory()
    worker = r"""
import json
import os
from pathlib import Path
import sys
from deeptutor.book import registry, registry_backfill
from deeptutor.knowledge import registry as kbs
from deeptutor.multi_user import paths
from deeptutor.multi_user.paths import local_admin_user, user_context

registry.DB_PATH_OVERRIDE = Path(sys.argv[1])
kbs.DB_PATH_OVERRIDE = Path(sys.argv[2])
paths.ADMIN_WORKSPACE_ROOT = Path(sys.argv[3])
snapshot = json.loads(sys.argv[4])
with user_context(local_admin_user()):
    progress, promoted = registry_backfill._promote_target(snapshot, "book_bk_edit")
    assert promoted and not progress["done"]
    Path(sys.argv[5]).write_text(json.dumps(progress))
    os._exit(23)
"""
    ready = tmp_path / "committed-outbox.json"
    result = subprocess.run(
        [sys.executable, "-B", "-c", worker, str(registry.DB_PATH_OVERRIDE), str(kbs.DB_PATH_OVERRIDE),
         str(get_admin_path_service().workspace_root), json.dumps(snapshot), str(ready)],
        capture_output=True, text=True, timeout=20,
    )
    (tmp_path / "crash.stdout.log").write_text(result.stdout)
    (tmp_path / "crash.stderr.log").write_text(result.stderr)
    assert result.returncode == 23, result.stderr
    assert result.stderr == ""
    assert json.loads(ready.read_text())["notices"]
    assert registry.get_book("bk_edit").visibility == "public"
    assert ("book_auto_public", "bk_edit") not in _items()
    with user_context(local_admin_user()):
        registry.set_book_visibility("bk_edit", "private")
    backfill.backfill_book_registry()
    assert registry.get_book("bk_edit").visibility == "private"
    assert ("book_auto_public", "bk_edit") in _items()
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY)


def test_preflight_never_normalizes_away_source_grants(seed_user, monkeypatch):
    _setup(seed_user)
    original = identity.USERS_FILE.read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError("D3 must not invoke the live create-only loader")

    monkeypatch.setattr(identity, "load_users", forbidden)
    assert backfill.backfill_book_registry()["promoted"] == 2
    assert identity.USERS_FILE.read_bytes() == original


def test_missing_persisted_identity_waits_for_b_normalization(seed_user):
    _setup(seed_user)
    users = json.loads(identity.USERS_FILE.read_text())
    user_id = users["alice"].pop("id")
    identity.USERS_FILE.write_text(json.dumps(users))
    backfill.backfill_book_registry()
    assert registry.list_book_records() == []
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY) is None
    assert "id" not in json.loads(identity.USERS_FILE.read_text())["alice"]
    users["alice"]["id"] = user_id
    identity.USERS_FILE.write_text(json.dumps(users))
    assert backfill.backfill_book_registry()["promoted"] == 2


def test_global_marker_write_failure_keeps_completed_books_withdrawn(seed_user, monkeypatch):
    _setup(seed_user)
    write = registry.set_meta

    def fail(key, value):
        if key == backfill.SHELF_MIGRATED_KEY:
            raise OSError("synthetic final marker failure")
        return write(key, value)

    monkeypatch.setattr(registry, "set_meta", fail)
    with pytest.raises(OSError, match="final marker failure"):
        backfill.backfill_book_registry()
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY) is None
    with user_context(local_admin_user()):
        registry.set_book_visibility("bk_read", "private")
    monkeypatch.setattr(registry, "set_meta", write)
    assert backfill.backfill_book_registry()["promoted"] == 0
    assert registry.get_book("bk_read").visibility == "private"
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY)


@pytest.mark.parametrize("failure", ["iterdir", "lstat"])
@pytest.mark.parametrize("replace_grants", [False, True])
def test_first_inventory_failure_preserves_boundary_and_legacy_evidence(
    seed_user, monkeypatch, tmp_path, failure, replace_grants,
):
    shelf, _, bob = _setup(seed_user)
    _permission("alice", {"default": "read"})
    root = shelf.path_service.get_book_dir()
    method = getattr(Path, failure)
    failed_path = root if failure == "iterdir" else shelf.book_root("bk_edit")
    unavailable = True

    def fail(path, *args, **kwargs):
        if unavailable and path == failed_path:
            raise PermissionError("synthetic first inventory failure")
        return method(path, *args, **kwargs)

    monkeypatch.setattr(Path, failure, fail)
    backfill.backfill_book_registry()
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY) is None
    first_boundary = registry.get_meta(backfill._INVENTORY_KEY)
    (tmp_path / "first-inventory-boundary.json").write_text(first_boundary or "null")
    unavailable = False

    # Startup continued. A new book is explicitly private before retry.
    private_kb = kbs.register_kb(
        base_dir=root.parent / "new-sources", name="new-private",
        kind="uploaded", created_by="local-admin",
    )
    shelf.save_book(Book(
        id="bk_new", title="Deliberately private", knowledge_bases=[private_kb.ref], created_at=1,
    ))
    # Neither an editable manifest date nor a backdated mtime is old-inventory proof.
    os.utime(shelf.book_root("bk_new"), (1, 1))
    registry.register_book(
        base_dir=root, dir_name="book_bk_new", title="Deliberately private",
        created_by="local-admin", visibility="private",
    )
    if replace_grants:
        _permission("alice", {"create": False})
        _permission("bob", {"create": True})
    backfill.backfill_book_registry()

    assert registry.get_book("bk_new").visibility == "private"
    assert ("book_auto_public", "bk_new") not in _items()
    assert kbs.list_references(private_kb.kb_id) == []
    assert registry.get_book("bk_read").visibility == "public"
    assert registry.get_book("bk_edit").visibility == "public"
    assert _items()[("book_auto_public", "bk_edit")]["users"] == ["alice", "bob"]
    assert ("book_edit_grant_retired", f"{bob['id']}:bk_edit") in _items()
    assert first_boundary is not None
    first = json.loads(first_boundary)
    current = json.loads(registry.get_meta(backfill._INVENTORY_KEY))
    (tmp_path / "retried-inventory-boundary.json").write_text(json.dumps(current))
    assert first["inventory_complete"] is False
    assert current["started_ns"] == first["started_ns"]
    assert current["grants"] == first["grants"]
    assert "book_bk_new" not in current["targets"]
    if failure == "iterdir":
        # No unchanged listing ever proved the original census: bk_new cannot
        # be told apart from an edited original, and the unknown original
        # inventory is an obligation, not an empty successful scan.
        assert "book_bk_new" in current["pending"]
        assert registry.get_meta(backfill.SHELF_MIGRATED_KEY) is None
    else:
        # B2-8: the first listing was an unchanged census, so the later
        # arrival is a new book and cannot leave the migration pending.
        assert "book_bk_new" in current["post_fence"]
        assert "book_bk_new" not in current["pending"]
        assert current["inventory_complete"] is True
        assert registry.get_meta(backfill.SHELF_MIGRATED_KEY)
    with user_context(local_admin_user()):
        registry.set_book_visibility("bk_read", "private")
    assert backfill.backfill_book_registry()["promoted"] == 0
    assert registry.get_book("bk_read").visibility == "private"
    assert registry.get_book("bk_new").visibility == "private"
    assert kbs.list_references(private_kb.kb_id) == []


@pytest.mark.parametrize("empty_root", [False, True])
def test_unknown_inventory_cannot_complete_when_shelf_disappears(
    seed_user, monkeypatch, tmp_path, empty_root,
):
    shelf, _, bob = _setup(seed_user)
    _permission("alice", {"default": "read"})
    root = shelf.path_service.get_book_dir()
    original = Path.iterdir

    def fail(path):
        if path == root:
            raise OSError("synthetic unknown shelf")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", fail)
    backfill.backfill_book_registry()
    first = json.loads(registry.get_meta(backfill._INVENTORY_KEY))
    monkeypatch.setattr(Path, "iterdir", original)
    saved = tmp_path / "temporarily-unmounted-shelf"
    root.rename(saved)
    if empty_root:
        root.mkdir()
    _permission("alice", {"create": False})
    _permission("bob", {"create": True})
    backfill.backfill_book_registry()
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY) is None
    state = json.loads(registry.get_meta(backfill._INVENTORY_KEY))
    assert state["started_ns"] == first["started_ns"]
    assert state["inventory_complete"] is False
    assert state["grants"] == first["grants"]
    if empty_root:
        root.rmdir()
    saved.rename(root)
    backfill.backfill_book_registry()
    assert registry.get_book("bk_edit").visibility == "public"
    assert _items()[("book_auto_public", "bk_edit")]["users"] == ["alice", "bob"]
    assert ("book_edit_grant_retired", f"{bob['id']}:bk_edit") in _items()
    # The restored children have old ctime, but the whole original census is unknown.
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY) is None


@pytest.mark.parametrize("failure", ["iterdir", "lstat"])
def test_unchanged_original_inventory_can_finish_after_first_failure(seed_user, monkeypatch, failure):
    shelf, _, _ = _setup(seed_user)
    original = getattr(Path, failure)
    failed_path = shelf.path_service.get_book_dir() if failure == "iterdir" else shelf.book_root("bk_edit")

    def fail(path, *args, **kwargs):
        if path == failed_path:
            raise PermissionError("synthetic unchanged inventory failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, failure, fail)
    backfill.backfill_book_registry()
    boundary = json.loads(registry.get_meta(backfill._INVENTORY_KEY))
    assert boundary["inventory_complete"] is False
    monkeypatch.setattr(Path, failure, original)
    backfill.backfill_book_registry()
    recovered = json.loads(registry.get_meta(backfill._INVENTORY_KEY))
    assert recovered["started_ns"] == boundary["started_ns"]
    assert recovered["inventory_complete"] is True
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY)
    assert registry.get_book("bk_read").visibility == "public"
    assert registry.get_book("bk_edit").visibility == "public"


def test_already_frozen_v1_inventory_keeps_its_original_targets(seed_user):
    shelf, _, _ = _setup(seed_user)
    _permission("alice", {"default": "read"})
    backfill._freeze_inventory(shelf.path_service.get_book_dir())
    users, raw = backfill._accounts()
    backfill._freeze_grants(users, raw)
    snapshot = backfill._resume_inventory()
    assert snapshot["inventory_complete"]
    legacy = {key: snapshot[key] for key in ("base_dir", "targets", "grants")}
    registry.set_meta(backfill._INVENTORY_KEY, json.dumps(legacy))
    shelf.save_book(Book(id="bk_new", title="New private"))
    _permission("alice", {"create": True})
    backfill.backfill_book_registry()
    assert registry.get_book("bk_new").visibility == "private"
    assert registry.get_book("bk_hidden").visibility == "public"
    assert ("book_auto_public", "bk_new") not in _items()
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY)


@pytest.mark.parametrize("entry", ["identity_failure", "persisted_start", "persisted_grants"])
def test_attempts_zero_unknown_survives_absent_shelf_and_recovers_legacy(
    seed_user, tmp_path, entry,
):
    shelf, _, bob = _setup(seed_user)
    _permission("alice", {"default": "read"})
    root = shelf.path_service.get_book_dir()
    original_users = identity.USERS_FILE.read_bytes()
    if entry == "identity_failure":
        identity.USERS_FILE.write_text("{")
        backfill.backfill_book_registry()
        identity.USERS_FILE.write_bytes(original_users)
    else:
        # This is the durable state left by an exit before the first resume.
        backfill._freeze_inventory(root)
        if entry == "persisted_grants":
            users, raw = backfill._accounts()
            backfill._freeze_grants(users, raw)
    before = json.loads(registry.get_meta(backfill._INVENTORY_KEY))
    assert before["attempts"] == 0
    assert before["inventory_complete"] is False
    (tmp_path / "attempts-zero-before.json").write_text(json.dumps(before))

    saved = tmp_path / "temporarily-unmounted-original-shelf"
    root.rename(saved)
    backfill.backfill_book_registry()
    absent = json.loads(registry.get_meta(backfill._INVENTORY_KEY))
    (tmp_path / "attempts-zero-absent.json").write_text(json.dumps(absent))
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY) is None
    assert absent["inventory_complete"] is False
    assert absent["started_ns"] == before["started_ns"]
    assert absent["grants"] is not None

    _permission("alice", {"create": False})
    _permission("bob", {"create": True})
    saved.rename(root)
    backfill.backfill_book_registry()
    recovered = json.loads(registry.get_meta(backfill._INVENTORY_KEY))
    (tmp_path / "attempts-zero-recovered.json").write_text(json.dumps(recovered))
    assert recovered["started_ns"] == before["started_ns"]
    assert recovered["grants"] == absent["grants"]
    assert registry.get_book("bk_read").visibility == "public"
    assert registry.get_book("bk_edit").visibility == "public"
    assert _items()[("book_auto_public", "bk_edit")]["users"] == ["alice", "bob"]
    assert ("book_edit_grant_retired", f"{bob['id']}:bk_edit") in _items()
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY) is None


def test_fresh_confirmed_absent_shelf_can_complete(seed_user):
    seed_user("boss", role="admin")
    seed_user("alice")
    _permission("alice", {"default": "read"})
    shelf = BookStorage(path_service=get_admin_path_service())
    assert not shelf.path_service.get_book_dir().exists()
    assert backfill.backfill_book_registry()["promoted"] == 0
    snapshot = json.loads(registry.get_meta(backfill._INVENTORY_KEY))
    assert snapshot["initial_absence_confirmed"] is True
    assert snapshot["inventory_complete"] is True
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY)
    shelf.save_book(Book(id="bk_new", title="New private"))
    backfill.backfill_book_registry()
    assert registry.get_book("bk_new").visibility == "private"
    assert ("book_auto_public", "bk_new") not in _items()


def test_disappearance_during_fresh_initial_observation_is_not_initial_absence(
    seed_user, monkeypatch, tmp_path,
):
    shelf, _, bob = _setup(seed_user)
    root = shelf.path_service.get_book_dir()
    saved = tmp_path / "disappeared-after-fence"
    original = Path.lstat
    moved = False

    def disappear(path, *args, **kwargs):
        nonlocal moved
        if path == root and not moved:
            state = json.loads(registry.get_meta(backfill._INVENTORY_KEY))
            assert state["inventory_complete"] is False
            moved = True
            root.rename(saved)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", disappear)
    backfill.backfill_book_registry()
    snapshot = json.loads(registry.get_meta(backfill._INVENTORY_KEY))
    assert moved
    assert not snapshot.get("initial_absence_confirmed")
    assert snapshot["inventory_complete"] is False
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY) is None
    monkeypatch.setattr(Path, "lstat", original)
    saved.rename(root)
    backfill.backfill_book_registry()
    assert registry.get_book("bk_edit").visibility == "public"
    assert ("book_edit_grant_retired", f"{bob['id']}:bk_edit") in _items()


def _backfill_warnings(caplog):
    caplog.clear()
    with caplog.at_level("WARNING"):
        backfill.backfill_book_registry()
    return [record for record in caplog.records if record.levelno >= 30]


@pytest.mark.parametrize("arrival", ["created", "moved_in"])
def test_post_fence_shelf_directory_is_not_legacy_and_inventory_completes(
    seed_user, monkeypatch, caplog, arrival,
):
    """B2-8/M8: a book that appears on the shelf after the fence is a new book."""
    shelf, _, bob = _setup(seed_user)
    _permission("alice", {"default": "read"})
    failed_path = shelf.book_root("bk_edit")
    original = Path.lstat
    unavailable = True

    def fail(path, *args, **kwargs):
        if unavailable and path == failed_path:
            raise PermissionError("synthetic first inventory failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", fail)
    backfill.backfill_book_registry()
    first = json.loads(registry.get_meta(backfill._INVENTORY_KEY))
    assert first["inventory_complete"] is False
    unavailable = False

    if arrival == "created":
        shelf.save_book(Book(id="bk_new", title="Created after fence"))
    else:
        outside = BookStorage(path_service=get_path_service_for_scope(scope_for_user(bob["id"], is_admin=False)))
        outside.save_book(Book(id="bk_new", title="Moved after fence"))
        outside.book_root("bk_new").rename(shelf.book_root("bk_new"))

    backfill.backfill_book_registry()
    snapshot = json.loads(registry.get_meta(backfill._INVENTORY_KEY))
    assert snapshot["started_ns"] == first["started_ns"]
    assert "book_bk_new" not in snapshot["pending"]
    assert "book_bk_new" not in snapshot["targets"]
    assert snapshot["inventory_complete"] is True
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY)
    assert registry.get_book("bk_edit").visibility == "public"
    assert registry.get_book("bk_new").visibility == "private"
    assert ("book_auto_public", "bk_new") not in _items()
    # A second startup is a no-op and logs nothing at warning level.
    items = _items()
    assert _backfill_warnings(caplog) == []
    assert _items() == items
    assert registry.get_book("bk_new").visibility == "private"


@pytest.mark.parametrize("move", ["relocated_copy", "relocated_moved", "deleted"])
def test_target_moved_off_shelf_before_promotion_is_recorded_once(
    seed_user, tmp_path, caplog, move,
):
    """B2-8/M8: a frozen target that left the shelf is reported, never retried."""
    import shutil

    from deeptutor.book.sharing import shared_write

    shelf, _, bob = _setup(seed_user)
    _permission("alice", {"default": "read"})
    root = shelf.path_service.get_book_dir()
    backfill._freeze_inventory(root)
    users, raw = backfill._accounts()
    backfill._freeze_grants(users, raw)
    assert backfill._resume_inventory()["inventory_complete"] is True
    with user_context(local_admin_user()):
        backfill._sync_scope("local-admin")
    elsewhere = tmp_path / "workspace-books"
    elsewhere.mkdir()
    if move == "deleted":
        shutil.rmtree(shelf.book_root("bk_edit"))
    else:
        if move == "relocated_copy":
            shutil.copytree(shelf.book_root("bk_edit"), elsewhere / "book_bk_edit")
        else:
            shelf.book_root("bk_edit").rename(elsewhere / "book_bk_edit")
        with shared_write() as conn:
            registry._relocate_book(conn, "bk_edit", elsewhere)

    caplog.clear()
    backfill.backfill_book_registry()
    assert not [r for r in caplog.records if "D22 migration will retry" in r.getMessage()]
    if move == "relocated_copy":
        # The stale shelf copy is a separate registration conflict (warned by
        # the scope sync) until cleaned up; it is never a promotion target.
        assert registry.get_meta(backfill.SHELF_MIGRATED_KEY) is None
        shutil.rmtree(shelf.book_root("bk_edit"))
        backfill.backfill_book_registry()
    assert registry.get_book("bk_edit").visibility == "private"
    assert ("book_auto_public", "bk_edit") not in _items()
    assert _items()[("book_left_shelf_skipped", "bk_edit")] == {
        "book_id": "bk_edit", "title": "Bk_Edit", "users": ["alice", "bob"],
    }
    assert registry.get_book("bk_read").visibility == "public"
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY)
    # A second startup is a no-op and logs nothing at warning level.
    items = _items()
    assert _backfill_warnings(caplog) == []
    assert _items() == items
    assert registry.get_book("bk_edit").visibility == "private"


def test_legacy_book_edited_before_first_listing_is_still_legacy(seed_user, caplog):
    """B2-8 fix round 1: an unchanged shelf root proves every listed book is original."""
    shelf, _, bob = _setup(seed_user)
    _permission("alice", {"default": "read"})
    original = identity.USERS_FILE.read_bytes()
    identity.USERS_FILE.write_text("{")
    backfill.backfill_book_registry()
    first = json.loads(registry.get_meta(backfill._INVENTORY_KEY))
    assert first["attempts"] == 0 and first["inventory_complete"] is False
    identity.USERS_FILE.write_bytes(original)
    # Normal editing advances the book directory's ctime past the fence.
    before = shelf.book_root("bk_edit").lstat().st_ctime_ns
    shelf.save_book(Book(id="bk_edit", title="Bk_Edit", description="edited after fence"))
    assert shelf.book_root("bk_edit").lstat().st_ctime_ns > max(before, first["started_ns"])

    backfill.backfill_book_registry()
    snapshot = json.loads(registry.get_meta(backfill._INVENTORY_KEY))
    assert "book_bk_edit" in snapshot["targets"]
    assert "book_bk_edit" not in snapshot.get("post_fence", {})
    assert registry.get_book("bk_edit").visibility == "public"
    assert _items()[("book_auto_public", "bk_edit")]["users"] == ["alice", "bob"]
    assert ("book_edit_grant_retired", f"{bob['id']}:bk_edit") in _items()
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY)
    items = _items()
    assert _backfill_warnings(caplog) == []
    assert _items() == items


def _proven_census_with_pending_bk_edit(shelf, monkeypatch):
    """First listing proves the census while bk_edit itself cannot be read."""
    failed_path = shelf.book_root("bk_edit")
    original = Path.lstat
    state = {"unavailable": True}

    def fail(path, *args, **kwargs):
        if state["unavailable"] and path == failed_path:
            raise PermissionError("synthetic first inventory failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", fail)
    backfill.backfill_book_registry()
    monkeypatch.setattr(Path, "lstat", original)
    snapshot = json.loads(registry.get_meta(backfill._INVENTORY_KEY))
    assert "book_bk_edit" in snapshot["pending"]
    return snapshot


def test_pending_census_book_edited_after_shelf_change_stays_pending(seed_user, monkeypatch, caplog):
    """A proven original name is never reclassified as new; unprovable identity stays pending."""
    shelf, _, _ = _setup(seed_user)
    _permission("alice", {"default": "read"})
    _proven_census_with_pending_bk_edit(shelf, monkeypatch)
    shelf.save_book(Book(id="bk_edit", title="Bk_Edit", description="edited after fence"))
    shelf.save_book(Book(id="bk_new", title="Created after fence"))
    caplog.clear()
    with caplog.at_level("WARNING"):
        backfill.backfill_book_registry()
    snapshot = json.loads(registry.get_meta(backfill._INVENTORY_KEY))
    assert "book_bk_edit" in snapshot["pending"]
    assert "book_bk_edit" not in snapshot.get("post_fence", {})
    assert "book_bk_new" in snapshot["post_fence"]
    assert any("book_bk_edit" in record.getMessage() for record in caplog.records)
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY) is None
    assert registry.get_book("bk_edit").visibility == "private"
    assert registry.get_book("bk_new").visibility == "private"


def test_pending_census_book_removed_later_is_recorded_as_left_shelf(seed_user, monkeypatch, caplog):
    """B2-8 Minor 1: a never-classified original book that disappears is reported once."""
    import shutil

    shelf, _, _ = _setup(seed_user)
    _permission("alice", {"default": "read"})
    _proven_census_with_pending_bk_edit(shelf, monkeypatch)
    shutil.rmtree(shelf.book_root("bk_edit"))
    backfill.backfill_book_registry()
    snapshot = json.loads(registry.get_meta(backfill._INVENTORY_KEY))
    assert "book_bk_edit" not in snapshot["pending"]
    assert _items()[("book_left_shelf_skipped", "bk_edit")]["users"] == ["alice", "bob"]
    assert ("book_auto_public", "bk_edit") not in _items()
    assert registry.get_meta(backfill.SHELF_MIGRATED_KEY)
    items = _items()
    assert _backfill_warnings(caplog) == []
    assert _items() == items
