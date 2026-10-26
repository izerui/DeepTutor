"""Reference-only knowledge bases open only inside the path that references them."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from deeptutor.agents.loop.pipeline import AgenticLoopPipeline
from deeptutor.api.routers import mastery_path
from deeptutor.capabilities.mastery.pipeline import MasteryLoopPipeline
from deeptutor.core.context import UnifiedContext
from deeptutor.knowledge import registry
from deeptutor.learning.models import TopicSource, TopicSourceKind
from deeptutor.multi_user.context import get_current_user
from deeptutor.multi_user.retrieval_scope import turn_retrieval_scope
from deeptutor.teaching import access
from deeptutor.teaching.access import studying_template
from deeptutor.teaching.service import TeachingService


@pytest.fixture
def kb_instance(fake_kb_registry, teaching_users, teacher_draft, publish_template, as_account):
    fake_kb_registry.add(
        "kb_answers",
        visibility="private",
        created_by=teaching_users["tina"]["id"],
        legacy_refs=("user:kb:answers",),
    )
    teacher_draft(
        sources=(
            TopicSource(
                id="s0",
                kind=TopicSourceKind.KNOWLEDGE_BASE,
                source_id="user:kb:answers",
                label="Answer keys",
                position=0,
            ),
        )
    )
    result = publish_template(students=("sara",), kb_choices={"kb_answers": "reference_only"})
    with as_account("sara"):
        topics = asyncio.run(mastery_path.list_topics())["topics"]
    return SimpleNamespace(path_id=topics[0]["path_id"], template_id=result["template"]["id"])


def test_readers_are_builtin_and_register_through_functions(monkeypatch):
    from deeptutor.book import registry as book_registry

    assert "deeptutor.teaching.access" in registry.BUILTIN_READER_MODULES
    assert "deeptutor.teaching.access" in book_registry.BUILTIN_BOOK_READER_MODULES
    kb_calls, book_calls = [], []
    monkeypatch.setattr(
        registry, "register_reference_reader", lambda kind, reader: kb_calls.append((kind, reader))
    )
    monkeypatch.setattr(
        book_registry,
        "register_book_reference_reader",
        lambda kind, reader: book_calls.append((kind, reader)),
    )

    access.register_kb_readers()
    access.register_kb_reader()  # plan B's loader hook
    access.register_book_readers()

    assert kb_calls == [("mastery_template", access.template_reader)] * 2
    assert book_calls == [("mastery_template", access.template_reader)]


def test_reference_only_kb_is_readable_only_while_studying_the_path(
    kb_instance, fake_kb_registry, as_account, teaching_users
):
    record = fake_kb_registry.records["kb_answers"]
    with as_account("sara"):
        user = get_current_user()
        assert registry.can_read(record, user) is False  # chat, writing, anywhere else
        with studying_template(kb_instance.template_id):
            assert registry.can_read(record, user) is True
        with studying_template("tpl_other"):
            assert registry.can_read(record, user) is False

    with as_account("tina"):
        TeachingService().revoke(kb_instance.template_id, teaching_users["sara"]["id"])
    with as_account("sara"):
        with studying_template(kb_instance.template_id):
            assert registry.can_read(record, get_current_user()) is False


def test_a_turn_scope_on_the_path_studies_it_and_follows_its_switches(
    kb_instance, fake_kb_registry, as_account
):
    record = fake_kb_registry.records["kb_answers"]
    with as_account("sara"):
        user = get_current_user()
        with turn_retrieval_scope() as scope:
            assert registry.can_read(record, user) is False  # unrestricted: own path
            scope.restrict({"kb_answers"}, template_id=kb_instance.template_id)
            assert access.studied_template() == kb_instance.template_id
            assert registry.can_read(record, user) is True
            scope.restrict(set(), template_id="tpl_other")  # switched to another course
            assert registry.can_read(record, user) is False
            scope.clear()  # switched to an own path
            assert registry.can_read(record, user) is False
        assert access.studied_template() == ""


def test_the_scope_never_answers_for_another_account(kb_instance, fake_kb_registry, as_account):
    record = fake_kb_registry.records["kb_answers"]
    with as_account("sam"):
        sam = get_current_user()
    with as_account("sara"):
        with studying_template(kb_instance.template_id):
            assert access.template_reader(kb_instance.template_id, sam.id) is False
            assert registry.can_read(record, sam) is False


def test_archiving_drops_the_references(kb_instance, fake_kb_registry, as_account):
    with as_account("tina"):
        TeachingService().archive(kb_instance.template_id)
    assert ("mastery_template", kb_instance.template_id) not in fake_kb_registry.references
    with as_account("sara"):
        assert access.open_template_for_current_user(kb_instance.template_id) is False


def test_topic_reports_unavailable_materials(kb_instance, fake_kb_registry, as_account):
    with as_account("sara"):
        assert asyncio.run(mastery_path.get_topic(kb_instance.path_id))["origin"][
            "materials_available"
        ] is True
    del fake_kb_registry.records["kb_answers"]  # an admin force-deleted the library
    with as_account("sara"):
        assert asyncio.run(mastery_path.get_topic(kb_instance.path_id))["origin"][
            "materials_available"
        ] is False


class _Stream:
    def __init__(self) -> None:
        self.errors: list[tuple[str, dict]] = []

    async def error(self, message, source="", stage="", metadata=None):
        self.errors.append((message, dict(metadata or {})))


def _context(path_id: str) -> UnifiedContext:
    return UnifiedContext(
        enabled_tools=[],
        knowledge_bases=[],
        metadata={"mastery_mode": True, "mastery_path_id": path_id, "mastery_session_mode": "study"},
    )


@pytest.mark.asyncio
async def test_the_tutor_reads_reference_only_material_inside_the_turn(
    kb_instance, fake_kb_registry, as_account, monkeypatch
):
    seen: dict = {}

    async def _loop(self, context, _stream):
        seen["readable"] = registry.can_read(
            fake_kb_registry.records["kb_answers"], get_current_user()
        )
        seen["kbs"] = list(context.knowledge_bases)
        return {}

    monkeypatch.setattr(AgenticLoopPipeline, "run", _loop)
    monkeypatch.setattr("deeptutor.capabilities.setup.binding._intro_pending", lambda _c: False)

    with as_account("sara"):
        await MasteryLoopPipeline(language="en").run(_context(kb_instance.path_id), _Stream())
        after = registry.can_read(fake_kb_registry.records["kb_answers"], get_current_user())

    assert seen == {"readable": True, "kbs": ["kb:kb_answers"]}
    assert after is False  # the turn's scope closed with it


@pytest.mark.asyncio
async def test_missing_materials_stop_the_tutor_instead_of_improvising(
    kb_instance, fake_kb_registry, as_account, monkeypatch
):
    ran: list[bool] = []

    async def _loop(self, context, _stream):
        ran.append(True)
        return {}

    monkeypatch.setattr(AgenticLoopPipeline, "run", _loop)
    monkeypatch.setattr("deeptutor.capabilities.setup.binding._intro_pending", lambda _c: False)
    del fake_kb_registry.records["kb_answers"]
    stream = _Stream()

    with as_account("sara"):
        await MasteryLoopPipeline(language="en").run(_context(kb_instance.path_id), stream)

    assert ran == []
    message, metadata = stream.errors[0]
    assert metadata == {"code": "teacher_materials_unavailable", "materials": ["Answer keys"]}
    assert "unavailable" in message.lower()


def test_an_admin_forced_revocation_marks_the_path_unavailable(
    fake_kb_registry, teaching_users, teacher_draft, publish_template, as_account
):
    # Spec 7.4: an admin forces another account's public KB back to private.
    fake_kb_registry.add(
        "kb_algebra",
        visibility="public",
        created_by=teaching_users["adam"]["id"],
        name="Algebra",
        legacy_refs=("admin:kb:algebra",),
    )
    teacher_draft(
        sources=(
            TopicSource(
                id="s0",
                kind=TopicSourceKind.KNOWLEDGE_BASE,
                source_id="admin:kb:algebra",
                label="Algebra notes",
                position=0,
            ),
        )
    )
    template_id = publish_template(students=("sara",))["template"]["id"]
    with as_account("sara"):
        path_id = asyncio.run(mastery_path.list_topics())["topics"][0]["path_id"]

    fake_kb_registry.force_private("kb_algebra")

    with as_account("tina"):
        (described,) = TeachingService().list_for_actor()
        preview = asyncio.run(TeachingService().preview_publish(template_id))
    # The teacher sees it on the path, and cannot simply republish it.
    assert described["materials_unavailable"] == ["Algebra notes"]
    assert [issue["code"] for issue in preview["source_issues"]] == ["not_shareable"]
    # The student sees "course materials unavailable"; the tutor stops (see above).
    with as_account("sara"):
        payload = asyncio.run(mastery_path.get_topic(path_id))
    assert payload["origin"]["materials_available"] is False
