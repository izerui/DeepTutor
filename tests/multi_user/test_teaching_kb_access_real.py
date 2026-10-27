"""Plans B, C and D together, without stand-ins (cross-check 1.3, R9, spec 7.8)."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from urllib.parse import quote

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


@pytest.mark.usefixtures("teaching_books")
def test_admin_force_private_of_a_cited_book_is_reported(
    teacher_draft, publish_template, as_account
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
        book = _write_real_book(as_account, make_kb, teaching_users)
        with as_account("tina"):
            root = get_account_path_service().get_knowledge_bases_root()
            make_kb(KnowledgeBaseManager(base_dir=str(root)), "answer-keys", text="2x")
            library = kb_registry.ensure_registered(
                root, "answer-keys", created_by=teaching_users["tina"]["id"]
            )
        teacher_draft(
            path_id="topic_a",
            sources=(
                TopicSource(
                    id="s0", kind=TopicSourceKind.KNOWLEDGE_BASE, source_id=library.ref,
                    label="Answer keys", position=0,
                ),
            ),
        )
        teacher_draft(path_id="topic_b", sources=(_book_source("bk_real", "Waves"),))
        a = publish_template(
            path_id="topic_a", students=("sara",), kb_choices={library.kb_id: "reference_only"}
        )["template"]["id"]
        b = publish_template(
            path_id="topic_b",
            students=("sara",),
            book_choices={"bk_real": "reference_only"},
            kb_choices={book.kb.kb_id: "reference_only"},
        )["template"]["id"]
        with as_account("sara"):
            asyncio.run(mastery_path.list_topics())
        return book, library, a, b

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


# ---- Study-page originals (R9, preflight items 3 and 4) -----------------------


def _files_url(ref: str, rel_path: str) -> str:
    return f"/api/knowledge-bases/{quote(ref, safe='')}/files/{rel_path}"


def test_study_page_opens_the_original_with_the_path_context(real_path, teaching_http):
    url = _files_url(real_path.record.ref, "doc.md")

    assert teaching_http.client.get(url, headers=_auth("sara")).status_code == 404
    opened = teaching_http.client.get(
        f"{url}?mastery_template={real_path.template_id}", headers=_auth("sara")
    )
    assert opened.status_code == 200
    assert "2x" in opened.text
    by_header = teaching_http.client.get(
        url, headers={**_auth("sara"), "X-DeepTutor-Mastery-Template": real_path.template_id}
    )
    assert by_header.status_code == 200
    # Sam has no assignment: naming the path does not open it.
    assert (
        teaching_http.client.get(
            f"{url}?mastery_template={real_path.template_id}", headers=_auth("sam")
        ).status_code
        == 403
    )


def test_the_path_context_opens_only_original_content_routes(real_path, teaching_http):
    ref = quote(real_path.record.ref, safe="")
    config = teaching_http.client.get(
        f"/api/knowledge-bases/{ref}/config?mastery_template={real_path.template_id}",
        headers=_auth("sara"),
    )
    assert config.status_code == 404
    text = teaching_http.client.get(
        f"/api/knowledge-bases/{ref}/file-preview-text/doc.md?mastery_template={real_path.template_id}",
        headers=_auth("sara"),
    )
    assert text.status_code == 200
    listing = teaching_http.client.get(
        f"/api/knowledge-bases/{ref}/files?mastery_template={real_path.template_id}",
        headers=_auth("sara"),
    )
    assert listing.status_code == 404


def test_revoking_closes_the_originals(real_path, teaching_http, as_account, teaching_users):
    url = f"{_files_url(real_path.record.ref, 'doc.md')}?mastery_template={real_path.template_id}"
    assert teaching_http.client.get(url, headers=_auth("sara")).status_code == 200
    with as_account("tina"):
        TeachingService().revoke(real_path.template_id, teaching_users["sara"]["id"])
    assert teaching_http.client.get(url, headers=_auth("sara")).status_code == 403


def _png() -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (2, 2), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _add_asset(kb_dir, source_path: str) -> str:
    from hashlib import sha256

    from deeptutor.services.rag.visual_assets import VisualAssetCandidate, VisualAssetStore

    image = kb_dir / f"{source_path.replace('/', '_')}.png"
    image.write_bytes(_png())
    asset_id = sha256(source_path.encode()).hexdigest()
    record = {
        "asset_id": asset_id,
        "source_document_id": "0" * 64,
        "source_path": source_path,
        "managed_source": False,
        "image_sha256": sha256(_png()).hexdigest(),
        "mime_type": "image/png",
        "size": len(_png()),
    }
    VisualAssetStore(kb_dir).publish([VisualAssetCandidate(path=image, record=record)])
    return asset_id


@pytest.fixture
def lesson_path(teaching_users, as_account, make_kb, teacher_draft, publish_template):
    """Tina's private KB with two documents; the path cites only ``notes/lesson.md``."""
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    from deeptutor.multi_user.paths import get_account_path_service

    with as_account("tina"):
        root = get_account_path_service().get_knowledge_bases_root()
        root.mkdir(parents=True, exist_ok=True)
        folder = make_kb(KnowledgeBaseManager(base_dir=str(root)), "physics", text="intro")
        (folder / "raw" / "notes").mkdir()
        (folder / "raw" / "notes" / "lesson.md").write_text("Lesson: F = m a", encoding="utf-8")
        (folder / "raw" / "secret.md").write_text("Exam answers", encoding="utf-8")
        record = kb_registry.ensure_registered(
            root, "physics", created_by=teaching_users["tina"]["id"]
        )
        kb_registry.refresh_documents(root, "physics")
        assets = {
            "lesson": _add_asset(folder, "notes/lesson.md"),
            "secret": _add_asset(folder, "secret.md"),
        }
    teacher_draft(
        sources=(
            TopicSource(
                id="f0",
                kind=TopicSourceKind.FILE,
                source_id="notes/lesson.md",
                label="Lesson",
                position=0,
                metadata={"kb_name": record.ref, "path": "notes/lesson.md"},
            ),
        )
    )
    template_id = publish_template(
        students=("sara",), kb_choices={record.kb_id: "reference_only"}
    )["template"]["id"]
    with as_account("sara"):
        path_id = asyncio.run(mastery_path.list_topics())["topics"][0]["path_id"]
    lesson = kb_registry.find_document(record.kb_id, "notes/lesson.md")
    return SimpleNamespace(
        record=record, folder=folder, root=root, template_id=template_id, path_id=path_id,
        assets=assets, lesson=lesson,
    )


def test_reference_only_originals_are_document_scoped(lesson_path, teaching_http):
    """Preflight item 3: the path's KB reference never opens its other documents."""
    ctx = f"mastery_template={lesson_path.template_id}"
    ref = quote(lesson_path.record.ref, safe="")
    get = teaching_http.client.get

    lesson = get(f"{_files_url(lesson_path.record.ref, 'notes/lesson.md')}?{ctx}", headers=_auth("sara"))
    assert lesson.status_code == 200 and "F = m a" in lesson.text
    for denied in (
        f"{_files_url(lesson_path.record.ref, 'secret.md')}?{ctx}",
        f"/api/knowledge-bases/{ref}/file-preview-text/secret.md?{ctx}",
        f"{_files_url(lesson_path.record.ref, 'notes/../secret.md')}?{ctx}",
        f"/api/knowledge-bases/{ref}/visual-assets/{lesson_path.assets['secret']}?{ctx}",
        f"/api/knowledge-bases/{ref}/source-page?source_path=secret.md&page=1&{ctx}",
    ):
        assert get(denied, headers=_auth("sara")).status_code == 404, denied
    asset = get(
        f"/api/knowledge-bases/{ref}/visual-assets/{lesson_path.assets['lesson']}?{ctx}",
        headers=_auth("sara"),
    )
    assert asset.status_code == 200 and asset.content == _png()
    # The owner's own access is unchanged (no path context needed).
    assert get(_files_url(lesson_path.record.ref, "secret.md"), headers=_auth("tina")).status_code == 200


def test_a_partially_cited_public_kb_keeps_its_public_documents(
    teaching_users, as_account, make_kb, teacher_draft, publish_template, teaching_http
):
    """Independent (public) access is not narrowed by the path's document scope."""
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    from deeptutor.multi_user.paths import get_account_path_service

    with as_account("adam"):
        root = get_account_path_service().get_knowledge_bases_root()
        root.mkdir(parents=True, exist_ok=True)
        folder = make_kb(KnowledgeBaseManager(base_dir=str(root)), "library", text="first")
        (folder / "raw" / "other.md").write_text("Public other", encoding="utf-8")
        record = kb_registry.ensure_registered(root, "library", created_by=teaching_users["adam"]["id"])
        kb_registry.set_visibility(record.kb_id, "public")
        kb_registry.refresh_documents(root, "library")
    teacher_draft(
        sources=(
            TopicSource(
                id="f0", kind=TopicSourceKind.FILE, source_id="doc.md", label="First",
                position=0, metadata={"kb_name": record.ref, "path": "doc.md"},
            ),
        )
    )
    template_id = publish_template(students=("sara",))["template"]["id"]
    with as_account("sara"):
        asyncio.run(mastery_path.list_topics())
    other = teaching_http.client.get(
        f"{_files_url(record.ref, 'other.md')}?mastery_template={template_id}", headers=_auth("sara")
    )
    assert other.status_code == 200 and "Public other" in other.text


def test_a_cited_file_that_moved_is_followed_by_its_id(
    lesson_path, teaching_http, as_account, monkeypatch
):
    """Root acceptance: loaders and originals follow doc_id, never the stale path."""
    from deeptutor.learning.lesson_evidence import selected_scope
    from deeptutor.learning.topic_materials import build_topic_materials
    from deeptutor.teaching.store import TeachingStore

    raw = lesson_path.folder / "raw"
    (raw / "archive").mkdir()
    (raw / "notes" / "lesson.md").replace(raw / "archive" / "lesson.md")
    kb_registry.move_document_paths(lesson_path.record.kb_id, "notes/lesson.md", "archive/lesson.md")
    # An unrelated file now occupies the captured path.
    (raw / "notes" / "lesson.md").write_text("Unrelated draft", encoding="utf-8")
    kb_registry.refresh_documents(lesson_path.root, "physics")
    impostor = kb_registry.find_document(lesson_path.record.kb_id, "notes/lesson.md")
    assert impostor.doc_id != lesson_path.lesson.doc_id

    ctx = f"mastery_template={lesson_path.template_id}"
    ref = quote(lesson_path.record.ref, safe="")
    for url in (
        f"{_files_url(lesson_path.record.ref, 'notes/lesson.md')}?{ctx}",
        f"{_files_url(lesson_path.record.ref, 'notes/lesson.md')}?{ctx}&doc_id={lesson_path.lesson.doc_id}",
        f"{_files_url(lesson_path.record.ref, 'archive/lesson.md')}?{ctx}",
        f"/api/knowledge-bases/{ref}/file-preview-text/notes/lesson.md?{ctx}",
    ):
        opened = teaching_http.client.get(url, headers=_auth("sara"))
        assert opened.status_code == 200, url
        assert "F = m a" in opened.text and "Unrelated" not in opened.text, url

    with as_account("sara"):
        store = LearningStore()
        progress = store.load(lesson_path.path_id)
        topic = store.get_topic(lesson_path.path_id, progress=progress)
        (source,) = topic.sources
        # The tutor's materials name the document where it lives now.
        (row,) = build_topic_materials(topic.sources).materials
        assert "archive/lesson.md" in row.note and row.available
        # Lesson evidence scopes the live document (PDF-only path checked by key).
        source.metadata["path"] = "notes/lesson.pdf"
        monkeypatch.setattr(
            "deeptutor.multi_user.knowledge_access.live_document_path",
            lambda kb, doc_id: "archive/lesson.pdf" if doc_id == lesson_path.lesson.doc_id else None,
        )
        point = progress.modules[0].knowledge_points[0]
        point.topic_source_ids = [source.id]
        scope = selected_scope(topic, progress, point.id)
        assert scope == {lesson_path.record.ref: ["archive/lesson.pdf"]}
        payload = asyncio.run(mastery_path.get_topic(lesson_path.path_id))
    assert payload["origin"]["materials_available"] is True
    # Provenance stays as published.
    revision = TeachingStore().get_revision(lesson_path.template_id, 1)
    (doc,) = revision.sources.kb_refs[0].documents
    assert (doc.rel_path, doc.version) == ("notes/lesson.md", lesson_path.lesson.version)
    assert revision.sources.sources[0].metadata["path"] == "notes/lesson.md"


@pytest.fixture
def real_path_kb_only(teacher_draft, publish_template, as_account):
    """A second course of Sara's that cites no book."""
    teacher_draft(path_id="topic_other")
    template_id = publish_template(path_id="topic_other", students=("sara",))["template"]["id"]
    with as_account("sara"):
        asyncio.run(mastery_path.list_topics())
    return template_id


def test_a_book_derived_kb_original_needs_the_paths_book(
    real_book_path, real_path_kb_only, teaching_http
):
    """Preflight item 4: path -> canonical book -> KB, resolved by the server."""
    url = _files_url(real_book_path.kb.ref, "doc.md")
    assert teaching_http.client.get(url, headers=_auth("sara")).status_code == 404
    opened = teaching_http.client.get(
        f"{url}?mastery_template={real_book_path.template_id}", headers=_auth("sara")
    )
    assert opened.status_code == 200 and "lambda" in opened.text
    # Another course of Sara's does not cite that book: no reading scope.
    assert (
        teaching_http.client.get(
            f"{url}?mastery_template={real_path_kb_only}", headers=_auth("sara")
        ).status_code
        == 404
    )


def test_study_page_opens_a_reference_only_book_with_the_path_context(
    real_book_path, teaching_http
):
    assert book_registry.get_book("bk_real").visibility == "private"
    assert teaching_http.client.get("/api/books/bk_real", headers=_auth("sara")).status_code != 200
    scoped = {**_auth("sara"), "X-DeepTutor-Mastery-Template": real_book_path.template_id}
    assert teaching_http.client.get("/api/books/bk_real", headers=scoped).status_code == 200
    assert teaching_http.client.get("/api/books/bk_real/spine", headers=scoped).status_code == 200
    page = teaching_http.client.get("/api/books/bk_real/pages/pg_1", headers=scoped)
    assert page.status_code == 200
    assert "Nodes stay still." in page.text
    assert (
        teaching_http.client.get(
            "/api/books/bk_real",
            headers={**_auth("sam"), "X-DeepTutor-Mastery-Template": real_book_path.template_id},
        ).status_code
        == 403
    )
    # Writes and non-original routes keep their normal rules.
    assert (
        teaching_http.client.get("/api/books/bk_real/sharing", headers=scoped).status_code != 200
    )


def test_originals_follow_the_revision_the_copy_is_on(
    real_path, teaching_users, as_account, make_kb, publish_template, teaching_http
):
    """Preflight item 6 for originals: a waiting copy opens only its own revision's."""
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    from deeptutor.learning.models import PendingQuestion

    with as_account("tina"):
        make_kb(KnowledgeBaseManager(base_dir=str(real_path.root)), "new-keys", text="new keys")
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
    publish_template(kb_choices={k2.kb_id: "reference_only"})
    ctx = f"mastery_template={real_path.template_id}"
    old = f"{_files_url(real_path.record.ref, 'doc.md')}?{ctx}"
    new = f"{_files_url(k2.ref, 'doc.md')}?{ctx}"
    get = teaching_http.client.get

    assert get(old, headers=_auth("sara")).status_code == 200
    assert get(new, headers=_auth("sara")).status_code == 404

    skipped = teaching_http.client.post(
        f"/api/mastery-paths/progress/{real_path.path_id}/skip-question",
        json={"question_id": "q-open"},
        headers=_auth("sara"),
    )
    assert skipped.status_code == 200, skipped.text
    assert get(f"/api/mastery-paths/topics/{real_path.path_id}", headers=_auth("sara")).json()[
        "origin"
    ]["revision"] == 2
    assert get(new, headers=_auth("sara")).status_code == 200
    assert get(old, headers=_auth("sara")).status_code == 404


# ---- re-assigning a student parked on an older revision (B2-1) -----------------


def _park_on_v1_then_publish_v2(real_path, teaching_users, as_account, make_kb, publish_template):
    """Sara waits on a v1 question; v2 replaces K1 with K2; Sara is revoked."""
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    from deeptutor.learning.models import PendingQuestion

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
    publish_template(kb_choices={k2.kb_id: "reference_only"})
    with as_account("tina"):
        TeachingService().revoke(real_path.template_id, teaching_users["sara"]["id"])
    # Revoking released K1: no active student needs revision 1 any more.
    assert kb_registry.list_referenced("mastery_template", real_path.template_id) == [
        kb_registry.get_kb(k2.kb_id)
    ]
    return k2


@pytest.mark.asyncio
async def test_reassigning_restores_the_old_revisions_materials(
    real_path, teaching_users, as_account, make_kb, publish_template, monkeypatch
):
    """Final review C M-a: the re-assigned student's v1 question is answerable again."""
    from deeptutor.multi_user.knowledge_access import resolve_for_rag

    k2 = await asyncio.to_thread(
        _park_on_v1_then_publish_v2,
        real_path, teaching_users, as_account, make_kb, publish_template,
    )
    with as_account("tina"):
        await asyncio.to_thread(
            TeachingService().assign, real_path.template_id, [teaching_users["sara"]["id"]]
        )
    assert {
        record.kb_id
        for record in kb_registry.list_referenced("mastery_template", real_path.template_id)
    } == {real_path.record.kb_id, k2.kb_id}

    seen: dict = {}

    async def body(context):
        seen["kbs"] = list(context.knowledge_bases)
        seen["k1"] = resolve_for_rag(real_path.record.ref).name
        return True

    ran = _probe(monkeypatch, body)
    stream = _Stream()
    with as_account("sara"):
        await MasteryLoopPipeline(language="en").run(_context(real_path.path_id), stream)
        origin = LearningStore().load_origin(real_path.path_id)
    assert stream.errors == [] and ran == [True]
    assert origin.revision == 1
    assert seen == {"kbs": [real_path.record.ref], "k1": "answer-keys"}


@pytest.mark.asyncio
async def test_reassigning_does_not_restore_a_deleted_old_material(
    real_path, teaching_users, as_account, make_kb, publish_template, monkeypatch
):
    """A K1 deleted meanwhile stays unavailable; the assignment itself still succeeds."""
    k2 = await asyncio.to_thread(
        _park_on_v1_then_publish_v2,
        real_path, teaching_users, as_account, make_kb, publish_template,
    )
    with as_account("tina"):
        kb_registry.unregister_kb(real_path.record.kb_id)
        rows = await asyncio.to_thread(
            TeachingService().assign, real_path.template_id, [teaching_users["sara"]["id"]]
        )
    assert [row["status"] for row in rows] == ["active"]
    assert {
        record.kb_id
        for record in kb_registry.list_referenced("mastery_template", real_path.template_id)
    } == {k2.kb_id}

    ran = _probe(monkeypatch, None)
    stream = _Stream()
    with as_account("sara"):
        await MasteryLoopPipeline(language="en").run(_context(real_path.path_id), stream)
    assert ran == []
    assert stream.errors[0][0] == MATERIALS_UNAVAILABLE


@pytest.mark.asyncio
async def test_reassigning_does_not_restore_an_old_material_its_owner_made_private(
    teaching_users, as_account, make_kb, teacher_draft, publish_template, monkeypatch
):
    """K1 was cited as Adam's public KB; Adam made it private after the release."""
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    from deeptutor.multi_user.paths import get_account_path_service

    with as_account("adam"):
        root = get_account_path_service().get_knowledge_bases_root()
        root.mkdir(parents=True, exist_ok=True)
        make_kb(KnowledgeBaseManager(base_dir=str(root)), "algebra")
        k1 = kb_registry.ensure_registered(
            root, "algebra", created_by=teaching_users["adam"]["id"]
        )
        kb_registry.set_visibility(k1.kb_id, "public")
    teacher_draft(
        sources=(
            TopicSource(
                id="s0", kind=TopicSourceKind.KNOWLEDGE_BASE, source_id=k1.ref,
                label="Algebra notes", position=0,
            ),
        )
    )
    template_id = (await asyncio.to_thread(publish_template, students=("sara",)))[
        "template"
    ]["id"]
    with as_account("sara"):
        path_id = (await mastery_path.list_topics())["topics"][0]["path_id"]
    with as_account("tina"):
        tina_root = get_account_path_service().get_knowledge_bases_root()
        tina_root.mkdir(parents=True, exist_ok=True)
    k2 = await asyncio.to_thread(
        _park_on_v1_then_publish_v2,
        SimpleNamespace(
            record=k1, template_id=template_id, path_id=path_id, root=tina_root
        ),
        teaching_users, as_account, make_kb, publish_template,
    )
    with as_account("adam"):
        kb_registry.set_visibility(k1.kb_id, "private")
    with as_account("tina"):
        rows = await asyncio.to_thread(
            TeachingService().assign, template_id, [teaching_users["sara"]["id"]]
        )
    assert [row["status"] for row in rows] == ["active"]
    assert {
        record.kb_id for record in kb_registry.list_referenced("mastery_template", template_id)
    } == {k2.kb_id}

    ran = _probe(monkeypatch, None)
    stream = _Stream()
    with as_account("sara"):
        await MasteryLoopPipeline(language="en").run(_context(path_id), stream)
    assert ran == []
    assert stream.errors[0][0] == MATERIALS_UNAVAILABLE


def test_reassigning_restores_an_old_revisions_reference_only_book(
    teaching_books, teaching_users, teacher_draft, publish_template, as_account, tmp_path
):
    """The book half of M-a: a released v1-only private book is cited again."""
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
        asyncio.run(mastery_path.list_topics())
    with as_account("tina"):
        store = LearningStore()
        topic = store.get_topic("topic_calculus")
        store.put_topic(topic.metadata, [_book_source("bk_own", "Waves")])
    publish_template(book_choices={"bk_own": "reference_only"})
    sara = teaching_users["sara"]["id"]

    def held() -> set[str]:
        return {
            r.book_id for r in book_registry.list_referenced_books("mastery_template", template_id)
        }

    with as_account("tina"):
        TeachingService().revoke(template_id, sara)
        assert held() == {"bk_own"}
        TeachingService().assign(template_id, [sara])
    assert held() == {"bk_own", "bk_two"}
