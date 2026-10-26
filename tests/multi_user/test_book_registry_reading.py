"""Book content and retrieval use the same authorized canonical source."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from deeptutor.book import registry
from deeptutor.book.context import build_book_context
from deeptutor.book.models import Block, BlockStatus, BlockType, Book, Chapter, Page, PageStatus, Spine
from deeptutor.book.storage import get_book_storage
from deeptutor.knowledge import registry as kbs
from deeptutor.learning.topic_materials import _load_book_materials
from deeptutor.multi_user import book_access
from deeptutor.multi_user.context import get_current_user
from deeptutor.multi_user.knowledge_access import current_kb_manager, resolve_for_rag


def _write(book_id="bk_public", *, title="Standing waves", refs=()):
    storage = get_book_storage()
    storage.save_book(Book(id=book_id, title=title, page_count=1, knowledge_bases=list(refs)))
    storage.save_spine(Spine(
        book_id=book_id, chapters=[Chapter(id="ch_1", title="Waves", page_ids=["pg_1"])],
    ))
    storage.save_page(Page(
        id="pg_1", book_id=book_id, chapter_id="ch_1", title="Page", status=PageStatus.READY,
        blocks=[Block(id="blk_1", type=BlockType.TEXT, status=BlockStatus.READY,
                      payload={"body": title})],
    ))


def _refs(book_id="book:bk_public"):
    return [{"book_id": book_id, "page_ids": ["pg_1"]}]


@pytest.fixture
def shared_book(as_user, make_kb):
    with as_user("teacher", role="teacher"):
        manager = current_kb_manager()
        make_kb(manager, "answers")
        kb = kbs.register_kb(
            base_dir=manager.base_dir, name="answers", kind="uploaded", created_by="teacher",
        )
        _write(refs=[kb.ref])
        _write("bk_private", title="Secret")
        book_access.accessible_books()
        registry.set_book_visibility(
            "bk_public", "public", kb_choices={kb.kb_id: "reference_only"},
        )
    return kb


def test_context_and_topic_materials_resolve_before_stripping(shared_book, as_user):
    with as_user("reader", role="student"):
        _write(title="Local collision")
        _write("bk_private", title="Private collision")
        _write("unknown", title="Unregistered local book")
        result = build_book_context(_refs() + _refs("book:bk_private"))
        assert "Standing waves" in result.text and "Local collision" not in result.text
        assert result.references[0]["book_id"] == "book:bk_public"
        assert result.warnings == ["book_not_found:book:bk_private"]
        assert "Local collision" in build_book_context(_refs("bk_public")).text
        materials = _load_book_materials("book:bk_public", "Waves", 100_000)
        assert materials[0].available
        assert materials[0].sid == "bk-bk_public-ch_1"
        assert "Standing waves" in materials[0].full_text
        assert "Local collision" not in materials[0].full_text
        assert not _load_book_materials("book:bk_private", "Secret", 100_000)[0].available
        assert not _load_book_materials("book:unknown", "Unknown", 100_000)[0].available


def test_legacy_shared_context_and_explicit_storage(shared_book, as_user):
    with as_user("reader", role="student"):
        assert "Standing waves" in build_book_context(_refs("bk_public")).text
        _write(title="Local collision")
        result = build_book_context(_refs(), storage=get_book_storage())
        assert "Local collision" in result.text


def test_scope_does_not_authorize_foreign_identity_from_local_collision(shared_book, as_user):
    with as_user("reader", role="student"):
        _write(title="Local collision", refs=[shared_book.ref])
        with book_access.book_reading_scope(_refs("bk_public")) as refs:
            assert refs == []
            assert registry.active_books.get() == frozenset()
            assert not kbs.can_read(shared_book, get_current_user())
        with book_access.book_reading_scope(_refs()) as refs:
            assert refs == [shared_book.ref]
            assert resolve_for_rag(shared_book.ref).base_dir == Path(shared_book.base_dir)
        assert not kbs.can_read(shared_book, get_current_user())


@pytest.mark.parametrize("exception", [False, True])
def test_scope_restores_nested_bindings_on_exit(shared_book, as_user, exception):
    with as_user("reader", role="student"):
        user = get_current_user()
        with book_access.book_reading_scope(_refs()):
            with pytest.raises(RuntimeError) if exception else nullcontext():
                with book_access.book_reading_scope(_refs("book:bk_private")) as refs:
                    assert refs == []
                    assert kbs.can_read(shared_book, user)
                    if exception:
                        raise RuntimeError("synthetic scope failure")
            assert kbs.can_read(shared_book, user)
        assert registry.active_books.get() == frozenset()
        with pytest.raises(HTTPException):
            resolve_for_rag(shared_book.ref)


@pytest.mark.parametrize("revoke", ["book", "kb"])
def test_retrieval_rechecks_revocation_inside_scope(shared_book, as_user, revoke):
    with as_user("reader", role="student"):
        with book_access.book_reading_scope(_refs()):
            assert resolve_for_rag(shared_book.ref)
            with as_user("teacher", role="teacher"):
                if revoke == "book":
                    registry.set_book_visibility("bk_public", "private")
                else:
                    kbs.unregister_kb(shared_book.kb_id)
            with pytest.raises(HTTPException):
                resolve_for_rag(shared_book.ref)
        assert registry.active_books.get() == frozenset()


def test_only_usable_fixed_bindings_are_returned(as_user, make_kb, monkeypatch):
    original = registry.resolve_kb_links

    def pending(refs):
        if refs == ["pending"]:
            raise registry.BookKbResolutionError("synthetic pending resolution")
        return original(refs)

    with as_user("teacher", role="teacher"):
        manager = current_kb_manager()
        for name in ("usable", "pending", "deleted", "unavailable"):
            make_kb(manager, name)
        monkeypatch.setattr(registry, "resolve_kb_links", pending)
        _write(refs=["usable", "pending", "missing", "deleted", "unavailable"])
        book_access.resolve_book("bk_public")
        links = registry.book_kb_links("bk_public")
        assert links["missing"] == "" and "pending" not in links
        # A legacy public record can precede complete binding migration.
        with registry._write() as conn:
            conn.execute("UPDATE book_registry SET visibility='public' WHERE book_id='bk_public'")
        kbs.replace_references(
            "book", "bk_public",
            [(links["usable"], None), (links["unavailable"], None)], created_by="teacher",
        )
        kbs.unregister_kb(links["deleted"])
        replacement = kbs.register_kb(
            base_dir=manager.base_dir, name="deleted", kind="uploaded", created_by="teacher",
        )
        # A registered but physically unavailable KB must not mount either.
        manager.config["knowledge_bases"].pop("unavailable")
        manager._save_config()
    with as_user("reader", role="student"):
        for name in ("pending", "missing", "deleted", "unavailable"):
            make_kb(current_kb_manager(), name)
        with book_access.book_reading_scope(_refs()) as refs:
            assert refs == [f"kb:{links['usable']}"]
            assert replacement.ref not in refs
        assert registry.book_kb_links("bk_public") == links
        assert not registry.kb_links_initialized("bk_public")


def test_reference_reader_revocation_and_actor_binding(shared_book, as_user):
    with as_user("teacher", role="teacher"):
        registry.set_book_visibility("bk_public", "private")
        registry.replace_book_references("course", "lesson", ["bk_public"], created_by="teacher")
    registry.register_book_reference_reader("course", lambda _ref, uid: uid == "reader")
    with as_user("reader", role="student"):
        assert "Standing waves" in _load_book_materials("book:bk_public", "Waves", 100_000)[0].full_text
        with book_access.book_reading_scope(_refs()) as refs:
            assert refs == [shared_book.ref]
            with as_user("other", role="student"):
                with pytest.raises(HTTPException):
                    resolve_for_rag(shared_book.ref)
            registry.remove_book_references("course", "lesson")
            with pytest.raises(HTTPException):
                resolve_for_rag(shared_book.ref)
        assert not _load_book_materials("book:bk_public", "Waves", 100_000)[0].available


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["reading", "plain", "forged", "collision", "revoked", "exception", "revoke_during"],
)
async def test_learner_preparer_executor_composition_and_rag(
    shared_book, as_user, make_kb, seed_user, tmp_path, monkeypatch, case,
):
    from deeptutor.agents.loop.pipeline import AgenticLoopPipeline
    from deeptutor.core.stream import StreamEvent, StreamEventType
    from deeptutor.multi_user.grants import save_grant
    from deeptutor.multi_user.paths import get_admin_path_service
    from deeptutor.services.session.sqlite_store import SQLiteSessionStore
    from deeptutor.services.session.turn_runtime import TurnRuntimeManager
    from deeptutor.services.skill.service import SkillService
    from deeptutor.tools.builtin import RAGTool

    seed_user("admin", role="admin")
    learner = seed_user("learner", role="student")
    save_grant(learner["id"], {
        "learning_policy": {
            "age_band": "9-12", "locked_persona": "teacher",
            "allowed_capabilities": ["chat"], "allowed_surfaces": ["chat", "reading"],
        },
    })
    persona_dir = get_admin_path_service().get_workspace_dir() / "personas" / "teacher"
    persona_dir.mkdir(parents=True, exist_ok=True)
    (persona_dir / "PERSONA.md").write_text("Synthetic teacher.", encoding="utf-8")
    captured = {}

    class History:
        def __init__(self, *_args, **_kwargs):
            pass

        async def build(self, **_kwargs):
            return SimpleNamespace(
                conversation_history=[], conversation_summary="", context_text="",
                token_count=0, budget=0,
            )

    class Retrieval:
        def __init__(self, *, kb_base_dir, provider):
            assert Path(kb_base_dir) == Path(shared_book.base_dir)

        async def search(self, *, kb_name, **_kwargs):
            assert kb_name == shared_book.dir_name
            captured["retrieved"] = True
            return {"content": "Synthetic canonical passage"}

    class Execution:
        async def execute(self, context):
            captured["context"] = context
            pipeline = AgenticLoopPipeline(language="en")
            captured["tools"] = pipeline._compose_enabled_tools(context)
            if case in {"reading", "exception", "revoke_during"}:
                assert context.knowledge_bases == [shared_book.ref]
                assert "rag" in captured["tools"]
                result = await RAGTool().execute(query="waves", kb_name=shared_book.ref)
                assert result.success and result.content == "Synthetic canonical passage"
                with pytest.raises(HTTPException):
                    await RAGTool().execute(query="waves", kb_name=unrelated.ref)
                if case == "revoke_during":
                    with as_user("teacher", role="teacher"):
                        registry.set_book_visibility("bk_public", "private")
                    with pytest.raises(HTTPException):
                        await RAGTool().execute(query="waves", kb_name=shared_book.ref)
                if case == "exception":
                    raise RuntimeError("synthetic execution failure")
            else:
                assert context.knowledge_bases == []
                assert "rag" not in captured["tools"]
                with pytest.raises(HTTPException):
                    await RAGTool().execute(query="waves", kb_name=shared_book.ref)
            yield StreamEvent(type=StreamEventType.CONTENT, content="ok")
            yield StreamEvent(type=StreamEventType.DONE, source="chat")

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("deeptutor.services.config.runtime_settings.load_system_settings", lambda: {})
    monkeypatch.setattr("deeptutor.services.llm.config.get_llm_config", lambda: SimpleNamespace())
    monkeypatch.setattr("deeptutor.agents.loop.pipeline.get_llm_config", lambda: SimpleNamespace())
    monkeypatch.setattr(
        "deeptutor.multi_user.model_access.apply_allowed_llm_selection", lambda _selection: {},
    )
    monkeypatch.setattr("deeptutor.services.session.context_builder.ContextBuilder", History)
    monkeypatch.setattr("deeptutor.tools.rag_tool.RAGService", Retrieval)
    monkeypatch.setattr(
        "deeptutor.services.memory.get_memory_store",
        lambda: SimpleNamespace(read_l3_concat=lambda: "", emit=noop),
    )
    monkeypatch.setattr(
        "deeptutor.services.skill.get_skill_service",
        lambda: SkillService(root=tmp_path / "skills", builtin_root=None),
    )
    with as_user("teacher", role="teacher"):
        make_kb(current_kb_manager(), "unrelated")
        unrelated = kbs.register_kb(
            base_dir=current_kb_manager().base_dir, name="unrelated",
            kind="uploaded", created_by="teacher",
        )
    with as_user(learner["id"], role="student"):
        if case in {"reading", "collision"}:
            _write(title="Local collision", refs=[shared_book.ref])
        references = (
            [] if case == "plain" else
            _refs("book:bk_private") + _refs("book:unknown") if case == "forged" else
            _refs("bk_public") if case == "collision" else _refs()
        )
        runtime = TurnRuntimeManager(
            SQLiteSessionStore(tmp_path / "turns.sqlite3"), turn_engine=Execution(),
        )
        original_run = runtime._run_turn

        async def observed_run(execution):
            # Real request_preparer has already applied the ordinary policy.
            captured["filtered"] = list(execution.payload["knowledge_bases"])
            if case == "revoked":
                with as_user("teacher", role="teacher"):
                    registry.set_book_visibility("bk_public", "private")
            try:
                await original_run(execution)
            finally:
                # Check cleanup in the executing task, not just its parent's
                # independent ContextVar copy.
                captured["after_scope"] = registry.active_books.get()
                captured["after_permission"] = kbs.can_read(shared_book, get_current_user())

        monkeypatch.setattr(runtime, "_run_turn", observed_run)
        try:
            session, turn = await runtime.start_turn({
                "content": "Explain waves", "capability": "chat", "language": "en",
                "tools": ["web_search"], "knowledge_bases": [shared_book.ref, unrelated.ref],
                "book_references": references, "attachments": [], "config": {},
                "llm_selection": {"profile_id": "synthetic", "model_id": "synthetic"},
            })
            async with asyncio.timeout(10):
                events = [
                    event async for event in runtime.subscribe_turn(turn["id"], after_seq=0)
                ]
                execution = runtime._executions.get(turn["id"])
                if execution and execution.task:
                    await asyncio.shield(execution.task)
            assert captured["filtered"] == []
            assert "context" in captured
            assert captured["context"].enabled_tools == []
            if case == "reading":
                assert "Standing waves" in captured["context"].metadata["book_context"]
                assert "Local collision" not in captured["context"].metadata["book_context"]
                assert captured["context"].metadata["book_references"] == _refs()
            assert captured["after_scope"] == frozenset()
            assert captured["after_permission"] is False
            assert bool(captured.get("retrieved")) == (
                case in {"reading", "exception", "revoke_during"}
            )
            errors = [event for event in events if event["type"] == "error"]
            assert bool(errors) == (case == "exception")
            if errors:
                assert "synthetic execution failure" in errors[0]["content"]
            stored = await runtime.store.get_session(session["id"])
            assert stored["preferences"]["knowledge_bases"] == []
        finally:
            await runtime.close()
