"""Legacy authorization never overrides a newer explicit visibility choice."""

import json
import sqlite3
from threading import Event, Thread

import pytest

from deeptutor.book import registry as books, registry_backfill as book_backfill
from deeptutor.book.models import Book
from deeptutor.book.storage import BookStorage
from deeptutor.knowledge import registry as kbs, registry_backfill as kb_backfill
from deeptutor.knowledge.manager import KnowledgeBaseManager
from deeptutor.multi_user import identity
from deeptutor.multi_user.grants import load_grant, save_grant
from deeptutor.multi_user.models import LOCAL_ADMIN_ID
from deeptutor.multi_user.paths import get_admin_path_service, local_admin_user, user_context


@pytest.fixture
def legacy(seed_user):
    seed_user("boss", role="admin")
    alice = seed_user("alice")
    save_grant(alice["id"], {"knowledge_bases": [{"resource_id": "admin:kb:atlas"}]})
    root = get_admin_path_service().get_knowledge_bases_root()
    root.mkdir(parents=True, exist_ok=True)
    return root, alice


def _kb(root, make_kb, *, fresh=False):
    manager = KnowledgeBaseManager(base_dir=str(root))
    if fresh:
        record = kbs.register_new_kb(
            base_dir=root, name="atlas", created_by=LOCAL_ADMIN_ID,
            visibility="private", check_available=lambda: None,
        )
    else:
        record = kbs.register_kb(
            base_dir=root, name="atlas", kind="uploaded", created_by=LOCAL_ADMIN_ID,
        )
    make_kb(manager, "atlas", text="Synthetic migration material")
    return record


def _public_notices():
    return [item for item in kbs.list_migration_items()
            if item.kind in {"kb_made_public", "book_auto_public"}]


def test_healthy_empty_root_retires_old_name_grant(legacy, make_kb):
    root, alice = legacy
    assert kb_backfill.backfill_registry()["admin"] == 0
    assert kbs.get_meta(kb_backfill.GRANTS_PROMOTED_KEY)
    record = _kb(root, make_kb, fresh=True)
    result = kb_backfill.backfill_registry()
    assert kbs.get_kb(record.kb_id).visibility == "private"
    assert result["promoted"] == 0
    assert kbs.get_meta(kb_backfill.GRANTS_PROMOTED_KEY)
    assert _public_notices() == []
    assert load_grant(alice["id"])["knowledge_bases"]


@pytest.mark.parametrize("boundary", ["notice", "marker", "completed"])
def test_kb_publication_retry_preserves_withdrawal(legacy, make_kb, monkeypatch, boundary):
    root, _ = legacy
    record = _kb(root, make_kb)
    hits = []
    with monkeypatch.context() as fault:
        if boundary == "notice":
            original = kbs.add_migration_item

            def fail_notice(kind, key, data):
                if kind == "kb_made_public":
                    assert kbs.get_kb(record.kb_id).visibility == "public"
                    hits.append(kind)
                    raise sqlite3.OperationalError("synthetic notice failure")
                return original(kind, key, data)

            fault.setattr(kbs, "add_migration_item", fail_notice)
        elif boundary == "marker":
            original = kbs.set_meta

            def fail_marker(key, value):
                if key == kb_backfill.GRANTS_PROMOTED_KEY:
                    assert kbs.get_kb(record.kb_id).visibility == "public"
                    hits.append(key)
                    raise sqlite3.OperationalError("synthetic marker failure")
                return original(key, value)

            fault.setattr(kbs, "set_meta", fail_marker)
        first = kb_backfill.backfill_registry()
    assert first["promoted"] == 1
    assert kbs.get_kb(record.kb_id).visibility == "public"
    assert bool(hits) == (boundary != "completed")
    assert bool(kbs.get_meta(kb_backfill.GRANTS_PROMOTED_KEY)) == (boundary == "completed")
    kbs.set_visibility(record.kb_id, "private")
    result = kb_backfill.backfill_registry()
    assert kbs.get_kb(record.kb_id).visibility == "private"
    assert result["promoted"] == 0
    # This notice describes the genuine earlier commit, never a second publication.
    assert len(_public_notices()) == 1
    assert kbs.get_meta(kb_backfill.GRANTS_PROMOTED_KEY)
    assert kbs.set_visibility(record.kb_id, "public").visibility == "public"


@pytest.mark.parametrize("decision", ["withdraw", "private", "none"])
def test_book_first_joint_rollback_respects_new_decision(legacy, make_kb, monkeypatch, decision):
    root, _ = legacy
    kb = _kb(root, make_kb)
    book_id = "bk_retry_withdrawal"
    users = identity.load_users()
    users["alice"]["book_permission"] = {
        "create": True, "default": "none", "books": {book_id: "read"},
    }
    identity._write_users(users)
    shelf = BookStorage(path_service=get_admin_path_service())
    shelf.save_book(Book(id=book_id, title="Synthetic retry", knowledge_bases=[kb.ref]))
    key = book_backfill._PROGRESS_PREFIX + book_id
    original = book_backfill._put_progress
    hits = []

    def fail_progress(conn, progress_key, value):
        original(conn, progress_key, value)
        assert conn.execute(
            "SELECT visibility FROM books.book_registry WHERE book_id = ?", (book_id,),
        ).fetchone()[0] == "public"
        assert conn.execute(
            "SELECT value FROM books.registry_meta WHERE key = ?", (key,),
        ).fetchone()
        hits.append(progress_key)
        raise RuntimeError("synthetic failure after joint publication and outbox")

    with monkeypatch.context() as fault:
        fault.setattr(book_backfill, "_put_progress", fail_progress)
        assert book_backfill.backfill_book_registry()["promoted"] == 0
    assert hits == [key]
    assert books.get_book(book_id).visibility == "private"
    assert books.get_meta(key) is None
    assert books.get_meta(book_backfill.SHELF_MIGRATED_KEY) is None
    assert _public_notices() == []
    assert kbs.list_references(kb.kb_id) == []
    with user_context(local_admin_user()):
        if decision == "withdraw":
            assert books.set_book_visibility(book_id, "public").visibility == "public"
        if decision != "none":
            assert books.set_book_visibility(book_id, "private").visibility == "private"
    if decision == "none":
        # An identically named key in the attached main DB is not book intent.
        kbs.set_meta("visibility_intent_v1:" + book_id, "private")
    result = book_backfill.backfill_book_registry()
    expected = "public" if decision == "none" else "private"
    assert books.get_book(book_id).visibility == expected
    assert result["promoted"] == int(decision == "none")
    assert len(_public_notices()) == int(decision == "none")
    assert len(kbs.list_references(kb.kb_id)) == int(decision == "none")
    assert json.loads(books.get_meta(key))["done"] is True
    assert books.get_meta(book_backfill.SHELF_MIGRATED_KEY)
    with user_context(local_admin_user()):
        assert books.set_book_visibility(book_id, "public").visibility == "public"
    assert len(kbs.list_references(kb.kb_id)) == 1


@pytest.mark.parametrize("creation", ["registered", "initializer", "reserved_public", "reserved_private"])
def test_new_creation_never_inherits_legacy_authority(legacy, make_kb, creation):
    from deeptutor.knowledge.initializer import KnowledgeBaseInitializer

    root, _ = legacy
    if creation == "registered":
        record = _kb(root, make_kb, fresh=True)
    else:
        reserved = None
        if creation.startswith("reserved_"):
            reserved = kbs.register_new_kb(
                base_dir=root, name="atlas", created_by=LOCAL_ADMIN_ID,
                visibility=creation.removeprefix("reserved_"), check_available=lambda: None,
            )
        with user_context(local_admin_user()):
            KnowledgeBaseInitializer("atlas", base_dir=str(root)).create_directory_structure()
        record = kbs.find_by_location(root, "atlas")
        if reserved:
            assert record.kb_id == reserved.kb_id
    expected = "public" if creation == "reserved_public" else "private"
    assert record.visibility == expected
    assert kb_backfill.backfill_registry()["promoted"] == 0
    assert kbs.get_kb(record.kb_id).visibility == expected
    assert _public_notices() == []


def test_initializer_repairs_legacy_identity_without_inventing_private_consent(legacy, monkeypatch):
    from deeptutor.knowledge.initializer import KnowledgeBaseInitializer

    root, _ = legacy
    old = kbs.register_kb(
        base_dir=root, name="atlas", kind="uploaded", created_by=LOCAL_ADMIN_ID,
    )
    assert not (root / "atlas").exists()
    assert kbs.get_meta("visibility_intent_v1:" + old.kb_id) is None
    real_sync = kb_backfill._sync_root_unguarded

    def unavailable(path, *, created_by):
        if path == root:
            raise OSError("synthetic legacy repair needed")
        return real_sync(path, created_by=created_by)

    with monkeypatch.context() as fault:
        fault.setattr(kb_backfill, "_sync_root_unguarded", unavailable)
        assert kb_backfill.backfill_registry()["promoted"] == 0
    with user_context(local_admin_user()):
        KnowledgeBaseInitializer("atlas", base_dir=str(root)).create_directory_structure()
    repaired = kbs.find_by_location(root, "atlas")
    assert repaired == old
    result = kb_backfill.backfill_registry()
    assert kbs.get_kb(old.kb_id).visibility == "public"
    assert result["promoted"] == 1
    assert kbs.get_meta("visibility_intent_v1:" + old.kb_id) is None
    assert [notice.key for notice in _public_notices()] == [old.kb_id]


@pytest.mark.parametrize("existing", [False, True])
def test_initializer_preserves_captured_owner_and_existing_canonical_authority(
    make_user, tmp_path, existing,
):
    from deeptutor.knowledge.initializer import KnowledgeBaseInitializer

    captured = make_user("captured-owner", role="teacher")
    root = captured.scope.root / "knowledge_bases"
    root.mkdir(parents=True)
    elsewhere = kbs.register_kb(
        base_dir=tmp_path / "other-canonical-root", name="atlas",
        kind="uploaded", created_by="unrelated-owner", visibility="public",
    )
    old = None
    if existing:
        old = kbs.register_kb(
            base_dir=root, name="atlas", kind="uploaded", created_by="original-owner",
        )
    with user_context(local_admin_user()):
        KnowledgeBaseInitializer("atlas", base_dir=str(root), owner=captured).create_directory_structure()
    record = kbs.find_by_location(root, "atlas")
    assert record.created_by == ("original-owner" if existing else captured.id)
    assert record.base_dir == str(root.resolve()) and record.dir_name == "atlas"
    assert record.visibility == "private"
    assert kbs.get_kb(elsewhere.kb_id) == elsewhere
    if existing:
        assert record == old
        assert kbs.get_meta("visibility_intent_v1:" + record.kb_id) is None
    else:
        assert kbs.get_meta("visibility_intent_v1:" + record.kb_id) == "private"


@pytest.mark.parametrize("registered", [True, False])
def test_failed_scan_preserves_old_identity_and_excludes_new_creation(
    legacy, make_kb, monkeypatch, registered,
):
    from deeptutor.knowledge.initializer import KnowledgeBaseInitializer

    root, alice = legacy
    if registered:
        old = _kb(root, make_kb)
    else:
        make_kb(KnowledgeBaseManager(base_dir=str(root)), "atlas", text="Legacy raw source")
        old = None
    save_grant(alice["id"], {"knowledge_bases": [
        {"resource_id": "admin:kb:atlas"}, {"resource_id": "admin:kb:later"},
    ]})
    real_sync = kb_backfill._sync_root_unguarded

    def unavailable(path, *, created_by):
        if path == root:
            raise OSError("synthetic unavailable admin catalog")
        return real_sync(path, created_by=created_by)

    with monkeypatch.context() as fault:
        fault.setattr(kb_backfill, "_sync_root_unguarded", unavailable)
        assert kb_backfill.backfill_registry()["promoted"] == 0
    assert kbs.get_meta(kb_backfill.GRANTS_PROMOTED_KEY) is None
    before = json.loads(kbs.get_meta(kb_backfill._INVENTORY_KEY))
    with user_context(local_admin_user()):
        KnowledgeBaseInitializer("later", base_dir=str(root)).create_directory_structure()
    # Retry uses frozen evidence, even if the old editor no longer writes grants.
    save_grant(alice["id"], {"knowledge_bases": []})
    result = kb_backfill.backfill_registry()
    current = kbs.find_by_location(root, "atlas")
    if old:
        assert current.kb_id == old.kb_id
    assert current.visibility == "public"
    assert kbs.find_by_location(root, "later").visibility == "private"
    assert result["promoted"] == 1
    after = json.loads(kbs.get_meta(kb_backfill._INVENTORY_KEY))
    assert after["started_ns"] == before["started_ns"]
    assert after["grants"] == before["grants"]
    assert [notice.key for notice in _public_notices()] == [current.kb_id]


@pytest.mark.parametrize("decision", ["none", "private", "replacement"])
def test_kb_joint_rollback_and_fixed_target_retry(legacy, make_kb, monkeypatch, decision):
    root, _ = legacy
    record = _kb(root, make_kb)
    put = kb_backfill._put_progress
    hits = []

    def fail(conn, kb_id, progress):
        put(conn, kb_id, progress)
        assert conn.execute(
            "SELECT visibility FROM kb_registry WHERE kb_id = ?", (kb_id,),
        ).fetchone()[0] == "public"
        hits.append(kb_id)
        raise sqlite3.OperationalError("synthetic rollback of publication and progress")

    with monkeypatch.context() as fault:
        fault.setattr(kb_backfill, "_put_progress", fail)
        assert kb_backfill.backfill_registry()["promoted"] == 0
    assert hits == [record.kb_id]
    assert kbs.get_kb(record.kb_id).visibility == "private"
    assert kbs.get_meta(kb_backfill._PROGRESS_PREFIX + record.kb_id) is None
    assert _public_notices() == []
    current = record
    if decision == "private":
        kbs.set_visibility(record.kb_id, "private")
    elif decision == "replacement":
        current = kbs.register_new_kb(
            base_dir=root, name="atlas", created_by=LOCAL_ADMIN_ID,
            visibility="private", check_available=lambda: None,
        )
        assert current.kb_id != record.kb_id
    result = kb_backfill.backfill_registry()
    assert kbs.get_kb(current.kb_id).visibility == ("public" if decision == "none" else "private")
    assert result["promoted"] == int(decision == "none")
    assert len(_public_notices()) == int(decision == "none")


def test_old_committed_notice_prevents_republication_without_new_progress(legacy, make_kb):
    root, _ = legacy
    record = _kb(root, make_kb)
    # An old binary left a successful notice and a private row, but no new
    # intent/outbox keys. Do not invent a new publication from the old grant.
    kbs.add_migration_item("kb_made_public", record.kb_id, {
        "kb_id": record.kb_id, "name": "atlas", "users": ["alice"],
    })
    assert kb_backfill.backfill_registry()["promoted"] == 0
    assert kbs.get_kb(record.kb_id).visibility == "private"
    assert len(_public_notices()) == 1


@pytest.mark.parametrize("resource", ["kb", "book"])
@pytest.mark.parametrize("first", ["setter", "migration"])
def test_visibility_setter_and_migration_share_one_write_boundary(
    legacy, make_kb, monkeypatch, resource, first,
):
    from deeptutor.book import sharing
    from deeptutor.core import visibility_intent

    root, _ = legacy
    kb = _kb(root, make_kb)
    if resource == "book":
        book_id = "bk_concurrent"
        users = identity.load_users()
        users["alice"]["book_permission"] = {"default": "read"}
        identity._write_users(users)
        shelf = BookStorage(path_service=get_admin_path_service())
        shelf.save_book(Book(id=book_id, title="Concurrent", knowledge_bases=[kb.ref]))
        module = book_backfill
        # Freeze/bind with the real migration, rolling back publication only.
        def rollback(*_args):
            raise RuntimeError("synthetic initial rollback")

        with monkeypatch.context() as fault:
            fault.setattr(module, "_put_progress", rollback)
            book_backfill.backfill_book_registry()
        migrate = book_backfill.backfill_book_registry
        setter = lambda: books.set_book_visibility(book_id, "private")
        current = lambda: books.get_book(book_id)
        target_id = book_id
    else:
        module = kb_backfill
        kb_backfill._begin_inventory(root)
        from deeptutor.multi_user.identity import list_user_info

        kb_backfill._freeze_grants(list_user_info())
        kb_backfill._freeze_targets(root)
        migrate = kb_backfill.backfill_registry
        setter = lambda: kbs.set_visibility(kb.kb_id, "private")
        current = lambda: kbs.get_kb(kb.kb_id)
        target_id = kb.kb_id

    entered, release, second_started, second_done = Event(), Event(), Event(), Event()
    errors = []

    def assert_sqlite_writer_locked():
        paths = [kbs._db_path()]
        if resource == "book":
            paths.append(books._db_path())
        for path in paths:
            conn = sqlite3.connect(path, timeout=0, isolation_level=None)
            try:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    conn.execute("BEGIN IMMEDIATE")
            finally:
                conn.close()

    if first == "migration":
        original = module._put_progress

        def hold_progress(conn, key, value):
            original(conn, key, value)
            assert_sqlite_writer_locked()
            entered.set()
            assert release.wait(5)

        monkeypatch.setattr(module, "_put_progress", hold_progress)
        operations = [migrate, setter]
    else:
        original = visibility_intent.record_choice

        def hold_intent(conn, resource_id, visibility, *, schema):
            original(conn, resource_id, visibility, schema=schema)
            if resource_id == target_id:
                assert_sqlite_writer_locked()
                entered.set()
                assert release.wait(5)

        monkeypatch.setattr(visibility_intent, "record_choice", hold_intent)
        monkeypatch.setattr(sharing, "record_choice", hold_intent)
        operations = [setter, migrate]

    def run(index):
        try:
            with user_context(local_admin_user()):
                if index:
                    second_started.set()
                operations[index]()
        except BaseException as exc:
            errors.append(exc)
        finally:
            if index:
                second_done.set()

    workers = [Thread(target=run, args=(index,), daemon=True) for index in range(2)]
    try:
        workers[0].start()
        assert entered.wait(5)
        workers[1].start()
        assert second_started.wait(5)
        assert not second_done.wait(0.1)
    finally:
        release.set()
        for worker in workers:
            if worker.ident is not None:
                worker.join(8)
    assert all(not worker.is_alive() for worker in workers)
    assert errors == []
    assert current().visibility == "private"
    assert len(_public_notices()) == int(first == "migration")
    if resource == "book":
        assert kbs.list_references(kb.kb_id) == []
        # Both DBs contain registry_meta; book intent must never land in main.
        assert books.get_meta("visibility_intent_v1:" + target_id) == "private"
        assert kbs.get_meta("visibility_intent_v1:" + target_id) is None
    assert migrate()["promoted"] == 0


def test_failed_scan_then_missing_root_retains_pending_legacy(legacy, make_kb, monkeypatch):
    root, _ = legacy
    make_kb(KnowledgeBaseManager(base_dir=str(root)), "atlas", text="Legacy unregistered")
    real_sync = kb_backfill._sync_root_unguarded

    def unavailable(path, *, created_by):
        if path == root:
            raise OSError("synthetic unavailable scan")
        return real_sync(path, created_by=created_by)

    with monkeypatch.context() as fault:
        fault.setattr(kb_backfill, "_sync_root_unguarded", unavailable)
        kb_backfill.backfill_registry()
    hidden = root.with_name("temporarily-unavailable")
    root.rename(hidden)
    try:
        kb_backfill.backfill_registry()
        assert kbs.get_meta(kb_backfill.GRANTS_PROMOTED_KEY) is None
        snapshot = json.loads(kbs.get_meta(kb_backfill._INVENTORY_KEY))
        assert snapshot["inventory_complete"] is False
    finally:
        hidden.rename(root)
    assert kb_backfill.backfill_registry()["promoted"] == 1
    assert kbs.find_by_location(root, "atlas").visibility == "public"


@pytest.mark.parametrize("resource", ["kb", "book"])
def test_failed_setter_rolls_back_intent_with_visibility(legacy, make_kb, monkeypatch, resource):
    from deeptutor.book import sharing
    from deeptutor.core import visibility_intent

    root, _ = legacy
    kb = _kb(root, make_kb)
    if resource == "book":
        shelf = BookStorage(path_service=get_admin_path_service())
        shelf.save_book(Book(id="bk_rollback", title="Rollback", knowledge_bases=[kb.ref]))
        users = identity.load_users()
        users["alice"]["book_permission"] = {"default": "read"}
        identity._write_users(users)
        with user_context(local_admin_user()):
            book_backfill._sync_scope(LOCAL_ADMIN_ID)
        target_id = "bk_rollback"
        setter = lambda: books.set_book_visibility(target_id, "public")
        current = lambda: books.get_book(target_id)
        store = books
        migrate = book_backfill.backfill_book_registry
    else:
        target_id = kb.kb_id
        setter = lambda: kbs.set_visibility(target_id, "public")
        current = lambda: kbs.get_kb(target_id)
        store = kbs
        migrate = kb_backfill.backfill_registry
    original = visibility_intent.record_choice
    hits = []

    def fail(conn, resource_id, visibility, *, schema):
        original(conn, resource_id, visibility, schema=schema)
        hits.append(resource_id)
        raise RuntimeError("synthetic intent write failure")

    with monkeypatch.context() as fault, user_context(local_admin_user()):
        fault.setattr(visibility_intent, "record_choice", fail)
        fault.setattr(sharing, "record_choice", fail)
        with pytest.raises(RuntimeError, match="intent write"):
            setter()
    assert hits == [target_id]
    assert current().visibility == "private"
    assert store.get_meta("visibility_intent_v1:" + target_id) is None
    assert kbs.list_references(kb.kb_id) == []
    assert migrate()["promoted"] == 1
    assert current().visibility == "public"


def test_one_pending_target_does_not_lose_other_progress(legacy, make_kb, monkeypatch):
    root, alice = legacy
    atlas = _kb(root, make_kb)
    make_kb(KnowledgeBaseManager(base_dir=str(root)), "other", text="Other legacy source")
    save_grant(alice["id"], {"knowledge_bases": [
        {"resource_id": "admin:kb:atlas"}, {"resource_id": "admin:kb:other"},
    ]})
    put = kb_backfill._put_progress

    def fail_one(conn, kb_id, progress):
        put(conn, kb_id, progress)
        if kb_id == atlas.kb_id:
            raise RuntimeError("synthetic single-target rollback")

    with monkeypatch.context() as fault:
        fault.setattr(kb_backfill, "_put_progress", fail_one)
        assert kb_backfill.backfill_registry()["promoted"] == 1
    other = kbs.find_by_location(root, "other")
    assert kbs.get_kb(atlas.kb_id).visibility == "private"
    assert other.visibility == "public"
    assert kbs.get_meta(kb_backfill.GRANTS_PROMOTED_KEY) is None
    kbs.set_visibility(other.kb_id, "private")
    assert kb_backfill.backfill_registry()["promoted"] == 1
    assert kbs.get_kb(atlas.kb_id).visibility == "public"
    assert kbs.get_kb(other.kb_id).visibility == "private"
    assert kbs.get_meta(kb_backfill.GRANTS_PROMOTED_KEY)
    assert len(_public_notices()) == 2


def test_book_make_public_records_kb_intent_in_main_database(legacy, make_kb):
    root, _ = legacy
    kb = _kb(root, make_kb)
    book_id = "bk_kb_choice"
    books.register_book(
        base_dir=get_admin_path_service().get_book_dir(), dir_name="book_" + book_id,
        title="Explicit KB choice", created_by=LOCAL_ADMIN_ID,
    )
    books.set_book_kb_links(book_id, {kb.ref: kb.kb_id})
    with user_context(local_admin_user()):
        books.share_book_kbs(book_id, {kb.kb_id: "make_public"})
    assert kbs.get_kb(kb.kb_id).visibility == "public"
    assert kbs.get_meta("visibility_intent_v1:" + kb.kb_id) == "public"
    assert books.get_meta("visibility_intent_v1:" + kb.kb_id) is None
    assert kbs.list_references(kb.kb_id) == []
    kbs.set_visibility(kb.kb_id, "private")
    assert kb_backfill.backfill_registry()["promoted"] == 0
    assert kbs.get_kb(kb.kb_id).visibility == "private"
    assert _public_notices() == []


@pytest.mark.parametrize("choice", ["explicit", "none", "empty", "unrelated", "ref"])
def test_book_reference_choice_survives_pending_kb_migration(
    legacy, make_kb, monkeypatch, tmp_path, choice,
):
    root, alice = legacy
    kb = _kb(root, make_kb)
    intent_key = "visibility_intent_v1:" + kb.kb_id
    progress_key = kb_backfill._PROGRESS_PREFIX + kb.kb_id
    original_grant = load_grant(alice["id"])
    put_progress = kb_backfill._put_progress
    hits = []

    def rollback(conn, kb_id, progress):
        put_progress(conn, kb_id, progress)
        assert kb_id == kb.kb_id
        assert conn.execute(
            "SELECT visibility FROM kb_registry WHERE kb_id = ?", (kb_id,),
        ).fetchone()[0] == "public"
        hits.append(kb_id)
        raise sqlite3.OperationalError("synthetic publication/outbox rollback")

    with monkeypatch.context() as fault:
        fault.setattr(kb_backfill, "_put_progress", rollback)
        first = kb_backfill.backfill_registry()
    assert first["promoted"] == 0 and hits == [kb.kb_id]
    assert kbs.get_kb(kb.kb_id).visibility == "private"
    assert kbs.get_meta(intent_key) is None
    assert kbs.get_meta(progress_key) is None
    assert kbs.get_meta(kb_backfill.GRANTS_PROMOTED_KEY) is None
    assert _public_notices() == []
    frozen = json.loads(kbs.get_meta(kb_backfill._INVENTORY_KEY))
    assert frozen["identities"]["atlas"] == kb.kb_id
    assert frozen["targets"][kb.kb_id]["owner"] == LOCAL_ADMIN_ID
    assert frozen["grants"]["atlas"] == ["alice"]
    book = books.register_book(
        base_dir=get_admin_path_service().get_book_dir(),
        dir_name="book_bk_reference_intent", title="Explicit reference choice",
        created_by=LOCAL_ADMIN_ID,
    )
    books.set_book_kb_links(book.book_id, {kb.ref: kb.kb_id})
    choices = {
        "explicit": {kb.kb_id: "reference_only"},
        "none": None,
        "empty": {},
        "unrelated": {"unrelated": "reference_only"},
        "ref": {kb.ref: "reference_only"},
    }[choice]
    actor = local_admin_user()
    assert actor.id == kb.created_by == book.created_by == LOCAL_ADMIN_ID
    assert actor.is_admin and kbs.can_write(kb, actor) and books.can_write_book(book, actor)
    with user_context(actor):
        plan = books.plan_book_kbs(book.book_id)
        assert plan.private_kbs == (kb,)
        assert not plan.issues and not plan.missing
        assert books.set_book_visibility(
            book.book_id, "public", kb_choices=choices,
        ).visibility == "public"
    assert kbs.get_kb(kb.kb_id).visibility == "private"
    refs = kbs.list_references(kb.kb_id)
    assert len(refs) == 1
    assert (refs[0].referrer_kind, refs[0].referrer_id) == ("book", book.book_id)
    assert books.get_meta(intent_key) is None
    assert books.get_meta("visibility_intent_v1:" + book.book_id) == "public"
    assert kbs.get_meta("visibility_intent_v1:" + book.book_id) is None
    immediate_intent = kbs.get_meta(intent_key)
    retried = kb_backfill.backfill_registry()
    current = kbs.get_kb(kb.kb_id)
    assert (current.kb_id, current.base_dir, current.dir_name, current.created_by, current.kind) == (
        kb.kb_id, kb.base_dir, kb.dir_name, kb.created_by, kb.kind,
    )
    assert json.loads(kbs.get_meta(kb_backfill._INVENTORY_KEY)) == frozen
    assert load_grant(alice["id"]) == original_grant
    notices = _public_notices()
    (tmp_path / "reference-only-evidence.json").write_text(json.dumps({
        "choice": choice, "kb_id": kb.kb_id, "book_id": book.book_id,
        "actor": {"id": actor.id, "role": actor.role},
        "frozen": frozen, "first_report": first, "immediate_intent": immediate_intent,
        "retry_report": retried, "final_visibility": current.visibility,
        "notices": [notice.to_dict() for notice in notices],
    }, indent=2), encoding="utf-8")
    explicit = choice == "explicit"
    assert current.visibility == ("private" if explicit else "public")
    assert immediate_intent == ("private" if explicit else None)
    assert retried["promoted"] == int(not explicit)
    assert [notice.key for notice in notices] == ([] if explicit else [kb.kb_id])
    assert kbs.get_meta(kb_backfill.GRANTS_PROMOTED_KEY)
    assert json.loads(kbs.get_meta(progress_key))["done"] is True
    assert kbs.list_references(kb.kb_id) == refs
    # A later authorized positive choice remains effective.
    with user_context(actor):
        books.share_book_kbs(book.book_id, {kb.kb_id: "make_public"})
    assert kbs.get_kb(kb.kb_id).visibility == "public"
    if explicit:
        assert kbs.get_meta(intent_key) == "public"
