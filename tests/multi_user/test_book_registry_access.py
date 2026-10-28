"""Canonical Book access, creation ordering and retry scope."""

from __future__ import annotations

import asyncio
import json

import pytest

from deeptutor.api.routers import book as router
from deeptutor.book import registry, registry_backfill
from deeptutor.book.engine import BookEngine
from deeptutor.book.event_hub import get_book_bus, get_book_stream
from deeptutor.book.models import Block, BlockStatus, BlockType, Book, BookProposal, Page, PageStatus
from deeptutor.book.storage import BookStorage, get_book_storage
from deeptutor.core.visibility_intent import explicit_choice
from deeptutor.multi_user import book_access
from deeptutor.runtime.stream_bus import StreamBus


def _write(book_id="bk_one", *, title="Canonical", refs=()):
    store = get_book_storage()
    book = Book(id=book_id, title=title, page_count=1, knowledge_bases=list(refs))
    store.save_book(book)
    store.save_page(Page(
        id="pg_1", book_id=book_id, title="Page", status=PageStatus.READY,
        blocks=[Block(id="blk_1", type=BlockType.TEXT, status=BlockStatus.READY,
                      payload={"body": title})],
    ))
    return book


async def _forwarded():
    """Socket authorization runs in a worker thread (B3-1); let forwarding settle."""
    for _ in range(20):
        await asyncio.sleep(0.005)


def _choice(book_id):
    with registry._read() as conn:
        if conn is None or not explicit_choice(conn, book_id, schema="main"):
            return None
        return conn.execute(
            "SELECT value FROM registry_meta WHERE key=?", ("visibility_intent_v1:" + book_id,),
        ).fetchone()[0]


@pytest.fixture
def proposal(monkeypatch):
    async def ideate(*_args, **_kwargs):
        return BookProposal(title="New book", estimated_chapters=1)

    monkeypatch.setattr(BookEngine, "_run_ideation", ideate)


def test_lazy_registration_keeps_default_private_without_consent(as_user):
    with as_user("teacher", role="teacher"):
        _write()
        assert registry.get_book("bk_one") is None
        resolved = book_access.resolve_book("bk_one")
        assert resolved.can_edit and resolved.can_delete and resolved.source == "own"
        assert resolved.record.created_by == "teacher"
        assert resolved.capabilities()["can_set_visibility"]
        assert registry.resolve_book_ref("bk_one") == resolved.record
        _write("bk_legacy")
        assert registry.resolve_book_ref("bk_legacy").created_by == "teacher"
        assert registry.resolve_book_ref("book:unknown") is None
    assert _choice("bk_one") is None
    assert registry.get_book("bk_one").visibility == "private"


def test_cached_and_explicit_storage_keep_their_original_location(as_user):
    with as_user("owner"):
        cached = get_book_storage()
        explicit = BookStorage()
        _write()
    with as_user("reader"):
        _write(title="Collision")
        assert cached.load_book("bk_one").title == "Canonical"
        assert explicit.load_book("bk_one").title == "Canonical"
        assert get_book_storage().load_book("bk_one").title == "Collision"


@pytest.mark.asyncio
async def test_public_private_reference_and_admin_access(as_user):
    with as_user("teacher", role="teacher"):
        _write()
        _write("bk_private")
        book_access.accessible_books()
        registry.set_book_visibility("bk_one", "public")
        registry.replace_book_references("course", "course1", ["bk_private"], created_by="teacher")
    registry.register_book_reference_reader("course", lambda _ref, uid: uid == "reader")
    with as_user("reader", role="student"):
        resolved = book_access.resolve_book("book:bk_one")
        assert resolved.source == "shared" and not resolved.can_edit and not resolved.can_delete
        assert resolved.capabilities()["group"] == "public"
        assert book_access.resolve_book("bk_private").permission == "read"
        await router.mark_visited(router.ProgressRequest(book_id="book:bk_one", page_id="pg_1"))
        assert (await router.get_book("book:bk_one"))["progress"]["current_page_id"] == "pg_1"
    with as_user("other"):
        assert book_access.resolve_book("bk_private") is None
        assert (await router.get_book("bk_one"))["progress"]["current_page_id"] == ""
    with as_user("root", role="admin"):
        assert book_access.resolve_book("book:bk_private").can_delete
    with as_user("teacher", role="teacher"):
        assert (await router.get_book("bk_one"))["progress"]["current_page_id"] == ""


def test_student_owner_can_withdraw_admin_published_book(as_user):
    """Student owner of an admin-published book can set it private (B2-11)."""
    # Student creates book, admin makes it public.
    with as_user("student", role="student"):
        _write()
        book_access.accessible_books()
    with as_user("root", role="admin"):
        registry.set_book_visibility("bk_one", "public")
    # Student owner sees can_set_visibility=True on the public book.
    with as_user("student", role="student"):
        caps = book_access.resolve_book("bk_one").capabilities()
        assert caps["can_set_visibility"], "student owner of a public book should be allowed to withdraw"
        assert not caps["can_make_public"], "student should not be able to make books public"
    # After withdrawing, the flag becomes false (no further visibility actions).
    with as_user("root", role="admin"):
        registry.set_book_visibility("bk_one", "private")
    with as_user("student", role="student"):
        caps = book_access.resolve_book("bk_one").capabilities()
        assert not caps["can_set_visibility"], "student owner of a private book has no visibility actions"


def test_library_only_adds_foreign_books_in_general_and_lists_transfers(as_user):
    from deeptutor.services.workspace import ContentWorkspaceService
    from deeptutor.services.workspace.context import workspace_context

    with as_user("teacher", role="teacher"):
        _write()
        _write("bk_private")
        book_access.accessible_books()
        registry.set_book_visibility("bk_one", "public")
    with as_user("reader"):
        assert [b.id for b, _ in book_access.accessible_books()] == ["bk_one"]
        wid = ContentWorkspaceService().create_workspace("Physics")["workspace_id"]
        with workspace_context(wid):
            assert book_access.accessible_books() == []
    registry.transfer_book_ownership("teacher", "root")
    with as_user("root", role="admin"):
        listed = {b.id: r for b, r in book_access.accessible_books()}
        assert set(listed) == {"bk_one", "bk_private"}
        assert all(r.can_edit and r.capabilities()["group"] == "mine" for r in listed.values())


@pytest.mark.asyncio
async def test_explicit_collision_uses_canonical_but_bare_local_keeps_editing(as_user):
    with as_user("teacher", role="teacher"):
        _write()
        canonical = book_access.resolve_book("bk_one")
        registry.set_book_visibility("bk_one", "public")
    with as_user("reader", role="teacher"):
        _write(title="Local import")
        local = book_access.resolve_book("bk_one")
        assert local.record is None and local.can_edit and local.can_delete
        assert not local.capabilities()["can_set_visibility"]
        assert local.capabilities()["ref"] == ""
        explicit = book_access.resolve_book("book:bk_one")
        assert explicit.engine.storage.path_service.get_book_dir() == canonical.engine.storage.path_service.get_book_dir()
        assert not explicit.can_edit
        assert (await router.get_book("book:bk_one"))["book"]["title"] == "Canonical"
        assert (await router.get_book("bk_one"))["book"]["title"] == "Local import"
        with pytest.raises(Exception) as denied:
            await router.update_block(router.UpdateBlockRequest(
                book_id="book:bk_one", page_id="pg_1", block_id="blk_1",
                body="forbidden", expected_revision=1,
            ))
        assert denied.value.status_code == 404
        await router.update_block(router.UpdateBlockRequest(
            book_id="bk_one", page_id="pg_1", block_id="blk_1",
            body="Allowed local edit", expected_revision=1,
        ))
    assert canonical.engine.load_page("bk_one", "pg_1").blocks[0].payload["body"] == "Canonical"
    with as_user("teacher", role="teacher"):
        registry.set_book_visibility("bk_one", "private")
    with as_user("reader", role="teacher"):
        assert book_access.resolve_book("book:bk_one") is None
        assert book_access.resolve_book("bk_one").can_edit


def test_pending_binding_repairs_in_owner_workspace_and_never_rebinds(as_user, make_kb, monkeypatch):
    from deeptutor.knowledge import registry as kbs
    from deeptutor.multi_user.context import get_current_user
    from deeptutor.multi_user.knowledge_access import current_kb_manager
    from deeptutor.services.workspace import ContentWorkspaceService
    from deeptutor.services.workspace.context import workspace_context

    original = registry.resolve_kb_links
    with as_user("owner", role="teacher"):
        wid = ContentWorkspaceService().create_workspace("Physics")["workspace_id"]
        with workspace_context(wid):
            manager = current_kb_manager()
            make_kb(manager, "physics")
            book = _write(refs=["physics"])
            monkeypatch.setattr(registry, "resolve_kb_links", lambda _refs: (_ for _ in ()).throw(
                registry.BookKbResolutionError("temporary")
            ))
            record = book_access.ensure_book_registered(book)
            assert not registry.kb_links_initialized(book.id)
            # Public legacy row can be encountered while bindings are pending.
            with registry._write() as conn:
                conn.execute("UPDATE book_registry SET visibility='public' WHERE book_id=?", (book.id,))
    seen = []

    def resolve(refs):
        seen.append((get_current_user().id, current_kb_manager().base_dir))
        return original(refs)

    monkeypatch.setattr(registry, "resolve_kb_links", resolve)
    with as_user("reader", role="teacher"):
        reader_manager = current_kb_manager()
        make_kb(reader_manager, "physics")
        resolved = book_access.resolve_book(record.ref)
        assert resolved.record.created_by == "owner"
        assert registry.kb_links_initialized(book.id)
        assert seen and all(uid == "owner" and str(root) == str(manager.base_dir) for uid, root in seen)
    fixed = registry.book_kb_links(book.id)["physics"]
    with as_user("root", role="admin"):
        kbs.unregister_kb(fixed)
        replacement = kbs.register_kb(base_dir=manager.base_dir, name="physics",
                                      created_by="owner", kind="uploaded")
    assert replacement.kb_id != fixed
    with as_user("reader"):
        book_access.resolve_book(record.ref)
        assert registry.book_kb_links(book.id)["physics"] == fixed
        payload = router._book_payload(book, book_access.resolve_book(record.ref))
        assert payload["kb_refs"] == [f"kb:{fixed}"]


def test_partial_pending_repair_keeps_confirmed_missing_and_fixed_links(as_user, make_kb, monkeypatch):
    from deeptutor.multi_user.knowledge_access import current_kb_manager

    original = registry.resolve_kb_links

    def partial(refs):
        if refs == ["later"]:
            raise registry.BookKbResolutionError("temporary")
        return original(refs)

    with as_user("owner", role="teacher"):
        manager = current_kb_manager()
        make_kb(manager, "physics")
        make_kb(manager, "later")
        monkeypatch.setattr(registry, "resolve_kb_links", partial)
        book = _write(refs=["physics", "gone", "later"])
        record = book_access.ensure_book_registered(book)
        initial = registry.book_kb_links(book.id)
        assert initial["physics"] and initial["gone"] == "" and "later" not in initial
        assert registry.has_kb_links(book.id) and not registry.kb_links_initialized(book.id)
        make_kb(manager, "gone")
        monkeypatch.setattr(registry, "resolve_kb_links", original)
        book_access.resolve_book(record.ref)
        repaired = registry.book_kb_links(book.id)
        assert registry.kb_links_initialized(book.id)
        assert repaired["later"] and repaired["physics"] == initial["physics"]
        assert repaired["gone"] == ""


def test_creation_never_adopts_existing_identity_or_changes_its_intent(as_user):
    with as_user("owner", role="teacher"):
        book = _write()
        original = book_access.ensure_book_registered(book)
        with pytest.raises(registry.BookRegistryError):
            book_access.prepare_new_book(book, get_book_storage())
        assert registry.get_book(book.id) == original and _choice(book.id) is None
    with as_user("other", role="teacher"):
        with pytest.raises(registry.BookRegistryError):
            book_access.prepare_new_book(book, get_book_storage())
        assert registry.get_book(book.id) == original and _choice(book.id) is None


@pytest.mark.asyncio
async def test_creation_identity_intent_and_bindings_precede_manifest_and_ready(
    as_user, proposal, monkeypatch, make_kb,
):
    from deeptutor.multi_user.knowledge_access import current_kb_manager

    with as_user("teacher", role="teacher"):
        make_kb(current_kb_manager(), "physics")
        engine = BookEngine()
        original_save = engine.storage.save_book
        seen = []

        def save(book):
            record = registry.get_book(book.id)
            assert record.created_by == "teacher" and record.visibility == "private"
            assert _choice(book.id) == "private"
            assert registry.kb_links_initialized(book.id)
            assert registry.book_kb_links(book.id)["physics"]
            seen.append(book.id)
            original_save(book)

        monkeypatch.setattr(engine.storage, "save_book", save)
        bus = StreamBus()
        book, _ = await engine.create_book(user_intent="Waves", knowledge_bases=["physics"], stream=bus)
        assert seen == [book.id]
        assert any(e.metadata.get("kind") == "proposal_ready" for e in bus._history)
        assert registry.list_book_references(book.id) == []


def test_new_identity_and_intent_roll_back_together(as_user, monkeypatch):
    from deeptutor.core import visibility_intent

    original = visibility_intent.record_choice

    def fail(conn, *args, **kwargs):
        original(conn, *args, **kwargs)
        raise RuntimeError("intent write failure")

    monkeypatch.setattr(visibility_intent, "record_choice", fail)
    with as_user("teacher", role="teacher"):
        with pytest.raises(RuntimeError, match="intent write"):
            book_access.prepare_new_book(Book(id="bk_new"), get_book_storage())
    assert registry.get_book("bk_new") is None and _choice("bk_new") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["boundary", "identity", "binding"])
async def test_failed_creation_emits_no_ready_and_can_retry(as_user, proposal, monkeypatch, failure):
    with as_user("root", role="admin"):
        engine = BookEngine()
        target, attribute = {
            "boundary": (registry_backfill, "ensure_shelf_creation_boundary"),
            "identity": (registry, "register_new_book"),
            "binding": (registry, "initialize_book_kb_links"),
        }[failure]
        original = getattr(target, attribute)

        def fail(*_args, **_kwargs):
            raise RuntimeError("synthetic creation failure")

        monkeypatch.setattr(target, attribute, fail)
        bus = StreamBus()
        with pytest.raises(RuntimeError, match="synthetic creation failure"):
            await engine.create_book(user_intent="Waves", stream=bus)
        assert engine.list_books() == []
        assert not any(e.metadata.get("kind") == "proposal_ready" for e in bus._history)
        monkeypatch.setattr(target, attribute, original)
        book, _ = await engine.create_book(user_intent="Waves", stream=bus)
        assert registry.get_book(book.id).created_by == "local-admin"
        assert _choice(book.id) == "private"
        assert any(e.metadata.get("kind") == "proposal_ready" for e in bus._history)


@pytest.mark.asyncio
async def test_post_registration_save_failure_leaves_no_orphan_registry_row(
    as_user, proposal, monkeypatch,
):
    """If save_book fails after prepare_new_book, the registry row must be compensated."""
    with as_user("root", role="admin"):
        engine = BookEngine()
        original_save = engine.storage.save_book
        captured_id = []

        def fail_save(book):
            captured_id.append(book.id)
            raise OSError("synthetic disk failure")

        monkeypatch.setattr(engine.storage, "save_book", fail_save)
        bus = StreamBus()
        with pytest.raises(OSError, match="synthetic disk failure"):
            await engine.create_book(user_intent="Waves", stream=bus)
        assert captured_id, "save_book must have been called"
        book_id = captured_id[0]
        # The orphan registry row should not exist after the failure
        assert registry.get_book(book_id) is None, "orphan registry row left behind"
        assert _choice(book_id) is None, "orphan visibility_intent left behind"
        # Retry must succeed cleanly
        monkeypatch.setattr(engine.storage, "save_book", original_save)
        book, _ = await engine.create_book(user_intent="Waves", stream=bus)
        assert registry.get_book(book.id).created_by == "local-admin"
        assert _choice(book.id) == "private"


@pytest.mark.asyncio
async def test_repaired_storage_creation_freezes_d22_without_scanning(
    as_user, proposal, monkeypatch, tmp_path, seed_user,
):
    from deeptutor.multi_user import identity

    seed_user("root", role="admin")
    seed_user("reader")
    users = identity.load_users()
    users["reader"]["book_permission"] = {"default": "read"}
    identity._write_users(users)
    with as_user("root", role="admin"):
        engine = BookEngine()
        _write("bk_legacy")
        real_path = registry.DB_PATH_OVERRIDE
        unavailable = tmp_path / "not-a-directory"
        unavailable.write_text("synthetic unavailable storage")
        monkeypatch.setattr(registry, "DB_PATH_OVERRIDE", unavailable / "registry.sqlite3")
        assert registry_backfill.backfill_book_registry()["promoted"] == 0
        monkeypatch.setattr(registry, "DB_PATH_OVERRIDE", real_path)
        assert registry.get_meta(registry_backfill._INVENTORY_KEY) is None

        def no_scan(*_args, **_kwargs):
            raise AssertionError("Creation must not scan or publish legacy content")

        with monkeypatch.context() as scoped:
            scoped.setattr(registry_backfill, "_resume_inventory", no_scan)
            book, _ = await engine.create_book(user_intent="Waves")
        fence = json.loads(registry.get_meta(registry_backfill._INVENTORY_KEY))
        manifest = engine.storage.path_service.get_book_manifest_file(book.id)
        assert fence["started_ns"] < manifest.stat().st_ctime_ns
        assert registry.get_book("bk_legacy") is None
        assert _choice(book.id) == "private"
        report = registry_backfill.backfill_book_registry()
        assert report["promoted"] == 1
        assert registry.get_book("bk_legacy").visibility == "public"
        assert registry.get_book(book.id).visibility == "private"


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke", ["visibility", "reference"])
async def test_fanout_rechecks_bound_authority_after_withdrawal(as_user, revoke, monkeypatch):
    with as_user("owner", role="teacher"):
        _write()
        record = book_access.resolve_book("bk_one").record
        if revoke == "visibility":
            registry.set_book_visibility(record.book_id, "public")
        else:
            registry.replace_book_references("course", "c1", [record.book_id], created_by="owner")
    registry.register_book_reference_reader("course", lambda _ref, uid: uid == "reader")
    frames = []

    async def send(frame):
        frames.append(frame)

    now = [1000.0]
    monkeypatch.setattr(router, "_stream_auth_clock", lambda: now[0])
    with as_user("reader"):
        resolved = book_access.resolve_book(record.ref)
        fanout = router._SocketFanout(send)
        fanout.attach_book(record.book_id, resolved)
        stream = get_book_stream(record.book_id, storage=resolved.engine.storage)
        try:
            await stream.book_event("before", {})
            await _forwarded()
            assert [f["metadata"]["kind"] for f in frames] == ["before"]
            with as_user("owner", role="teacher"):
                if revoke == "visibility":
                    registry.set_book_visibility(record.book_id, "private")
                else:
                    registry.remove_book_references("course", "c1")
            # The cached authorization expires after its TTL (B3-1).
            now[0] += router._STREAM_AUTH_TTL
            await stream.book_event("private", {})
            await _forwarded()
            assert [f["metadata"]["kind"] for f in frames] == ["before"]
        finally:
            await fanout.close()


@pytest.mark.asyncio
async def test_same_id_fanout_replay_and_engine_deletion_are_location_bound(as_user):
    with as_user("owner", role="teacher"):
        _write()
        canonical = book_access.resolve_book("bk_one")
        registry.set_book_visibility("bk_one", "public")
    with as_user("reader"):
        _write(title="Local")
        local = book_access.resolve_book("bk_one")
        shared = book_access.resolve_book("book:bk_one")
        assert shared.engine is canonical.engine
        canonical_bus = get_book_bus("bk_one", storage=canonical.engine.storage)
        local_bus = get_book_bus("bk_one", storage=local.engine.storage)
        assert canonical_bus is get_book_bus("book:bk_one", storage=shared.engine.storage)
        assert local_bus is not canonical_bus
        await get_book_stream("bk_one", storage=canonical.engine.storage).book_event("canonical", {})
        await get_book_stream("bk_one", storage=local.engine.storage).book_event("local", {})
        local_frames, shared_frames = [], []

        async def send_local(frame):
            local_frames.append(frame)

        async def send_shared(frame):
            shared_frames.append(frame)

        local_fanout, shared_fanout = router._SocketFanout(send_local), router._SocketFanout(send_shared)
        try:
            local_fanout.attach_book("bk_one", local)
            shared_fanout.attach_book("bk_one", shared)
            await _forwarded()
            assert [f["metadata"]["kind"] for f in local_frames] == ["local"]
            assert [f["metadata"]["kind"] for f in shared_frames] == ["canonical"]
            assert local.engine.delete_book("bk_one")
            assert local_bus._closed and not canonical_bus._closed
            await get_book_stream("bk_one", storage=canonical.engine.storage).book_event("still-live", {})
            await _forwarded()
            assert [f["metadata"]["kind"] for f in shared_frames] == ["canonical", "still-live"]
            assert canonical.engine.load_book("bk_one") is not None
        finally:
            await local_fanout.close()
            await shared_fanout.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["compile", "regenerate", "insert"])
async def test_compiler_quiz_uses_actual_store_under_foreign_ambient_scope(as_user, monkeypatch, action):
    from deeptutor.agents.question.pipeline import QuestionPipeline
    from deeptutor.book.compiler import BookCompiler, CompilerOptions
    from deeptutor.book.models import Chapter, Spine
    from deeptutor.book.streaming import BookStream

    buses = []

    async def run(_self, **kwargs):
        bus = kwargs["stream"]
        buses.append(bus)
        await BookStream(bus).book_event("quiz_progress", {})
        return {"summary": {"results": [{"qa_pair": {"question_id": "q1", "question": "Waves?"}}]}}

    monkeypatch.setattr(QuestionPipeline, "__init__", lambda self, **kwargs: None)
    monkeypatch.setattr(QuestionPipeline, "run", run)
    with as_user("owner"):
        store = get_book_storage()
        book = _write()
        chapter = Chapter(title="Waves")
        page = Page(id="pg_quiz", book_id=book.id, chapter_id=chapter.id,
                    blocks=[Block(type=BlockType.QUIZ)])
        store.save_spine(Spine(book_id=book.id, chapters=[chapter]))
        store.save_page(page)
        compiler = BookCompiler(storage=store, options=CompilerOptions(phase=1))
        engine = BookEngine(storage=store)
    with as_user("reader"):
        _write(title="Collision")
        foreign_bus = get_book_bus(book.id)
        canonical_bus = get_book_bus(book.id, storage=store)
        if action == "compile":
            compiled = await compiler.compile_page(
                book_id=book.id, chapter=chapter, page=page, stream=BookStream(canonical_bus),
            )
            block = compiled.blocks[0]
        elif action == "regenerate":
            block = await engine.regenerate_block(
                book_id=book.id, page_id=page.id, block_id=page.blocks[0].id,
            )
        else:
            block = await engine.insert_block(
                book_id=book.id, page_id=page.id, block_type=BlockType.QUIZ,
            )
        assert block.status == BlockStatus.READY
        assert buses == [canonical_bus]
        assert any(e.metadata.get("kind") == "quiz_progress" for e in canonical_bus._history)
        assert not foreign_bus._history
