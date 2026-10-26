"""A tutoring turn on a teacher path studies; it never redesigns, and closes when revoked.

R8: such a turn is taught from the path's own knowledge bases only, enforced
where retrieval executes — an explicit ``rag(kb_name=...)`` naming any other
library is refused, also when the path cites none. Every path switch re-runs
the same admission gate and re-points that restriction; leaving a teacher path
drops it. These tests drive the real pipeline entry, the real switch tool, the
real ``RAGTool`` and the real registries; only the search backend is stubbed.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from deeptutor.agents.loop.pipeline import AgenticLoopPipeline
from deeptutor.book import registry as book_registry
from deeptutor.book.models import Book
from deeptutor.book.storage import get_book_storage
from deeptutor.capabilities.mastery.binding import PathBindingError, rebind_active_path
from deeptutor.capabilities.mastery.loop import _path_binder
from deeptutor.capabilities.mastery.pipeline import MasteryLoopPipeline
from deeptutor.core.context import UnifiedContext
from deeptutor.knowledge import registry as kbs
from deeptutor.learning.models import TopicMetadata, TopicSource, TopicSourceKind
from deeptutor.learning.service import LearningService
from deeptutor.learning.storage import LearningStore
from deeptutor.multi_user import book_access
from deeptutor.multi_user.knowledge_access import current_kb_manager, resolve_for_rag
from deeptutor.multi_user.retrieval_scope import (
    TEACHER_PATH_RETRIEVAL_DENIED,
    current_retrieval_scope,
)
from deeptutor.teaching.instances import INSTANCE_UNAVAILABLE
from deeptutor.teaching.service import TeachingService
from deeptutor.tools.builtin import RAGTool
from deeptutor.tools.mastery_tool import MasteryLeaveTool, MasterySwitchTool


class _Stream:
    def __init__(self) -> None:
        self.errors: list[tuple[str, dict]] = []

    async def error(self, message, source="", stage="", metadata=None):
        self.errors.append((message, dict(metadata or {})))


def _probe_loop(monkeypatch, seen: dict) -> None:
    async def _loop(self, context, _stream):
        seen["mode"] = context.metadata.get("mastery_session_mode")
        seen["kbs"] = list(context.knowledge_bases)
        return {}

    monkeypatch.setattr(AgenticLoopPipeline, "run", _loop)
    monkeypatch.setattr("deeptutor.capabilities.setup.binding._intro_pending", lambda _c: False)


def _context(path_id: str, mode: str, knowledge_bases: tuple[str, ...] = ()) -> UnifiedContext:
    return UnifiedContext(
        session_id="s1",
        enabled_tools=[],
        knowledge_bases=list(knowledge_bases),
        metadata={
            "mastery_mode": True,
            "mastery_path_id": path_id,
            "mastery_session_mode": mode,
            "turn_id": "t1",
        },
    )


# ---------------------------------------------------------------------------
# Brief: mode and admission at the turn entry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_turn_on_a_teacher_path_runs_in_study_mode(sara_instance, as_account, monkeypatch):
    seen: dict = {}
    _probe_loop(monkeypatch, seen)
    stream = _Stream()

    with as_account("sara"):
        await MasteryLoopPipeline(language="en").run(
            _context(sara_instance.path_id, "outline"), stream
        )

    assert seen == {"mode": "study", "kbs": []}
    assert stream.errors == []


@pytest.mark.asyncio
async def test_a_teacher_path_ignores_the_learners_own_kb_selection(
    sara_instance, as_account, monkeypatch
):
    # R8: tutoring and questions on a teacher path use only the path's materials.
    seen: dict = {}
    _probe_loop(monkeypatch, seen)

    with as_account("sara"):
        await MasteryLoopPipeline(language="en").run(
            _context(sara_instance.path_id, "study", ("user:kb:my-notes",)), _Stream()
        )

    assert seen["kbs"] == []


@pytest.mark.asyncio
async def test_a_revoked_teacher_path_closes_the_turn(
    sara_instance, as_account, teaching_users, monkeypatch
):
    seen: dict = {}
    _probe_loop(monkeypatch, seen)
    with as_account("tina"):
        TeachingService().revoke(sara_instance.template_id, teaching_users["sara"]["id"])
    stream = _Stream()

    with as_account("sara"):
        await MasteryLoopPipeline(language="en").run(
            _context(sara_instance.path_id, "study"), stream
        )

    assert seen == {}
    assert stream.errors[0][1]["code"] == "teacher_path_unavailable"
    assert current_retrieval_scope() is None


@pytest.mark.asyncio
async def test_switching_into_a_revoked_teacher_path_is_refused(
    sara_instance, as_account, teaching_users
):
    with as_account("tina"):
        TeachingService().revoke(sara_instance.template_id, teaching_users["sara"]["id"])

    with as_account("sara"):
        with pytest.raises(PathBindingError):
            await rebind_active_path(
                path_id=sara_instance.path_id,
                session_id="s1",
                turn_id="t1",
                bind_turn=None,
            )


# ---------------------------------------------------------------------------
# R8 at retrieval execution, and path switches
# ---------------------------------------------------------------------------


@pytest.fixture
def libraries(as_account, make_kb, teaching_users, monkeypatch):
    """Real plan B registrations: two public course KBs, one unrelated public KB
    and Sara's own private notes. The search backend is the only stub."""
    searched: list[str] = []

    class Retrieval:
        def __init__(self, *, kb_base_dir, provider):
            self.kb_base_dir = kb_base_dir

        async def search(self, *, query, kb_name, **_kwargs):
            searched.append(kb_name)
            return {"content": f"passage from {kb_name}"}

    monkeypatch.setattr("deeptutor.tools.rag_tool.RAGService", Retrieval)

    def library(owner: str, name: str, visibility: str):
        with as_account(owner):
            manager = current_kb_manager()
            make_kb(manager, name, text=f"Synthetic {name} material")
            return kbs.register_kb(
                base_dir=manager.base_dir,
                name=name,
                kind="uploaded",
                created_by=teaching_users[owner]["id"],
                visibility=visibility,
            )

    return SimpleNamespace(
        calculus=library("tina", "calculus", "public"),
        algebra=library("tina", "algebra", "public"),
        history=library("tom", "history", "public"),
        notes=library("sara", "notes", "private"),
        searched=searched,
    )


def _kb_source(record, index: int = 0) -> TopicSource:
    return TopicSource(
        id=f"src_{record.name}",
        kind=TopicSourceKind.KNOWLEDGE_BASE,
        source_id=record.ref,
        label=record.name,
        position=index,
    )


@pytest.fixture
def courses(libraries, teacher_draft, publish_template, as_account):
    """Tina publishes two paths to Sara (A cites calculus, B algebra); Sara also
    keeps a personal path P citing her own notes."""
    from deeptutor.api.routers import mastery_path

    teacher_draft(path_id="topic_calculus", sources=(_kb_source(libraries.calculus),))
    teacher_draft(path_id="topic_algebra", sources=(_kb_source(libraries.algebra),))
    a = publish_template(path_id="topic_calculus", students=("sara",))["template"]["id"]
    b = publish_template(path_id="topic_algebra", students=("sara",))["template"]["id"]
    with as_account("sara"):
        LearningService(LearningStore()).create_topic(
            "topic_mine",
            name="Mine",
            modules=[],
            metadata=TopicMetadata(path_id="topic_mine", goal="My own"),
            sources=[_kb_source(libraries.notes)],
        )
        topics = asyncio.run(mastery_path.list_topics())["topics"]
    ids = {topic["path_id"] for topic in topics}
    assert {f"assigned_{a}", f"assigned_{b}", "topic_mine"} <= ids
    return SimpleNamespace(
        a=f"assigned_{a}", b=f"assigned_{b}", personal="topic_mine", template_a=a, template_b=b
    )


def _turn(monkeypatch, body) -> None:
    """Run the real mastery entry; ``body(context)`` stands in for the model loop."""

    async def _loop(self, context, _stream):
        await body(context)
        return {}

    monkeypatch.setattr(AgenticLoopPipeline, "run", _loop)
    monkeypatch.setattr("deeptutor.capabilities.setup.binding._intro_pending", lambda _c: False)

    async def _forget(_session_id, _path_id):
        return None

    # The session store is not part of these turns.
    monkeypatch.setattr("deeptutor.capabilities.mastery.binding._remember_on_session", _forget)


async def _rag(kb_ref: str, **selectors):
    return await RAGTool().execute(query="what is a limit", kb_name=kb_ref, **selectors)


async def _switch(context, path_id: str):
    return await MasterySwitchTool().execute(
        path_id=path_id,
        _mastery_path_id=context.metadata["mastery_path_id"],
        _session_id="s1",
        _turn_id="t1",
        _bind_active_path=_path_binder(context),
    )


@pytest.mark.asyncio
async def test_an_explicit_rag_call_cannot_reach_an_unrelated_public_kb(
    courses, libraries, as_account, monkeypatch
):
    seen: dict = {}

    async def body(context):
        seen["kbs"] = list(context.knowledge_bases)
        seen["own"] = await _rag(libraries.calculus.ref)
        seen["unrelated"] = await _rag(libraries.history.ref)
        seen["unrelated_legacy"] = await _rag("user:kb:notes")
        seen["visual"] = await _rag(libraries.history.ref, source_path="a.pdf", page=1)
        with pytest.raises(HTTPException) as denied:
            resolve_for_rag(libraries.history.ref)
        seen["seam"] = denied.value.detail

    _turn(monkeypatch, body)
    with as_account("sara"):
        await MasteryLoopPipeline(language="en").run(
            _context(courses.a, "study", (libraries.history.ref,)), _Stream()
        )

    assert seen["kbs"] == [libraries.calculus.ref]
    assert seen["own"].success and "calculus" in seen["own"].content
    for refused in (seen["unrelated"], seen["unrelated_legacy"], seen["visual"]):
        assert refused.success is False
        assert refused.content == TEACHER_PATH_RETRIEVAL_DENIED
        assert refused.metadata["error_type"] == "teacher_path_scope"
    assert seen["seam"] == TEACHER_PATH_RETRIEVAL_DENIED
    assert libraries.searched == ["calculus"]
    # Nothing outlives the turn.
    assert current_retrieval_scope() is None


@pytest.mark.asyncio
async def test_document_reads_and_inventories_obey_the_allowlist(
    courses, libraries, as_account, monkeypatch
):
    from deeptutor.multi_user.knowledge_access import resolve_kb_document_path
    from deeptutor.tools.builtin import KbFilesTool

    seen: dict = {}

    async def body(_context):
        seen["own_files"] = await KbFilesTool().execute(kb_name=libraries.calculus.ref)
        with pytest.raises(ValueError, match="not accessible"):
            await KbFilesTool().execute(kb_name=libraries.history.ref)
        seen["own_doc"] = resolve_kb_document_path(libraries.calculus.ref, "doc.md")
        seen["other_doc"] = resolve_kb_document_path(libraries.history.ref, "doc.md")

    _turn(monkeypatch, body)
    with as_account("sara"):
        await MasteryLoopPipeline(language="en").run(_context(courses.a, "study"), _Stream())
        outside = resolve_kb_document_path(libraries.history.ref, "doc.md")

    assert seen["own_files"].metadata["total"] == 1
    assert seen["own_doc"] is not None and seen["other_doc"] is None
    assert outside is not None


@pytest.mark.asyncio
async def test_rag_search_with_an_explicit_base_dir_obeys_the_allowlist(
    courses, libraries, as_account, monkeypatch
):
    from deeptutor.tools.rag_tool import rag_search

    seen: dict = {}

    async def body(_context):
        with pytest.raises(ValueError, match="teacher's materials only"):
            await rag_search(
                "q", libraries.history.name, kb_base_dir=libraries.history.base_dir
            )
        seen["own"] = await rag_search(
            "q", libraries.calculus.name, kb_base_dir=libraries.calculus.base_dir
        )

    _turn(monkeypatch, body)
    with as_account("sara"):
        await MasteryLoopPipeline(language="en").run(_context(courses.a, "study"), _Stream())

    assert seen["own"]["content"] == "passage from calculus"


@pytest.mark.asyncio
async def test_a_teacher_path_without_knowledge_bases_retrieves_nothing(
    sara_instance, libraries, as_account, monkeypatch
):
    seen: dict = {}

    async def body(context):
        seen["kbs"] = list(context.knowledge_bases)
        seen["history"] = await _rag(libraries.history.ref)
        seen["notes"] = await _rag(libraries.notes.ref)

    _turn(monkeypatch, body)
    with as_account("sara"):
        await MasteryLoopPipeline(language="en").run(
            _context(sara_instance.path_id, "study", (libraries.notes.ref,)), _Stream()
        )

    assert seen["kbs"] == []
    assert seen["history"].success is False and seen["notes"].success is False
    assert libraries.searched == []


@pytest.mark.asyncio
async def test_personal_paths_and_ordinary_chat_keep_their_retrieval(
    courses, libraries, as_account, monkeypatch
):
    seen: dict = {}

    async def body(context):
        seen["kbs"] = list(context.knowledge_bases)
        seen["history"] = await _rag(libraries.history.ref)
        seen["notes"] = await _rag(libraries.notes.ref)

    _turn(monkeypatch, body)
    with as_account("sara"):
        await MasteryLoopPipeline(language="en").run(
            _context(courses.personal, "outline", (libraries.history.ref,)), _Stream()
        )
        # Ordinary chat: no mastery entry at all.
        chat = await _rag(libraries.history.ref)

    assert seen["kbs"] == [libraries.history.ref, libraries.notes.ref]
    assert seen["history"].success and seen["notes"].success
    assert chat.success
    assert libraries.searched == ["history", "notes", "history"]


@pytest.mark.asyncio
async def test_a_personal_path_never_opens_the_teaching_store(
    courses, libraries, as_account, monkeypatch
):
    def _forbidden(*_args, **_kwargs):
        raise AssertionError("a personal path must not open the teaching store")

    async def body(context):
        await _rag(libraries.notes.ref)
        await _switch(context, courses.personal)

    _turn(monkeypatch, body)
    monkeypatch.setattr("deeptutor.teaching.instances.TeachingStore", _forbidden)
    with as_account("sara"):
        await MasteryLoopPipeline(language="en").run(
            _context(courses.personal, "study"), _Stream()
        )


@pytest.mark.asyncio
async def test_switching_teacher_paths_re_points_the_allowlist(
    courses, libraries, as_account, monkeypatch
):
    seen: dict = {}

    async def body(context):
        seen["switched"] = await _switch(context, courses.b)
        seen["kbs"] = list(context.knowledge_bases)
        seen["b"] = await _rag(libraries.algebra.ref)
        seen["a"] = await _rag(libraries.calculus.ref)

    _turn(monkeypatch, body)
    with as_account("sara"):
        await MasteryLoopPipeline(language="en").run(_context(courses.a, "study"), _Stream())

    assert seen["switched"].success, seen["switched"].content
    assert seen["kbs"] == [libraries.algebra.ref]
    assert seen["b"].success
    assert seen["a"].success is False and seen["a"].content == TEACHER_PATH_RETRIEVAL_DENIED


@pytest.mark.asyncio
async def test_leaving_a_teacher_path_drops_its_restriction(
    courses, libraries, as_account, monkeypatch
):
    seen: dict = {}

    async def body(context):
        seen["before"] = await _rag(libraries.history.ref)
        seen["switched"] = await _switch(context, courses.personal)
        seen["kbs"] = list(context.knowledge_bases)
        seen["history"] = await _rag(libraries.history.ref)
        seen["template"] = current_retrieval_scope().template_id
        # ...and back in: the restriction and the study-only mode return.
        context.metadata["mastery_session_mode"] = "outline"
        seen["back"] = await _switch(context, courses.a)
        seen["mode"] = context.metadata["mastery_session_mode"]
        seen["again"] = await _rag(libraries.history.ref)
        # mastery_leave lands on a scratch path, which is the learner's own.
        seen["left"] = await MasteryLeaveTool().execute(
            _mastery_path_id=context.metadata["mastery_path_id"],
            _session_id="s1",
            _turn_id="t1",
            _bind_active_path=_path_binder(context),
        )
        seen["after_leave"] = await _rag(libraries.history.ref)

    _turn(monkeypatch, body)
    with as_account("sara"):
        await MasteryLoopPipeline(language="en").run(
            _context(courses.a, "study", (libraries.history.ref,)), _Stream()
        )

    assert seen["before"].success is False
    assert seen["switched"].success
    # The learner's own selection comes back with the personal path's sources.
    assert seen["kbs"] == [libraries.history.ref, libraries.notes.ref]
    assert seen["history"].success and seen["template"] == ""
    assert seen["back"].success and seen["mode"] == "study"
    assert seen["again"].success is False
    assert seen["left"].success and seen["after_leave"].success


@pytest.mark.asyncio
async def test_a_refused_switch_leaves_the_turn_where_it_was(
    courses, libraries, as_account, teaching_users, monkeypatch
):
    seen: dict = {}

    async def body(context):
        with as_account("tina"):
            TeachingService().revoke(courses.template_b, teaching_users["sara"]["id"])
        seen["switched"] = await _switch(context, courses.b)
        seen["path"] = context.metadata["mastery_path_id"]
        seen["kbs"] = list(context.knowledge_bases)
        seen["a"] = await _rag(libraries.calculus.ref)
        seen["b"] = await _rag(libraries.algebra.ref)

    _turn(monkeypatch, body)
    with as_account("sara"):
        await asyncio.to_thread(LearningStore().acquire_path_lease, courses.a, "s1", "t1")
        await MasteryLoopPipeline(language="en").run(_context(courses.a, "study"), _Stream())

    assert seen["switched"].success is False
    assert seen["switched"].content == INSTANCE_UNAVAILABLE
    assert seen["path"] == courses.a
    assert seen["kbs"] == [libraries.calculus.ref]
    assert seen["a"].success and seen["b"].success is False


@pytest.mark.asyncio
async def test_a_switch_re_runs_the_turn_gate_and_rolls_back_its_lease(
    courses, libraries, as_account, monkeypatch
):
    """Later admission steps (revision, materials) live in the same gate; a
    refusal after the lease handoff hands the lease back."""
    seen: dict = {}
    from deeptutor.capabilities.mastery import turn_scope

    original = turn_scope.teacher_instance_gate

    async def gate(stream, learning_store, path_id):
        seen.setdefault("gated", []).append(path_id)
        if path_id == courses.b:
            await stream.error(
                "Synthetic refusal",
                source="mastery_path",
                metadata={"code": "teacher_materials_unavailable"},
            )
            return None
        return await original(stream, learning_store, path_id)

    async def body(context):
        seen["switched"] = await _switch(context, courses.b)
        seen["path"] = context.metadata["mastery_path_id"]
        seen["a"] = await _rag(libraries.calculus.ref)

    _turn(monkeypatch, body)
    monkeypatch.setattr(turn_scope, "teacher_instance_gate", gate)
    with as_account("sara"):
        store = LearningStore()
        await asyncio.to_thread(store.acquire_path_lease, courses.a, "s1", "t1")
        await MasteryLoopPipeline(language="en").run(_context(courses.a, "study"), _Stream())
        lease_a = await asyncio.to_thread(store.get_path_lease, courses.a)
        lease_b = await asyncio.to_thread(store.get_path_lease, courses.b)

    assert seen["gated"] == [courses.a, courses.b]
    assert seen["switched"].success is False and seen["switched"].content == "Synthetic refusal"
    assert seen["path"] == courses.a and seen["a"].success
    assert lease_a is not None and lease_a.turn_id == "t1"
    assert lease_b is None


@pytest.mark.asyncio
async def test_concurrent_turns_do_not_share_a_scope(courses, libraries, as_account, monkeypatch):
    release = asyncio.Event()
    seen: dict = {}

    async def body(_context):
        await release.wait()

    async def ordinary_chat():
        with as_account("sara"):
            await asyncio.sleep(0)
            seen["chat"] = await _rag(libraries.history.ref)
        release.set()

    async def teacher_turn():
        with as_account("sara"):
            await MasteryLoopPipeline(language="en").run(_context(courses.a, "study"), _Stream())

    _turn(monkeypatch, body)
    await asyncio.gather(asyncio.create_task(teacher_turn()), asyncio.create_task(ordinary_chat()))

    assert seen["chat"].success


# ---------------------------------------------------------------------------
# Plan D: book reading scope composes with the allowlist
# ---------------------------------------------------------------------------


@pytest.fixture
def shared_book(as_account, make_kb, teaching_users):
    """Tom's public book "Optics", bound to his KB shared by reference only."""
    with as_account("tom"):
        manager = current_kb_manager()
        make_kb(manager, "optics", text="Synthetic optics material")
        kb = kbs.register_kb(
            base_dir=manager.base_dir,
            name="optics",
            kind="uploaded",
            created_by=teaching_users["tom"]["id"],
        )
        get_book_storage().save_book(
            Book(id="bk_optics", title="Optics", knowledge_bases=[kb.ref])
        )
        book_access.accessible_books()
        book_registry.set_book_visibility(
            "bk_optics", "public", kb_choices={kb.kb_id: "reference_only"}
        )
    return kb


@pytest.mark.asyncio
async def test_book_reading_retrieval_is_unchanged_outside_teacher_paths(
    shared_book, courses, libraries, as_account, monkeypatch
):
    seen: dict = {}
    refs = [{"book_id": "book:bk_optics", "page_ids": []}]

    async def body(_context):
        seen.setdefault("turns", []).append(await _rag(shared_book.ref))

    _turn(monkeypatch, body)
    with as_account("sara"):
        with pytest.raises(HTTPException):
            resolve_for_rag(shared_book.ref)  # Not readable without the book.
        with book_access.book_reading_scope(refs) as usable:
            assert usable == [shared_book.ref]
            chat = await _rag(shared_book.ref)
            await MasteryLoopPipeline(language="en").run(
                _context(courses.personal, "study", (shared_book.ref,)), _Stream()
            )
            # A teacher path does not widen to a book the learner attached.
            await MasteryLoopPipeline(language="en").run(
                _context(courses.a, "study", (shared_book.ref,)), _Stream()
            )
            after = await _rag(shared_book.ref)

    personal, teacher = seen["turns"]
    assert chat.success and personal.success and after.success
    assert teacher.success is False and teacher.content == TEACHER_PATH_RETRIEVAL_DENIED
    assert libraries.searched == ["optics", "optics", "optics"]


def test_the_allowlist_only_narrows(libraries, as_account):
    """Allowlisted is necessary, not sufficient, and a nested scope cannot
    reopen what its enclosing turn closed."""
    from deeptutor.multi_user.retrieval_scope import turn_retrieval_scope

    with as_account("tom"), turn_retrieval_scope() as outer:
        outer.restrict({libraries.history.kb_id, libraries.notes.kb_id}, template_id="t")
        assert resolve_for_rag(libraries.history.ref) is not None
        with pytest.raises(HTTPException) as closed:
            resolve_for_rag(libraries.notes.ref)  # Sara's private notes stay hers.
        assert closed.value.status_code == 404
        with turn_retrieval_scope() as inner:
            assert resolve_for_rag(libraries.history.ref) is not None
            with pytest.raises(HTTPException):
                resolve_for_rag(libraries.calculus.ref)
            inner.restrict({libraries.calculus.kb_id}, template_id="t2")
            with pytest.raises(HTTPException):
                resolve_for_rag(libraries.calculus.ref)
            with pytest.raises(HTTPException):
                resolve_for_rag(libraries.history.ref)
        assert current_retrieval_scope() is outer
    assert current_retrieval_scope() is None


# ---------------------------------------------------------------------------
# The turn runtime: every mastery-workspace action, not only the tutor
# ---------------------------------------------------------------------------


@pytest.fixture
def chat_runtime(libraries, tmp_path, monkeypatch):
    """The real turn runtime (request preparer + executor) with a stand-in
    turn engine; ``run(account, path_id, body)`` starts a chat action in the
    mastery workspace and returns its events plus what the executor did."""
    from deeptutor.core.stream import StreamEvent, StreamEventType
    from deeptutor.services.session import _turn_runtime_shared
    from deeptutor.services.session.sqlite_store import SQLiteSessionStore
    from deeptutor.services.session.turn_runtime import TurnRuntimeManager
    from deeptutor.services.session.turns import executor as executor_module
    from deeptutor.services.skill.service import SkillService

    class History:
        def __init__(self, *_args, **_kwargs):
            pass

        async def build(self, **_kwargs):
            return SimpleNamespace(
                conversation_history=[], conversation_summary="", context_text="",
                token_count=0, budget=0,
            )

    async def noop(*_args, **_kwargs):
        return None

    async def forget(_session_id, _path_id):
        return None

    loaded: list[str] = []
    real_manifest = _turn_runtime_shared._topic_material_manifest

    def manifest(path_id):
        loaded.append(path_id)
        return real_manifest(path_id)

    monkeypatch.setattr(executor_module, "_topic_material_manifest", manifest)
    monkeypatch.setattr("deeptutor.services.config.runtime_settings.load_system_settings", lambda: {})
    monkeypatch.setattr("deeptutor.services.llm.config.get_llm_config", lambda: SimpleNamespace())
    monkeypatch.setattr("deeptutor.agents.loop.pipeline.get_llm_config", lambda: SimpleNamespace())
    monkeypatch.setattr(
        "deeptutor.multi_user.model_access.apply_allowed_llm_selection", lambda _selection: {},
    )
    monkeypatch.setattr("deeptutor.services.session.context_builder.ContextBuilder", History)
    monkeypatch.setattr(
        "deeptutor.services.memory.get_memory_store",
        lambda: SimpleNamespace(read_l3_concat=lambda: "", emit=noop),
    )
    monkeypatch.setattr(
        "deeptutor.services.skill.get_skill_service",
        lambda: SkillService(root=tmp_path / "skills", builtin_root=None),
    )
    monkeypatch.setattr("deeptutor.capabilities.mastery.binding._remember_on_session", forget)

    async def run(path_id: str, body, *, knowledge_bases=()):
        seen: dict = {}

        class Execution:
            async def execute(self, context):
                seen["context"] = context
                await body(context, seen)
                yield StreamEvent(type=StreamEventType.CONTENT, content="ok")
                yield StreamEvent(type=StreamEventType.DONE, source="chat")

        runtime = TurnRuntimeManager(
            SQLiteSessionStore(tmp_path / f"turns-{path_id}.sqlite3"), turn_engine=Execution(),
        )
        try:
            _session, turn = await runtime.start_turn({
                "content": "Explain limits", "capability": "chat",
                "workspace_mode": "mastery_path", "mastery_path_id": path_id,
                "language": "en", "tools": [], "knowledge_bases": list(knowledge_bases),
                "attachments": [], "config": {},
                "llm_selection": {"profile_id": "synthetic", "model_id": "synthetic"},
            })
            async with asyncio.timeout(20):
                events = [e async for e in runtime.subscribe_turn(turn["id"], after_seq=0)]
                execution = runtime._executions.get(turn["id"])
                if execution and execution.task:
                    await asyncio.shield(execution.task)
        finally:
            await runtime.close()
        seen["events"] = events
        seen["loaded"] = list(loaded)
        seen["scope_after"] = current_retrieval_scope()
        loaded.clear()
        return seen

    return run


@pytest.mark.asyncio
async def test_a_chat_action_on_a_revoked_teacher_path_is_refused(
    courses, chat_runtime, as_account, teaching_users
):
    with as_account("tina"):
        TeachingService().revoke(courses.template_a, teaching_users["sara"]["id"])

    async def body(_context, out):
        out["ran"] = True

    with as_account("sara"):
        seen = await chat_runtime(courses.a, body)

    assert "ran" not in seen
    # The instance's materials were never read into the turn.
    assert seen["loaded"] == []
    errors = [e for e in seen["events"] if e["type"] == "error"]
    assert errors and errors[0]["content"] == INSTANCE_UNAVAILABLE
    assert errors[0]["metadata"]["error_code"] == "teacher_path_unavailable"
    done = [e for e in seen["events"] if e["type"] == "done"]
    assert done[-1]["metadata"]["status"] == "failed"


@pytest.mark.asyncio
async def test_a_chat_action_on_a_teacher_path_retrieves_only_its_libraries(
    courses, libraries, chat_runtime, as_account
):
    async def body(context, out):
        out["kbs"] = list(context.knowledge_bases)
        out["unrelated"] = await _rag(libraries.history.ref)
        out["own"] = await _rag(libraries.calculus.ref)

    with as_account("sara"):
        seen = await chat_runtime(courses.a, body, knowledge_bases=(libraries.history.ref,))

    assert seen["loaded"] == [courses.a]
    assert seen["kbs"] == [libraries.calculus.ref]
    assert seen["unrelated"].success is False
    assert seen["unrelated"].content == TEACHER_PATH_RETRIEVAL_DENIED
    assert seen["own"].success
    assert libraries.searched == ["calculus"]
    assert seen["scope_after"] is None


@pytest.mark.asyncio
async def test_a_chat_action_on_a_personal_path_is_unchanged(
    courses, libraries, chat_runtime, as_account, monkeypatch
):
    def _forbidden(*_args, **_kwargs):
        raise AssertionError("a personal path must not open the teaching store")

    monkeypatch.setattr("deeptutor.teaching.instances.TeachingStore", _forbidden)

    async def body(context, out):
        out["kbs"] = list(context.knowledge_bases)
        out["history"] = await _rag(libraries.history.ref)

    with as_account("sara"):
        seen = await chat_runtime(courses.personal, body, knowledge_bases=(libraries.history.ref,))

    assert seen["kbs"] == [libraries.history.ref]
    assert seen["history"].success
    assert not [e for e in seen["events"] if e["type"] == "error"]


@pytest.mark.asyncio
async def test_a_switch_inside_a_chat_action_re_runs_the_gate(
    courses, libraries, chat_runtime, as_account, teaching_users
):
    from deeptutor.capabilities.mastery.loop import MasteryLoopCapability

    async def switch(context, path_id):
        kwargs = MasteryLoopCapability().augment_kwargs(
            "mastery_switch", {"path_id": path_id}, context
        )
        return await MasterySwitchTool().execute(**kwargs)

    async def body(context, out):
        context.metadata["mastery_session_mode"] = "outline"
        context.metadata["mastery_topic_source_index"] = {"src_a": "Path A full text"}
        out["to_b"] = await switch(context, courses.b)
        out["kbs_b"] = list(context.knowledge_bases)
        out["mode_b"] = context.metadata["mastery_session_mode"]
        out["index_b"] = dict(context.metadata["mastery_topic_source_index"])
        out["b"] = await _rag(libraries.algebra.ref)
        out["a"] = await _rag(libraries.calculus.ref)
        out["to_personal"] = await switch(context, courses.personal)
        out["kbs_p"] = list(context.knowledge_bases)
        out["history"] = await _rag(libraries.history.ref)
        with as_account("tina"):
            TeachingService().revoke(courses.template_a, teaching_users["sara"]["id"])
        out["to_revoked"] = await switch(context, courses.a)
        out["path"] = context.metadata["mastery_path_id"]

    with as_account("sara"):
        seen = await chat_runtime(courses.a, body, knowledge_bases=(libraries.history.ref,))

    assert seen["to_b"].success, seen["to_b"].content
    assert seen["kbs_b"] == [libraries.algebra.ref] and seen["mode_b"] == "study"
    # read_source follows the path: A's material is gone from the index.
    assert "src_a" not in seen["index_b"]
    assert seen["b"].success
    assert seen["a"].success is False and seen["a"].content == TEACHER_PATH_RETRIEVAL_DENIED
    assert seen["to_personal"].success
    assert seen["kbs_p"] == [libraries.history.ref]
    assert seen["history"].success
    assert seen["to_revoked"].success is False
    assert seen["to_revoked"].content == INSTANCE_UNAVAILABLE
    assert seen["path"] == courses.personal
