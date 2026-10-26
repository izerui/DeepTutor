"""Plans B, C and D together, without stand-ins (cross-check 1.3, R9, spec 7.8)."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from deeptutor.agents.loop.pipeline import AgenticLoopPipeline
from deeptutor.api.routers import mastery_path
from deeptutor.book import registry as book_registry
from deeptutor.capabilities.mastery.pipeline import MasteryLoopPipeline
from deeptutor.core.context import UnifiedContext
from deeptutor.knowledge import registry as kb_registry
from deeptutor.learning.models import TopicSource, TopicSourceKind
from deeptutor.learning.storage import LearningStore
from deeptutor.multi_user.context import get_current_user
from deeptutor.multi_user.knowledge_access import resolve_kb
from deeptutor.teaching.access import studying_template
from deeptutor.teaching.instances import MATERIALS_UNAVAILABLE
from deeptutor.teaching.service import TeachingService


def _auth(name: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {name}"}


def _book_source(book_id: str, label: str, position: int = 0) -> TopicSource:
    return TopicSource(
        id=f"b{position}", kind=TopicSourceKind.BOOK, source_id=book_id, label=label,
        position=position,
    )


@pytest.fixture
def real_path(teaching_users, as_account, make_kb, teacher_draft, publish_template):
    """Tina's private KB, really registered, published reference-only to Sara."""
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    from deeptutor.multi_user.paths import get_account_path_service

    with as_account("tina"):
        root = get_account_path_service().get_knowledge_bases_root()
        root.mkdir(parents=True, exist_ok=True)
        make_kb(KnowledgeBaseManager(base_dir=str(root)), "answer-keys", text="d/dx x^2 = 2x")
        record = kb_registry.ensure_registered(
            root, "answer-keys", created_by=teaching_users["tina"]["id"]
        )
    assert record.visibility == "private"
    teacher_draft(
        sources=(
            TopicSource(
                id="s0",
                kind=TopicSourceKind.KNOWLEDGE_BASE,
                source_id=record.ref,
                label="Answer keys",
                position=0,
            ),
        )
    )
    result = publish_template(students=("sara",), kb_choices={record.kb_id: "reference_only"})
    assert result["warnings"] == []
    with as_account("sara"):
        path_id = asyncio.run(mastery_path.list_topics())["topics"][0]["path_id"]
    return SimpleNamespace(
        record=record, template_id=result["template"]["id"], path_id=path_id, root=root
    )


def test_the_student_resolves_the_kb_only_inside_the_path(real_path, as_account):
    assert kb_registry.get_kb(real_path.record.kb_id).visibility == "private"
    with as_account("sara"):
        with pytest.raises(HTTPException) as outside:
            resolve_kb(real_path.record.ref)
        assert outside.value.status_code == 404
        with studying_template(real_path.template_id):
            resource = resolve_kb(real_path.record.ref)
    assert resource.read_only is True
    assert resource.name == "answer-keys"


def test_another_path_or_another_account_does_not_open_it(real_path, as_account):
    with as_account("sam"):
        with studying_template(real_path.template_id):
            with pytest.raises(HTTPException):
                resolve_kb(real_path.record.ref)
    with as_account("sara"):
        with studying_template("tpl_other"):
            with pytest.raises(HTTPException):
                resolve_kb(real_path.record.ref)


def test_revoking_closes_the_kb_immediately(real_path, as_account, teaching_users):
    with as_account("tina"):
        TeachingService().revoke(real_path.template_id, teaching_users["sara"]["id"])
    with as_account("sara"):
        with studying_template(real_path.template_id):
            with pytest.raises(HTTPException):
                resolve_kb(real_path.record.ref)


def test_archiving_closes_the_kb_immediately(real_path, as_account):
    with as_account("tina"):
        TeachingService().archive(real_path.template_id)
    with as_account("sara"):
        with studying_template(real_path.template_id):
            with pytest.raises(HTTPException):
                resolve_kb(real_path.record.ref)


def test_a_reference_only_book_and_its_kbs_open_only_inside_the_path(
    teaching_books, teacher_draft, publish_template, as_account
):
    teacher_draft(sources=(_book_source("bk_own", "Waves"),))
    template_id = publish_template(
        students=("sara",),
        book_choices={"bk_own": "reference_only"},
        kb_choices={teaching_books.answers.kb_id: "reference_only"},
    )["template"]["id"]
    book = book_registry.get_book("bk_own")
    answers = kb_registry.get_kb(teaching_books.answers.kb_id)

    with as_account("sara"):
        user = get_current_user()
        assert book_registry.can_read_book(book, user) is False
        with studying_template(template_id):
            assert book_registry.can_read_book(book, user) is True
            assert kb_registry.can_read(answers, user) is False  # not reading the book yet
            with book_registry.reading_book("bk_own"):
                assert kb_registry.can_read(answers, user) is True
        assert kb_registry.can_read(answers, user) is False


def test_a_students_migration_releases_a_dropped_private_book(
    teaching_books, teaching_users, teacher_draft, publish_template, as_account, tmp_path, caplog
):
    """C10 handoff: the post-migration resync runs as the student and now succeeds."""
    book_registry.register_book(
        base_dir=tmp_path / "books",
        dir_name="book_bk_two",
        title="Two",
        created_by=teaching_users["tina"]["id"],
    )
    teacher_draft(sources=(_book_source("bk_own", "Waves"), _book_source("bk_two", "Two", 1)))
    template_id = publish_template(
        students=("sara",),
        book_choices={"bk_own": "reference_only", "bk_two": "reference_only"},
        kb_choices={teaching_books.answers.kb_id: "reference_only"},
    )["template"]["id"]
    with as_account("sara"):
        path_id = asyncio.run(mastery_path.list_topics())["topics"][0]["path_id"]
    with as_account("tina"):
        store = LearningStore()
        topic = store.get_topic("topic_calculus")
        store.put_topic(topic.metadata, [_book_source("bk_own", "Waves")])
    publish_template(book_choices={"bk_own": "reference_only"})
    # Sara is still on revision 1, so both books stay referenced for her.
    held = {r.book_id for r in book_registry.list_referenced_books("mastery_template", template_id)}
    assert held == {"bk_own", "bk_two"}

    caplog.set_level(logging.WARNING, logger="deeptutor.teaching.instances")
    with as_account("sara"):
        payload = asyncio.run(mastery_path.get_topic(path_id))
    assert payload["origin"]["revision"] == 2
    released = {
        r.book_id for r in book_registry.list_referenced_books("mastery_template", template_id)
    }
    assert released == {"bk_own"}
    assert "Could not release" not in caplog.text
    assert book_registry.get_book("bk_two").visibility == "private"


# ---- materials unavailable (spec 7.4, preflight item 5) ----------------------


class _Stream:
    def __init__(self) -> None:
        self.errors: list[tuple[str, dict]] = []

    async def error(self, message, source="", stage="", metadata=None):
        self.errors.append((message, dict(metadata or {})))


def _context(path_id: str, turn_id: str = "t1") -> UnifiedContext:
    return UnifiedContext(
        session_id="s1",
        enabled_tools=[],
        knowledge_bases=[],
        metadata={
            "mastery_mode": True,
            "mastery_path_id": path_id,
            "mastery_session_mode": "study",
            "turn_id": turn_id,
        },
    )


def _probe(monkeypatch, body) -> list:
    ran: list = []

    async def _loop(self, context, _stream):
        ran.append(await body(context) if body else True)
        return {}

    monkeypatch.setattr(AgenticLoopPipeline, "run", _loop)
    monkeypatch.setattr("deeptutor.capabilities.setup.binding._intro_pending", lambda _c: False)
    return ran


def test_admin_force_private_makes_the_path_materials_unavailable(
    teaching_users, as_account, make_kb, teacher_draft, publish_template
):
    """Spec 7.4 with plan B's real force path (``list_unavailable_references``)."""
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    from deeptutor.multi_user.paths import get_account_path_service

    with as_account("adam"):
        root = get_account_path_service().get_knowledge_bases_root()
        root.mkdir(parents=True, exist_ok=True)
        make_kb(KnowledgeBaseManager(base_dir=str(root)), "algebra")
        record = kb_registry.ensure_registered(
            root, "algebra", created_by=teaching_users["adam"]["id"]
        )
        kb_registry.set_visibility(record.kb_id, "public")
    teacher_draft(
        sources=(
            TopicSource(
                id="s0",
                kind=TopicSourceKind.KNOWLEDGE_BASE,
                source_id=record.ref,
                label="Algebra notes",
                position=0,
            ),
        )
    )
    template_id = publish_template(students=("sara",))["template"]["id"]
    with as_account("sara"):
        path_id = asyncio.run(mastery_path.list_topics())["topics"][0]["path_id"]
        assert asyncio.run(mastery_path.get_topic(path_id))["origin"]["materials_available"] is True

    with as_account("adam"):
        kb_registry.set_visibility(record.kb_id, "private", force=True)

    assert [
        r.reason for r in kb_registry.list_unavailable_references("mastery_template", template_id)
    ] == ["made_private"]
    with as_account("tina"):
        (described,) = TeachingService().list_for_actor()
    assert described["materials_unavailable"] == ["Algebra notes"]
    with as_account("sara"):
        assert asyncio.run(mastery_path.get_topic(path_id))["origin"]["materials_available"] is False
        with studying_template(template_id):
            with pytest.raises(HTTPException):
                resolve_kb(record.ref)


def test_admin_force_private_of_a_cited_book_is_reported(
    teaching_books, teacher_draft, publish_template, as_account
):
    """Spec 7.4 for books, with plan D's ``list_unavailable_book_references``."""
    teacher_draft(sources=(_book_source("bk_pub", "Optics"),))
    template_id = publish_template(students=("sara",))["template"]["id"]
    with as_account("sara"):
        path_id = asyncio.run(mastery_path.list_topics())["topics"][0]["path_id"]

    with as_account("adam"):
        book_registry.set_book_visibility("bk_pub", "private", force=True)

    assert [
        r.reason
        for r in book_registry.list_unavailable_book_references("mastery_template", template_id)
    ] == ["made_private"]
    with as_account("tina"):
        (described,) = TeachingService().list_for_actor()
    assert described["materials_unavailable"] == ["Optics"]
    with as_account("sara"):
        assert asyncio.run(mastery_path.get_topic(path_id))["origin"]["materials_available"] is False


@pytest.fixture
def algebra_book(teaching_users, as_account, make_kb, teacher_draft, publish_template, tmp_path):
    """Tina's reference-only book bound to Adam's public KB, published to Sara."""
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    from deeptutor.multi_user.paths import get_account_path_service

    with as_account("adam"):
        root = get_account_path_service().get_knowledge_bases_root()
        root.mkdir(parents=True, exist_ok=True)
        make_kb(KnowledgeBaseManager(base_dir=str(root)), "algebra")
        kb = kb_registry.ensure_registered(root, "algebra", created_by=teaching_users["adam"]["id"])
        kb_registry.set_visibility(kb.kb_id, "public")
    book = book_registry.register_book(
        base_dir=tmp_path / "books",
        dir_name="book_bk_alg",
        title="Algebra book",
        created_by=teaching_users["tina"]["id"],
    )
    book_registry.set_book_kb_links(book.book_id, {kb.ref: kb.kb_id})
    teacher_draft(sources=(_book_source("bk_alg", "Algebra book"),))
    template_id = publish_template(students=("sara",), book_choices={"bk_alg": "reference_only"})[
        "template"
    ]["id"]
    with as_account("sara"):
        path_id = asyncio.run(mastery_path.list_topics())["topics"][0]["path_id"]
    return SimpleNamespace(kb=kb, book=book, template_id=template_id, path_id=path_id)


@pytest.mark.asyncio
async def test_a_revoked_book_dependency_stops_the_tutor(algebra_book, as_account, monkeypatch):
    """Preflight item 5: the book stays readable, its bound KB does not."""
    seen: dict = {}

    async def body(context):
        seen["kbs"] = list(context.knowledge_bases)
        seen["kb_readable"] = kb_registry.can_read(algebra_book.kb, get_current_user())
        return True

    ran = _probe(monkeypatch, body)
    with as_account("sara"):
        await MasteryLoopPipeline(language="en").run(_context(algebra_book.path_id), _Stream())
    # Before the withdrawal: the book's KB is part of the turn's materials.
    assert ran == [True]
    assert seen == {"kbs": [algebra_book.kb.ref], "kb_readable": True}

    with as_account("adam"):
        kb_registry.set_visibility(algebra_book.kb.kb_id, "private", force=True)
    ran.clear()
    stream = _Stream()
    with as_account("sara"):
        user = get_current_user()
        with studying_template(algebra_book.template_id):
            assert book_registry.can_read_book(algebra_book.book, user)  # the book itself is fine
        await MasteryLoopPipeline(language="en").run(_context(algebra_book.path_id), stream)
        payload = await asyncio.to_thread(
            lambda: asyncio.run(mastery_path.get_topic(algebra_book.path_id))
        )

    assert ran == []
    message, metadata = stream.errors[0]
    assert message == MATERIALS_UNAVAILABLE
    assert metadata == {"code": "teacher_materials_unavailable", "materials": ["Algebra book"]}
    assert payload["origin"]["materials_available"] is False
    with as_account("tina"):
        (described,) = TeachingService().list_for_actor()
    assert described["materials_unavailable"] == ["Algebra book"]


@pytest.mark.asyncio
async def test_an_old_revision_question_keeps_only_that_revisions_materials(
    real_path, teaching_users, as_account, make_kb, publish_template, monkeypatch
):
    """Preflight item 6: K1 stays usable for the waiting copy; K2 is not yet its own."""
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    from deeptutor.learning.models import PendingQuestion
    from deeptutor.multi_user.knowledge_access import resolve_for_rag

    with as_account("tina"):
        make_kb(KnowledgeBaseManager(base_dir=str(real_path.root)), "new-keys", text="new")
        k2 = kb_registry.ensure_registered(
            real_path.root, "new-keys", created_by=teaching_users["tina"]["id"]
        )
    with as_account("sara"):
        store = LearningStore()
        point = store.load(real_path.path_id).modules[0].knowledge_points[0].id

        def ask(tx):
            tx.progress.pending_question = PendingQuestion(
                question_id="q-open", knowledge_point_id=point, expected_answer="x"
            )
            tx.touch()

        store.mutate(real_path.path_id, ask)
    with as_account("tina"):
        store = LearningStore()
        topic = store.get_topic("topic_calculus")
        store.put_topic(
            topic.metadata,
            [
                TopicSource(
                    id="s0", kind=TopicSourceKind.KNOWLEDGE_BASE, source_id=k2.ref,
                    label="New keys", position=0,
                )
            ],
        )
    await asyncio.to_thread(publish_template, kb_choices={k2.kb_id: "reference_only"})

    seen: dict = {}

    async def body(context):
        seen["kbs"] = list(context.knowledge_bases)
        seen["k1"] = resolve_for_rag(real_path.record.ref).name
        with pytest.raises(HTTPException) as refused:
            resolve_for_rag(k2.ref)
        seen["k2"] = refused.value.status_code
        return True

    ran = _probe(monkeypatch, body)
    stream = _Stream()
    with as_account("sara"):
        await MasteryLoopPipeline(language="en").run(_context(real_path.path_id), stream)
        origin = LearningStore().load_origin(real_path.path_id)
    assert stream.errors == [] and ran == [True]
    assert origin.revision == 1
    assert seen == {"kbs": [real_path.record.ref], "k1": "answer-keys", "k2": 403}

    # An explicit revoke still closes everything at once.
    with as_account("tina"):
        TeachingService().revoke(real_path.template_id, teaching_users["sara"]["id"])
    stream = _Stream()
    ran.clear()
    with as_account("sara"):
        await MasteryLoopPipeline(language="en").run(_context(real_path.path_id), stream)
        with studying_template(real_path.template_id):
            with pytest.raises(HTTPException):
                resolve_kb(real_path.record.ref)
    assert ran == []
    assert stream.errors[0][1]["code"] == "teacher_path_unavailable"


# ---- a real reference-only book with content -----------------------------------


def _write_real_book(as_account, make_kb, teaching_users, *, book_id: str = "bk_real"):
    """Tina's private book with one page, bound to her private KB "wave-keys"."""
    from deeptutor.book.models import (
        Block,
        BlockStatus,
        BlockType,
        Book,
        Chapter,
        Page,
        PageStatus,
        Spine,
    )
    from deeptutor.book.storage import get_book_storage
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    from deeptutor.multi_user.paths import get_account_path_service

    with as_account("tina"):
        root = get_account_path_service().get_knowledge_bases_root()
        root.mkdir(parents=True, exist_ok=True)
        make_kb(KnowledgeBaseManager(base_dir=str(root)), "wave-keys", text="lambda = v / f")
        kb = kb_registry.ensure_registered(
            root, "wave-keys", created_by=teaching_users["tina"]["id"]
        )
        storage = get_book_storage()
        storage.save_book(
            Book(id=book_id, title="Waves", page_count=1, knowledge_bases=[kb.ref])
        )
        storage.save_spine(
            Spine(book_id=book_id, chapters=[Chapter(id="ch_1", title="Waves", page_ids=["pg_1"])])
        )
        storage.save_page(
            Page(
                id="pg_1",
                book_id=book_id,
                chapter_id="ch_1",
                title="Standing waves",
                status=PageStatus.READY,
                blocks=[
                    Block(
                        id="blk_1",
                        type=BlockType.TEXT,
                        status=BlockStatus.READY,
                        payload={"body": "Nodes stay still."},
                    )
                ],
            )
        )
        # Registered in Tina's scope on first sight (plan D, R3).
        record = book_registry.resolve_book_ref(book_id)
    assert record is not None
    assert book_registry.book_kb_links(book_id) == {kb.ref: kb.kb_id}
    return SimpleNamespace(record=record, kb=kb)


@pytest.fixture
def real_book_path(teaching_users, as_account, make_kb, teacher_draft, publish_template):
    """A real private book of Tina's, published reference-only to Sara."""
    written = _write_real_book(as_account, make_kb, teaching_users)
    teacher_draft(sources=(_book_source("bk_real", "Waves"),))
    template_id = publish_template(
        students=("sara",),
        book_choices={"bk_real": "reference_only"},
        kb_choices={written.kb.kb_id: "reference_only"},
    )["template"]["id"]
    with as_account("sara"):
        path_id = asyncio.run(mastery_path.list_topics())["topics"][0]["path_id"]
    return SimpleNamespace(template_id=template_id, path_id=path_id, **vars(written))


@pytest.mark.asyncio
async def test_a_switch_moves_the_template_and_book_scopes_with_it(
    teaching_users, as_account, make_kb, teacher_draft, publish_template
):
    """Preflight item 2: entering B opens B's materials, leaving A closes A's."""
    import contextlib

    from deeptutor.capabilities.mastery import turn_scope
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    from deeptutor.multi_user.paths import get_account_path_service
    from deeptutor.teaching.access import studied_template

    def setup():
        written = _write_real_book(as_account, make_kb, teaching_users)
        with as_account("tina"):
            root = get_account_path_service().get_knowledge_bases_root()
            make_kb(KnowledgeBaseManager(base_dir=str(root)), "answer-keys", text="2x")
            keys = kb_registry.ensure_registered(
                root, "answer-keys", created_by=teaching_users["tina"]["id"]
            )
        teacher_draft(
            path_id="topic_a",
            sources=(
                TopicSource(
                    id="s0", kind=TopicSourceKind.KNOWLEDGE_BASE, source_id=keys.ref,
                    label="Answer keys", position=0,
                ),
            ),
        )
        teacher_draft(path_id="topic_b", sources=(_book_source("bk_real", "Waves"),))
        a = publish_template(
            path_id="topic_a", students=("sara",), kb_choices={keys.kb_id: "reference_only"}
        )["template"]["id"]
        b = publish_template(
            path_id="topic_b",
            students=("sara",),
            book_choices={"bk_real": "reference_only"},
            kb_choices={written.kb.kb_id: "reference_only"},
        )["template"]["id"]
        with as_account("sara"):
            asyncio.run(mastery_path.list_topics())
        return written, keys, a, b

    written, keys, template_a, template_b = await asyncio.to_thread(setup)
    path_a, path_b = f"assigned_{template_a}", f"assigned_{template_b}"
    with as_account("sara"):
        user = get_current_user()
        with contextlib.ExitStack() as stack:
            turn = await turn_scope.open_mastery_turn(
                stack, path_id=path_a, stream=_Stream(), turn_id="t1"
            )
            assert turn is not None
            manifest, index = await asyncio.to_thread(turn_scope._material_manifest, path_a)
            context = _context(path_a)
            context.source_manifest = manifest + "\n[Task Board]"
            context.metadata["mastery_topic_source_index"] = index
            turn.manifest = manifest
            turn.attach(context)
            assert studied_template() == template_a
            assert kb_registry.can_read(keys, user)
            assert not book_registry.can_read_book(written.record, user)

            # The switch tool may run in a copied context; it must still move
            # the whole turn.
            refusal = await asyncio.create_task(turn.switch(path_b, turn.store))
            assert refusal is None
            assert studied_template() == template_b
            assert not kb_registry.can_read(keys, user)  # A's library closed
            assert book_registry.can_read_book(written.record, user)
            assert kb_registry.can_read(written.kb, user)  # through the book's scope
            assert context.knowledge_bases == [written.kb.ref]
            texts = "\n".join(context.metadata["mastery_topic_source_index"].values())
            assert "Nodes stay still." in texts
            assert "Answer keys" not in context.source_manifest
            assert "Waves" in context.source_manifest
            assert context.source_manifest.endswith("[Task Board]")
        assert studied_template() == ""
        assert not book_registry.can_read_book(written.record, user)
        assert not kb_registry.can_read(written.kb, user)
