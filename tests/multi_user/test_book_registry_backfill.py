"""Synthetic D22 inventory, canonical binding and resumable publication cases."""

from __future__ import annotations

import json
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
    snapshot = backfill._freeze_grants(users, raw)
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
