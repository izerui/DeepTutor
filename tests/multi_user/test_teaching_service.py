"""Teacher-side operations: register, preview, publish, assign, archive, delete."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import pytest

from deeptutor.knowledge import registry
from deeptutor.learning.models import QuizAttempt, TopicSource, TopicSourceKind
from deeptutor.learning.service import LearningService
from deeptutor.learning.storage import LearningStore
from deeptutor.teaching import service as service_module
from deeptutor.teaching.models import KpDecision, PublishRequest
from deeptutor.teaching.service import (
    InvalidStudents,
    PublishBlocked,
    TeachingService,
    TemplateConflict,
    TemplateForbidden,
    TemplateNotFound,
)
from deeptutor.teaching.store import TeachingStore


def _kb_source(index: int, ref: str, label: str) -> TopicSource:
    return TopicSource(
        id=f"s{index}",
        kind=TopicSourceKind.KNOWLEDGE_BASE,
        source_id=ref,
        label=label,
        position=index,
    )


def _preview(as_account, template_id: str, name: str = "tina") -> dict:
    with as_account(name):
        return asyncio.run(TeachingService().preview_publish(template_id))


def test_first_publish_snapshots_the_outline_but_not_the_teachers_progress(
    as_account, teacher_draft, publish_template, teaching_users
):
    path_id = teacher_draft()
    with as_account("tina"):
        store = LearningStore()

        def practise(tx):
            point = tx.progress.modules[0].knowledge_points[0]
            tx.progress.mastery_levels[point.id] = 0.9
            tx.progress.quiz_attempts.append(
                QuizAttempt(question_id="q1", knowledge_point_id=point.id, is_correct=True)
            )
            tx.touch()

        store.mutate(path_id, practise)

    result = publish_template(students=("sara",))

    template_id = result["template"]["id"]
    revision = TeachingStore().get_revision(template_id, 1)
    assert result["revision"] == 1
    assert result["warnings"] == []
    assert {entry.kind for entry in revision.change_log.values()} == {"added"}
    assert [p.name for p in revision.outline.modules[0].knowledge_points] == [
        "Limits",
        "Derivatives",
        "Chain rule",
    ]
    dumped = revision.model_dump_json()
    assert "mastery_levels" not in dumped
    assert "quiz_attempts" not in dumped
    assignment = TeachingStore().get_assignment(template_id, teaching_users["sara"]["id"])
    assert assignment.status == "active"


def test_registering_twice_returns_the_same_template(as_account, teacher_draft):
    teacher_draft()
    with as_account("tina"):
        service = TeachingService()
        assert service.register("topic_calculus", "").id == service.register(
            "topic_calculus", ""
        ).id


def test_private_kbs_are_published_or_shared_by_reference(
    as_account, teacher_draft, publish_template, teaching_users, fake_kb_registry
):
    tina = teaching_users["tina"]["id"]
    fake_kb_registry.add(
        "kb_notes", visibility="private", created_by=tina, legacy_refs=("user:kb:notes",)
    )
    fake_kb_registry.add(
        "kb_answers", visibility="private", created_by=tina, legacy_refs=("user:kb:answers",)
    )
    teacher_draft(
        sources=(
            _kb_source(0, "user:kb:notes", "Notes"),
            _kb_source(1, "user:kb:answers", "Answers"),
        )
    )

    result = publish_template(kb_choices={"kb_answers": "reference_only"})

    template_id = result["template"]["id"]
    assert fake_kb_registry.records["kb_notes"].visibility == "public"
    assert fake_kb_registry.records["kb_answers"].visibility == "private"
    assert sorted(fake_kb_registry.references[("mastery_template", template_id)]) == [
        ("kb_answers", None),
        ("kb_notes", None),
    ]
    revision = TeachingStore().get_revision(template_id, 1)
    assert {ref.kb_id: ref.access for ref in revision.sources.kb_refs} == {
        "kb_notes": "public",
        "kb_answers": "reference_only",
    }
    assert {s.source_id for s in revision.sources.sources} == {"kb:kb_notes", "kb:kb_answers"}


def test_a_republish_keeps_the_reference_only_choice(
    as_account, teacher_draft, publish_template, teaching_users, fake_kb_registry
):
    fake_kb_registry.add(
        "kb_answers",
        visibility="private",
        created_by=teaching_users["tina"]["id"],
        legacy_refs=("user:kb:answers",),
    )
    teacher_draft(sources=(_kb_source(0, "user:kb:answers", "Answers"),))
    template_id = publish_template(kb_choices={"kb_answers": "reference_only"})["template"]["id"]

    preview = _preview(as_account, template_id)
    assert preview["private_kbs"][0]["default"] == "reference_only"

    publish_template()  # no explicit choice this time
    assert fake_kb_registry.records["kb_answers"].visibility == "private"


def test_file_sources_reference_fixed_document_ids(
    as_account, teacher_draft, publish_template, fake_kb_registry
):
    fake_kb_registry.add("kb_lib", visibility="public", legacy_refs=("library",))
    fake_kb_registry.write_file("kb_lib", "ch1.md")
    teacher_draft(
        sources=(
            TopicSource(
                id="f0",
                kind=TopicSourceKind.FILE,
                source_id="ch1.md",
                label="Chapter 1",
                position=0,
                metadata={"kb_name": "library"},
            ),
        )
    )

    template_id = publish_template()["template"]["id"]

    assert fake_kb_registry.references[("mastery_template", template_id)] == [
        ("kb_lib", ["doc_1"])
    ]
    (ref,) = TeachingStore().get_revision(template_id, 1).sources.kb_refs
    assert [(d.doc_id, d.version) for d in ref.documents] == [("doc_1", 1)]


def test_personal_sources_block_publishing(as_account, teacher_draft, quiet_change_model):
    teacher_draft(
        sources=(
            TopicSource(
                id="s0",
                kind=TopicSourceKind.NOTEBOOK,
                source_id="nb1",
                label="My notes",
                position=0,
            ),
        )
    )
    with as_account("tina"):
        service = TeachingService()
        template = service.register("topic_calculus", "")
        preview = asyncio.run(service.preview_publish(template.id))
        assert preview["can_publish"] is False
        assert [issue["code"] for issue in preview["source_issues"]] == ["personal_source"]
        with pytest.raises(PublishBlocked):
            asyncio.run(
                service.publish(
                    template.id, PublishRequest(draft_revision=preview["draft_revision"])
                )
            )


def test_a_failed_make_public_aborts_before_anything_is_written(
    as_account, teacher_draft, publish_template, teaching_users, fake_kb_registry, monkeypatch
):
    fake_kb_registry.add(
        "kb_notes",
        visibility="private",
        created_by=teaching_users["tina"]["id"],
        legacy_refs=("user:kb:notes",),
    )
    teacher_draft(sources=(_kb_source(0, "user:kb:notes", "Notes"),))

    def refuse(kb_id, visibility, *, force=False):
        raise registry.KbVisibilityError("Still referenced elsewhere")

    monkeypatch.setattr(registry, "set_visibility", refuse)

    with pytest.raises(PublishBlocked) as blocked:
        publish_template(students=("sara",))

    assert blocked.value.detail["failed_step"] == "make_public:kb:kb_notes"
    assert blocked.value.detail["completed_steps"] == ["documents"]
    template = TeachingStore().list_templates()[0]
    assert template.current_revision == 0
    assert TeachingStore().list_assignments(template.id) == []


def test_a_failed_reference_write_is_reported_as_a_warning(
    as_account, teacher_draft, publish_template, fake_kb_registry, monkeypatch
):
    fake_kb_registry.add("kb_lib", visibility="public", legacy_refs=("library",))
    teacher_draft(sources=(_kb_source(0, "library", "Library"),))

    def broken(*_args, **_kwargs):
        raise registry.KbRegistryError("registry is read-only")

    monkeypatch.setattr(registry, "replace_references", broken)

    result = publish_template(students=("sara",))

    assert result["revision"] == 1
    assert [w["step"] for w in result["warnings"]] == ["knowledge_base_references"]
    assert TeachingStore().count_active(result["template"]["id"]) == 1


def test_sources_are_resolved_in_the_drafts_workspace(
    as_account, teacher_draft, teaching_users, quiet_change_model, monkeypatch
):
    teacher_draft()
    template = TeachingStore().create_template(
        owner_user_id=teaching_users["tina"]["id"],
        title="Calculus I",
        description="",
        draft_path_id="topic_calculus",
        draft_workspace_id="ws_course",
    )
    scope: ContextVar[str] = ContextVar("scope", default="")
    seen: list[str] = []

    @contextmanager
    def fake_scope(workspace_id):
        token = scope.set(workspace_id)
        try:
            yield
        finally:
            scope.reset(token)

    real_plan = service_module.plan_sources

    def recording_plan(*args, **kwargs):
        seen.append(scope.get())
        return real_plan(*args, **kwargs)

    monkeypatch.setattr(service_module, "_scope", fake_scope)
    monkeypatch.setattr(service_module, "plan_sources", recording_plan)

    _preview(as_account, template.id)

    assert seen == ["ws_course"]


def test_a_stale_preview_cannot_be_published(as_account, teacher_draft, quiet_change_model):
    teacher_draft()
    with as_account("tina"):
        service = TeachingService()
        template = service.register("topic_calculus", "")
        preview = asyncio.run(service.preview_publish(template.id))
        LearningService(LearningStore()).rename_path("topic_calculus", "Calculus I (v2)")
        with pytest.raises(TemplateConflict):
            asyncio.run(
                service.publish(
                    template.id, PublishRequest(draft_revision=preview["draft_revision"])
                )
            )


def test_only_active_student_accounts_can_be_assigned(
    as_account, teacher_draft, publish_template, teaching_users
):
    teacher_draft()
    template_id = publish_template()["template"]["id"]
    with as_account("tina"):
        with pytest.raises(InvalidStudents):
            TeachingService().assign(template_id, [teaching_users["uma"]["id"]])
        rows = TeachingService().assign(template_id, [teaching_users["sam"]["id"]])
    assert [row["username"] for row in rows] == ["sam"]


def test_ownership_rules_d17(as_account, teacher_draft, publish_template):
    teacher_draft()
    template_id = publish_template()["template"]["id"]

    with as_account("tom"):
        service = TeachingService()
        with pytest.raises(TemplateNotFound):
            service.get(template_id)
        assert service.list_for_actor() == []

    with as_account("adam"):
        service = TeachingService()
        assert [t["id"] for t in service.list_for_actor()] == [template_id]
        assert service.assignments(template_id) == []
        with pytest.raises(TemplateForbidden):
            asyncio.run(service.preview_publish(template_id))


def test_delete_only_before_publishing_and_archive_afterwards(
    as_account, teacher_draft, publish_template, fake_kb_registry
):
    teacher_draft(path_id="topic_unpublished")
    with as_account("tina"):
        service = TeachingService()
        unpublished = service.register("topic_unpublished", "")
        service.delete(unpublished.id)
        assert TeachingStore().get_template(unpublished.id) is None

    teacher_draft()
    template_id = publish_template()["template"]["id"]
    with as_account("tina"):
        service = TeachingService()
        with pytest.raises(TemplateConflict):
            service.delete(template_id)
        assert service.archive(template_id).status == "archived"
    assert ("mastery_template", template_id) not in fake_kb_registry.references


def test_republishing_records_the_confirmed_changes(
    as_account, teacher_draft, publish_template, edit_calculus_draft
):
    teacher_draft()
    template_id = publish_template(students=("sara",))["template"]["id"]
    ids = edit_calculus_draft()
    assert ids.renamed != ids.derivatives

    preview = _preview(as_account, template_id)
    new_points = {item["kp_id"] for item in preview["changes"]["items"] if item["status"] == "new"}
    assert new_points == {ids.renamed, ids.integrals}
    assert preview["affected_students"] == 1

    result = publish_template(
        kp_decisions={
            ids.limits: KpDecision(kind="redefined"),
            ids.renamed: KpDecision(kind="reworded", from_id=ids.derivatives),
        }
    )

    revision = TeachingStore().get_revision(template_id, 2)
    assert [p.id for p in revision.outline.modules[0].knowledge_points] == [
        ids.limits,
        ids.derivatives,
        ids.integrals,
    ]
    assert {k: v.kind for k, v in revision.change_log.items()} == {
        ids.limits: "redefined",
        ids.derivatives: "reworded",
        ids.integrals: "added",
        ids.chain: "removed",
    }
    assert TeachingStore().get_template(template_id).kp_aliases == {
        ids.renamed: ids.derivatives
    }
    assert result["affected_students"] == 1


def test_student_picker_returns_only_public_fields(as_account, teaching_users):
    with as_account("tina"):
        students = TeachingService().list_students()
        filtered = TeachingService().list_students("sa")
    assert sorted(s["username"] for s in students) == ["sam", "sara"]
    assert set(students[0]) == {"id", "username", "avatar"}
    assert sorted(s["username"] for s in filtered) == ["sam", "sara"]


# ---- identity before planning (C3 deferred finding, D17) -------------------


def test_only_the_owner_reaches_source_planning(
    as_account, teacher_draft, publish_template, monkeypatch
):
    teacher_draft()
    template_id = publish_template()["template"]["id"]
    calls: list[str] = []
    real_plan = service_module.plan_sources

    def recording_plan(sources, user, **kwargs):
        calls.append(user.id)
        return real_plan(sources, user, **kwargs)

    monkeypatch.setattr(service_module, "plan_sources", recording_plan)

    with as_account("adam"):
        service = TeachingService()
        with pytest.raises(TemplateForbidden):
            asyncio.run(service.preview_publish(template_id))
        with pytest.raises(TemplateForbidden):
            asyncio.run(service.publish(template_id, PublishRequest(draft_revision=0)))
    with as_account("tom"):
        with pytest.raises(TemplateNotFound):
            asyncio.run(TeachingService().publish(template_id, PublishRequest(draft_revision=0)))
    assert calls == []

    template = TeachingStore().get_template(template_id)
    with as_account("tina"):
        _progress, topic = TeachingService()._load_draft(template)
    with as_account("tom"):
        # Even an internal caller cannot plan under someone else's identity.
        with pytest.raises(TemplateForbidden):
            TeachingService()._plan(template, topic)
    assert calls == []


# ---- pre-version failures abort; post-version failures warn -----------------


def test_a_failed_document_registration_aborts_first(
    teacher_draft, publish_template, fake_kb_registry, monkeypatch
):
    fake_kb_registry.add("kb_lib", visibility="public", legacy_refs=("library",))
    fake_kb_registry.write_file("kb_lib", "ch1.md")
    teacher_draft(
        sources=(
            TopicSource(
                id="f0",
                kind=TopicSourceKind.FILE,
                source_id="ch1.md",
                label="Chapter 1",
                position=0,
                metadata={"kb_name": "library"},
            ),
        )
    )

    def broken(*_args, **_kwargs):
        raise registry.KbRegistryError("documents table is locked")

    monkeypatch.setattr(registry, "upsert_documents", broken)

    with pytest.raises(PublishBlocked) as blocked:
        publish_template(students=("sara",))

    assert blocked.value.detail["failed_step"] == "documents"
    assert blocked.value.detail["completed_steps"] == []
    template = TeachingStore().list_templates()[0]
    assert template.current_revision == 0
    assert TeachingStore().get_revision(template.id, 1) is None
    assert TeachingStore().list_assignments(template.id) == []
    assert fake_kb_registry.references == {}


@pytest.mark.parametrize("step", ["book_references", "assignments"])
def test_post_version_failures_are_warnings_not_false_success(
    teacher_draft, publish_template, fake_kb_registry, monkeypatch, step
):
    from deeptutor.book import registry as book_registry

    fake_kb_registry.add("kb_lib", visibility="public", legacy_refs=("library",))
    teacher_draft(sources=(_kb_source(0, "library", "Library"),))

    def broken(*_args, **_kwargs):
        raise RuntimeError(f"{step} store is read-only")

    if step == "book_references":
        monkeypatch.setattr(book_registry, "replace_book_references", broken)
    else:
        monkeypatch.setattr(TeachingStore, "assign", broken)

    result = publish_template(students=("sara",))

    assert result["revision"] == 1
    assert result["warnings"] == [{"step": step, "message": f"{step} store is read-only"}]
    template_id = result["template"]["id"]
    assert TeachingStore().get_revision(template_id, 1) is not None
    # The KB reference step before the failing one still happened.
    assert fake_kb_registry.references[("mastery_template", template_id)] == [("kb_lib", None)]
    assert TeachingStore().count_active(template_id) == (0 if step == "assignments" else 1)


# ---- explicit reference_only consent ---------------------------------------


def test_only_explicit_reference_only_choices_record_private_intent(
    as_account, teacher_draft, publish_template, teaching_users, fake_kb_registry
):
    tina = teaching_users["tina"]["id"]
    fake_kb_registry.add(
        "kb_answers", visibility="private", created_by=tina, legacy_refs=("user:kb:answers",)
    )
    fake_kb_registry.add("kb_lib", visibility="public", legacy_refs=("library",))
    teacher_draft(
        sources=(
            _kb_source(0, "user:kb:answers", "Answers"),
            _kb_source(1, "library", "Library"),
        )
    )
    with as_account("tina"):
        service = TeachingService()
        template = service.register("topic_calculus", "")
        key = ("mastery_template", template.id)
        asyncio.run(service.preview_publish(template.id))
    # A preview is not consent and writes nothing.
    assert fake_kb_registry.references == {}
    assert fake_kb_registry.records["kb_answers"].visibility == "private"

    publish_template(
        kb_choices={
            "kb_answers": "reference_only",
            # Neither a public KB nor a key the path does not cite is a private choice.
            "kb_lib": "reference_only",
            "kb_unrelated": "reference_only",
        }
    )
    assert fake_kb_registry.reference_options[key]["private_intent_kb_ids"] == ["kb_answers"]

    publish_template()  # the earlier choice is the default now, not new consent
    assert "private_intent_kb_ids" not in fake_kb_registry.reference_options[key]
    assert fake_kb_registry.records["kb_answers"].visibility == "private"
    revision = TeachingStore().get_revision(template.id, 2)
    assert {ref.kb_id: ref.access for ref in revision.sources.kb_refs} == {
        "kb_answers": "reference_only",
        "kb_lib": "public",
    }


# ---- per-applied-revision material protection (preflight item 6) ------------


def _swap_sources(as_account, sources, *, path_id: str = "topic_calculus") -> None:
    with as_account("tina"):
        store = LearningStore()
        topic = store.get_topic(path_id)
        store.put_topic(topic.metadata, list(sources))


def _referenced(fake_kb_registry, template_id: str) -> list[str]:
    return sorted(
        kb_id for kb_id, _docs in fake_kb_registry.references.get(("mastery_template", template_id), [])
    )


@pytest.fixture
def two_libraries(fake_kb_registry):
    fake_kb_registry.add("kb_a", visibility="public", legacy_refs=("lib_a",))
    fake_kb_registry.add("kb_b", visibility="public", legacy_refs=("lib_b",))
    return fake_kb_registry


def test_an_update_keeps_the_old_revisions_materials_for_instances_still_on_it(
    as_account, teacher_draft, publish_template, teaching_users, two_libraries
):
    sara = teaching_users["sara"]["id"]
    teacher_draft(sources=(_kb_source(0, "lib_a", "A"),))
    template_id = publish_template(students=("sara", "sam"))["template"]["id"]
    # Sara's instance is on revision 1 (e.g. a pending old-revision question).
    TeachingStore().record_instance(
        template_id, sara, instance_path_id=f"assigned_{template_id}", applied_revision=1
    )
    _swap_sources(as_account, (_kb_source(0, "lib_b", "B"),))

    result = publish_template()

    assert result["warnings"] == []
    revision = TeachingStore().get_revision(template_id, 2)
    assert [ref.kb_id for ref in revision.sources.kb_refs] == ["kb_b"]
    assert _referenced(two_libraries, template_id) == ["kb_a", "kb_b"]

    # Once her migration commits (C10), the hook drops what nobody needs.
    TeachingStore().record_instance(
        template_id, sara, instance_path_id=f"assigned_{template_id}", applied_revision=2
    )
    service_module.sync_material_references(template_id)
    assert _referenced(two_libraries, template_id) == ["kb_b"]


def test_without_an_instance_on_the_old_revision_its_materials_are_released(
    as_account, teacher_draft, publish_template, two_libraries
):
    teacher_draft(sources=(_kb_source(0, "lib_a", "A"),))
    # Sam is assigned but never opened the path: no instance on revision 1.
    template_id = publish_template(students=("sam",))["template"]["id"]
    _swap_sources(as_account, (_kb_source(0, "lib_b", "B"),))

    publish_template()

    assert _referenced(two_libraries, template_id) == ["kb_b"]


def test_revoking_the_last_student_on_an_old_revision_releases_it_immediately(
    as_account, teacher_draft, publish_template, teaching_users, two_libraries
):
    sara = teaching_users["sara"]["id"]
    teacher_draft(sources=(_kb_source(0, "lib_a", "A"),))
    template_id = publish_template(students=("sara",))["template"]["id"]
    TeachingStore().record_instance(
        template_id, sara, instance_path_id=f"assigned_{template_id}", applied_revision=1
    )
    _swap_sources(as_account, (_kb_source(0, "lib_b", "B"),))
    publish_template()
    assert _referenced(two_libraries, template_id) == ["kb_a", "kb_b"]

    with as_account("tina"):
        TeachingService().revoke(template_id, sara)

    assert _referenced(two_libraries, template_id) == ["kb_b"]


def test_archiving_releases_every_revisions_materials_immediately(
    as_account, teacher_draft, publish_template, teaching_users, two_libraries
):
    sara = teaching_users["sara"]["id"]
    teacher_draft(sources=(_kb_source(0, "lib_a", "A"),))
    template_id = publish_template(students=("sara",))["template"]["id"]
    TeachingStore().record_instance(
        template_id, sara, instance_path_id=f"assigned_{template_id}", applied_revision=1
    )
    _swap_sources(as_account, (_kb_source(0, "lib_b", "B"),))
    publish_template()

    with as_account("adam"):
        TeachingService().archive(template_id)

    assert ("mastery_template", template_id) not in two_libraries.references
    # A late resync (C10) never brings them back for an archived path.
    service_module.sync_material_references(template_id)
    assert ("mastery_template", template_id) not in two_libraries.references


def test_an_old_revision_never_regains_a_revoked_material(
    as_account, teacher_draft, publish_template, teaching_users, two_libraries
):
    sara = teaching_users["sara"]["id"]
    teacher_draft(sources=(_kb_source(0, "lib_a", "A"),))
    template_id = publish_template(students=("sara",))["template"]["id"]
    TeachingStore().record_instance(
        template_id, sara, instance_path_id=f"assigned_{template_id}", applied_revision=1
    )
    two_libraries.force_private("kb_a")  # an admin withdrew it
    _swap_sources(as_account, (_kb_source(0, "lib_b", "B"),))

    publish_template()

    key = ("mastery_template", template_id)
    assert _referenced(two_libraries, template_id) == ["kb_b"]
    # The withdrawal stays visible to the instance still on revision 1.
    assert two_libraries.reference_options[key]["retain_revoked_kb_ids"] == ["kb_a"]


# ---- reference writes are serialized with publishes and archives -----------


def _in_thread(as_account, name: str, action: Callable[[], object]) -> tuple[Any, list]:
    """Run *action* as *name* on another thread; return (thread, errors)."""
    import threading

    errors: list[BaseException] = []

    def run():
        try:
            with as_account(name):
                action()
        except BaseException as exc:  # surfaced by the test
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, errors


def _hook_first_reference_write(monkeypatch, before_write: Callable[[], None]) -> None:
    """Call ``before_write()`` once, just before the first KB reference write."""
    from deeptutor.teaching import kb as kb_access

    real = kb_access.replace_template_references
    fired: list[bool] = []

    def hooked(*args, **kwargs):
        if not fired:
            fired.append(True)
            before_write()
        return real(*args, **kwargs)

    monkeypatch.setattr(kb_access, "replace_template_references", hooked)


def _entered(monkeypatch, method: str):
    """An event set when another caller reaches ``TeachingStore.<method>``."""
    import threading

    event = threading.Event()
    real = getattr(TeachingStore, method)

    def spy(self, *args, **kwargs):
        event.set()
        return real(self, *args, **kwargs)

    monkeypatch.setattr(TeachingStore, method, spy)
    return event


def test_a_resync_racing_a_publish_keeps_the_new_revisions_materials(
    as_account, teacher_draft, publish_template, teaching_users, fake_kb_registry, monkeypatch
):
    sara = teaching_users["sara"]["id"]
    for kb_id, ref in (("kb_a", "lib_a"), ("kb_b", "lib_b"), ("kb_c", "lib_c")):
        fake_kb_registry.add(kb_id, visibility="public", legacy_refs=(ref,))
    teacher_draft(sources=(_kb_source(0, "lib_a", "A"),))
    template_id = publish_template(students=("sara",))["template"]["id"]
    instance = f"assigned_{template_id}"
    TeachingStore().record_instance(template_id, sara, instance_path_id=instance, applied_revision=1)
    _swap_sources(as_account, (_kb_source(0, "lib_b", "B"),))
    publish_template()
    assert _referenced(fake_kb_registry, template_id) == ["kb_a", "kb_b"]
    # Sara migrates to revision 2; the teacher meanwhile prepares revision 3.
    TeachingStore().record_instance(template_id, sara, instance_path_id=instance, applied_revision=2)
    _swap_sources(as_account, (_kb_source(0, "lib_b", "B"), _kb_source(1, "lib_c", "C")))
    add_revision = _entered(monkeypatch, "add_revision")
    racing: dict = {}

    def publish_revision_3_now():
        # The resync has computed its references from revision 2; publish 3.
        racing["thread"], racing["errors"] = _in_thread(as_account, "tina", publish_template)
        assert add_revision.wait(10)
        racing["thread"].join(1.0)  # without the lock this publish would finish here

    _hook_first_reference_write(monkeypatch, publish_revision_3_now)

    service_module.sync_material_references(template_id)
    thread, errors = racing["thread"], racing["errors"]
    thread.join(30)

    assert not thread.is_alive() and errors == []
    assert TeachingStore().get_template(template_id).current_revision == 3
    # Revision 3's new material survived the resync computed from revision 2.
    assert _referenced(fake_kb_registry, template_id) == ["kb_b", "kb_c"]


def test_an_archive_racing_the_reference_write_leaves_nothing_shared(
    as_account, teacher_draft, publish_template, fake_kb_registry, monkeypatch
):
    fake_kb_registry.add("kb_lib", visibility="public", legacy_refs=("library",))
    teacher_draft(sources=(_kb_source(0, "library", "Library"),))
    with as_account("tina"):
        template_id = TeachingService().register("topic_calculus", "").id
    update = _entered(monkeypatch, "update_template")
    racing: dict = {}

    def archive_now():
        racing["thread"], racing["errors"] = _in_thread(
            as_account, "adam", lambda: TeachingService().archive(template_id)
        )
        assert update.wait(10)
        racing["thread"].join(1.0)  # without the lock the archive would finish here

    _hook_first_reference_write(monkeypatch, archive_now)

    result = publish_template()
    thread, errors = racing["thread"], racing["errors"]
    thread.join(30)

    assert not thread.is_alive() and errors == []
    assert result["warnings"] == []
    assert TeachingStore().get_template(template_id).status == "archived"
    assert ("mastery_template", template_id) not in fake_kb_registry.references


def test_a_publish_that_finds_its_path_archived_shares_nothing(
    teacher_draft, publish_template, fake_kb_registry, monkeypatch
):
    fake_kb_registry.add("kb_lib", visibility="public", legacy_refs=("library",))
    teacher_draft(sources=(_kb_source(0, "library", "Library"),))
    real_add = TeachingStore.add_revision

    def add_then_archive(self, revision, **kwargs):
        updated = real_add(self, revision, **kwargs)
        TeachingStore().update_template(revision.template_id, status="archived")
        return updated

    monkeypatch.setattr(TeachingStore, "add_revision", add_then_archive)

    result = publish_template(students=("sara",))

    assert [w["step"] for w in result["warnings"]] == [
        "knowledge_base_references",
        "book_references",
        "assignments",
    ]
    assert fake_kb_registry.references == {}
    assert TeachingStore().list_assignments(result["template"]["id"]) == []
