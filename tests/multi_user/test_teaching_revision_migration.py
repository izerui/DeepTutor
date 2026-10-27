"""A teacher's update reaches each student at their next study start, by the rules."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from deeptutor.agents.loop.pipeline import AgenticLoopPipeline
from deeptutor.api.routers import mastery_path
from deeptutor.capabilities.mastery.pipeline import MasteryLoopPipeline
from deeptutor.core.context import UnifiedContext
from deeptutor.learning import policy as learning_policy
from deeptutor.learning.models import (
    KnowledgePoint,
    KnowledgeType,
    LearningModule,
    LearningProgress,
    PendingQuestion,
    QuizAttempt,
    TopicMetadata,
    TopicSource,
    TopicSourceKind,
)
from deeptutor.learning.scheduler import SpacedRepetitionScheduler
from deeptutor.learning.service import LearningService
from deeptutor.learning.storage import LearningStore
from deeptutor.teaching.instances import (
    acknowledge_update,
    apply_pending_revision,
    instance_source_id,
)
from deeptutor.teaching.models import KpDecision
from deeptutor.teaching.service import TeachingService
from deeptutor.teaching.store import TeachingStore


def _practise_everything(as_account, path_id: str) -> None:
    with as_account("sara"):
        store = LearningStore()

        def apply(tx):
            for point in tx.progress.modules[0].knowledge_points:
                tx.progress.mastery_levels[point.id] = 0.9
                tx.progress.quiz_attempts.append(
                    QuizAttempt(
                        question_id=f"q-{point.id}",
                        knowledge_point_id=point.id,
                        is_correct=True,
                    )
                )
            tx.touch()

        store.mutate(path_id, apply)


def _publish_edit(edit_calculus_draft, publish_template):
    ids = edit_calculus_draft()
    publish_template(
        kp_decisions={
            ids.limits: KpDecision(kind="redefined"),
            ids.renamed: KpDecision(kind="reworded", from_id=ids.derivatives),
        }
    )
    return ids


def _ask(as_account, path_id: str, kp_id: str = "") -> str:
    with as_account("sara"):
        store = LearningStore()
        point = kp_id or store.load(path_id).modules[0].knowledge_points[0].id

        def ask(tx):
            tx.progress.pending_question = PendingQuestion(
                question_id="q-open", knowledge_point_id=point, expected_answer="x"
            )
            tx.touch()

        store.mutate(path_id, ask)
    return point


def _flags(progress: LearningProgress) -> dict[str, bool]:
    return {
        point["id"]: point["needs_reassessment"]
        for module in learning_policy.map_summary(progress)["modules"]
        for point in module["knowledge_points"]
    }


# ---------------------------------------------------------------------------
# The migration rules (spec 5.3)
# ---------------------------------------------------------------------------


def test_update_follows_the_change_rules(
    sara_instance, as_account, edit_calculus_draft, publish_template, teaching_users
):
    _practise_everything(as_account, sara_instance.path_id)
    ids = _publish_edit(edit_calculus_draft, publish_template)

    with as_account("sara"):
        store = LearningStore()
        notice = apply_pending_revision(sara_instance.path_id, store, respect_lease=True)
        progress = store.load(sara_instance.path_id)
        events = [event.event_type for event in store.list_events(sara_instance.path_id)]

    assert (notice.from_revision, notice.to_revision) == (1, 2)
    assert (notice.added, notice.removed, notice.redefined) == (1, 1, 1)
    assert "path.teacher_revision_applied" in events
    points = progress.modules[0].knowledge_points
    assert [p.name for p in points] == ["Limits", "Derivative rules", "Integrals"]
    # Reworded: same stable id, progress kept.
    assert points[1].id == ids.derivatives
    assert progress.mastery_levels[ids.derivatives] == 0.9
    # Redefined: only this point needs re-assessment; its history is voided, not lost.
    assert ids.limits not in progress.mastery_levels
    limits_attempts = [a for a in progress.quiz_attempts if a.knowledge_point_id == ids.limits]
    assert limits_attempts and all(
        a.voided and a.void_reason == learning_policy.REDEFINED_VOID_REASON
        for a in limits_attempts
    )
    assert learning_policy.objective_status(progress, points[0]) == "learning"
    # R11: shown as "needs re-assessment", not as ordinary progress.
    assert _flags(progress) == {ids.limits: True, ids.derivatives: False, ids.integrals: False}
    # Removed: off the map, answers and mastery kept as history.
    assert ids.chain not in {p.id for p in points}
    assert any(a.knowledge_point_id == ids.chain for a in progress.quiz_attempts)
    assert progress.mastery_levels[ids.chain] == 0.9
    # Added: not started.
    assert learning_policy.objective_status(progress, points[2]) == "new"
    assert progress.origin.revision == 2
    assert progress.origin.update_notice.to_revision == 2
    assignment = TeachingStore().get_assignment(
        sara_instance.template_id, teaching_users["sara"]["id"]
    )
    assert assignment.applied_revision == 2
    # The map and the scheduler cope with retired history.
    summary = learning_policy.map_summary(progress)
    assert summary["counts"] == {"mastered": 1, "learning": 1, "new": 1, "total": 3}
    SpacedRepetitionScheduler().build_review_queue(progress)


def test_update_waits_until_the_open_question_is_answered(
    sara_instance, as_account, edit_calculus_draft, publish_template
):
    _ask(as_account, sara_instance.path_id)
    _publish_edit(edit_calculus_draft, publish_template)

    with as_account("sara"):
        store = LearningStore()
        assert apply_pending_revision(sara_instance.path_id, store, respect_lease=True) is None
        assert store.load_origin(sara_instance.path_id).revision == 1

        def answer(tx):
            tx.progress.pending_question = None
            tx.touch()

        store.mutate(sara_instance.path_id, answer)
        notice = apply_pending_revision(sara_instance.path_id, store, respect_lease=True)
    assert notice.to_revision == 2


def test_a_live_turn_defers_the_update_on_read(
    sara_instance, as_account, edit_calculus_draft, publish_template
):
    _publish_edit(edit_calculus_draft, publish_template)
    with as_account("sara"):
        store = LearningStore()
        store.acquire_path_lease(sara_instance.path_id, "s1", "t1", bind_session=False)
        assert apply_pending_revision(sara_instance.path_id, store, respect_lease=True) is None
        # Another turn's lease is never "ours".
        assert (
            apply_pending_revision(
                sara_instance.path_id, store, respect_lease=True, lease_turn_id="t2"
            )
            is None
        )
        assert store.load_origin(sara_instance.path_id).revision == 1
        # The turn holding the lease applies it itself.
        notice = apply_pending_revision(
            sara_instance.path_id, store, respect_lease=True, lease_turn_id="t1"
        )
    assert notice is not None and notice.to_revision == 2


def test_opening_the_path_applies_the_update_and_ack_clears_the_notice(
    sara_instance, as_account, edit_calculus_draft, publish_template
):
    _publish_edit(edit_calculus_draft, publish_template)

    with as_account("sara"):
        payload = asyncio.run(mastery_path.get_topic(sara_instance.path_id))
        assert payload["origin"]["revision"] == 2
        assert payload["origin"]["update_notice"]["to_revision"] == 2
        acked = asyncio.run(mastery_path.acknowledge_teacher_update(sara_instance.path_id))
        assert acked == {"status": "ok", "acknowledged": True}
        again = asyncio.run(mastery_path.get_topic(sara_instance.path_id))
        repeated = asyncio.run(mastery_path.acknowledge_teacher_update(sara_instance.path_id))
        events = [e.event_type for e in LearningStore().list_events(sara_instance.path_id)]
    assert again["origin"]["update_notice"] is None
    assert repeated == {"status": "ok", "acknowledged": False}
    assert "path.teacher_update_acknowledged" in events


def test_acknowledging_a_learners_own_path_does_nothing(teacher_draft, as_account):
    teacher_draft("sara", path_id="topic_own")
    with as_account("sara"):
        store = LearningStore()
        before = store.load("topic_own").version
        assert acknowledge_update("topic_own", store) is False
        assert store.load("topic_own").version == before


def test_unacknowledged_updates_accumulate_into_one_notice(
    sara_instance, as_account, edit_calculus_draft, publish_template
):
    _publish_edit(edit_calculus_draft, publish_template)
    with as_account("sara"):
        apply_pending_revision(sara_instance.path_id, LearningStore(), respect_lease=True)
    # Revision 3 drops "Integrals" again.
    with as_account("tina"):
        store = LearningStore()
        module = store.load("topic_calculus").modules[0].model_copy(deep=True)
        module.knowledge_points = module.knowledge_points[:2]
        LearningService(store).replace_modules_for_path(
            "topic_calculus", [module], identity_mode="explicit"
        )
    publish_template()

    with as_account("sara"):
        notice = apply_pending_revision(sara_instance.path_id, LearningStore(), respect_lease=True)
    assert (notice.from_revision, notice.to_revision) == (1, 3)
    assert (notice.added, notice.removed, notice.redefined) == (1, 2, 1)


def test_redo_after_an_update_keeps_the_new_outline(
    sara_instance, as_account, edit_calculus_draft, publish_template
):
    _practise_everything(as_account, sara_instance.path_id)
    _publish_edit(edit_calculus_draft, publish_template)
    with as_account("sara"):
        store = LearningStore()
        apply_pending_revision(sara_instance.path_id, store, respect_lease=True)
        LearningService(store).reset_path(sara_instance.path_id)
        progress = store.load(sara_instance.path_id)
    assert progress.origin.revision == 2
    assert [p.name for p in progress.modules[0].knowledge_points] == [
        "Limits",
        "Derivative rules",
        "Integrals",
    ]
    assert progress.quiz_attempts == []
    assert progress.reassessment_required == {}


def test_a_revoked_or_archived_course_is_not_migrated(
    sara_instance, as_account, edit_calculus_draft, publish_template, teaching_users
):
    _publish_edit(edit_calculus_draft, publish_template)
    with as_account("tina"):
        TeachingService().revoke(sara_instance.template_id, teaching_users["sara"]["id"])
    with as_account("sara"):
        store = LearningStore()
        assert apply_pending_revision(sara_instance.path_id, store, respect_lease=False) is None
        assert store.load_origin(sara_instance.path_id).revision == 1


def test_a_lost_applied_revision_record_is_repaired(
    sara_instance, as_account, edit_calculus_draft, publish_template, teaching_users
):
    sara = teaching_users["sara"]["id"]
    _publish_edit(edit_calculus_draft, publish_template)
    with as_account("sara"):
        apply_pending_revision(sara_instance.path_id, LearningStore(), respect_lease=True)
    # The copy committed revision 2, but the teaching record was not updated.
    TeachingStore().record_instance(
        sara_instance.template_id, sara, instance_path_id=sara_instance.path_id, applied_revision=1
    )
    with as_account("sara"):
        assert apply_pending_revision(sara_instance.path_id, LearningStore(), respect_lease=True) is None
    assignment = TeachingStore().get_assignment(sara_instance.template_id, sara)
    assert assignment.applied_revision == 2


# ---------------------------------------------------------------------------
# R11: "needs re-assessment" is persisted from the change itself
# ---------------------------------------------------------------------------


def _redefine_everything(as_account, publish_template):
    """Republish with every objective marked as a changed objective."""
    with as_account("tina"):
        points = LearningStore().load("topic_calculus").modules[0].knowledge_points
    publish_template(kp_decisions={p.id: KpDecision(kind="redefined") for p in points})
    return [p.id for p in points]


def test_reassessment_does_not_depend_on_quiz_history(
    sara_instance, as_account, publish_template
):
    path_id = sara_instance.path_id
    with as_account("sara"):
        store = LearningStore()
        limits, derivatives, chain = (p.id for p in store.load(path_id).modules[0].knowledge_points)

        def history(tx):
            # Limits (a concept): assessed qualitatively, never quizzed.
            LearningService.record_qualitative_in_memory(tx.progress, limits, passed=True)
            # Derivatives: never touched at all.
            # Chain rule: its only answer was voided earlier for another reason.
            tx.progress.quiz_attempts.append(
                QuizAttempt(
                    question_id="q-bad",
                    knowledge_point_id=chain,
                    is_correct=True,
                    voided=True,
                    void_reason="invalid_question",
                )
            )
            tx.touch()

        store.mutate(path_id, history)
        before = learning_policy.map_summary(store.load(path_id))
    assert before["counts"]["mastered"] == 1
    assert set(_flags(store.load(path_id)).values()) == {False}

    _redefine_everything(as_account, publish_template)
    with as_account("sara"):
        notice = apply_pending_revision(path_id, LearningStore(), respect_lease=True)
        progress = LearningStore().load(path_id)  # persisted, not recomputed

    assert notice.redefined == 3
    assert progress.reassessment_required.keys() == {limits, derivatives, chain}
    assert _flags(progress) == {limits: True, derivatives: True, chain: True}
    statuses = {
        kp.id: learning_policy.objective_status(progress, kp)
        for kp in progress.modules[0].knowledge_points
    }
    # Status keeps its three values; the qualitative pass no longer counts.
    assert statuses == {limits: "new", derivatives: "new", chain: "learning"}
    assert limits not in progress.qualitative_mastery
    chain_reasons = [a.void_reason for a in progress.quiz_attempts if a.knowledge_point_id == chain]
    assert chain_reasons == ["invalid_question"]  # an earlier verdict is not rewritten
    summary = learning_policy.map_summary(progress)
    assert summary["counts"] == {"mastered": 0, "learning": 1, "new": 2, "total": 3}
    report = learning_policy.objective_report(progress, limits)
    assert report["needs_reassessment"] is True


def test_a_new_assessment_clears_reassessment_whatever_the_attempt_count(
    sara_instance, as_account, publish_template
):
    path_id = sara_instance.path_id
    _practise_everything(as_account, path_id)
    limits, derivatives, chain = _redefine_everything(as_account, publish_template)
    scheduler = SpacedRepetitionScheduler()
    with as_account("sara"):
        store = LearningStore()
        apply_pending_revision(path_id, store, respect_lease=True)

        def reassess(tx):
            service = LearningService(store)
            # A quiz answer (even a wrong one) is a new assessment.
            service._apply_grade(
                tx.progress,
                question_id="q-new",
                knowledge_point_id=derivatives,
                module_id="",
                user_answer="nope",
                expected_answer="2x",
                question_type="short",
                scheduler=scheduler,
            )
            # So is a qualitative verdict.
            service.record_qualitative_in_memory(
                tx.progress, limits, passed=False, scheduler=scheduler
            )
            # A voided attempt or a learner's own claim is not.
            tx.progress.quiz_attempts.append(
                QuizAttempt(
                    question_id="q-void",
                    knowledge_point_id=chain,
                    is_correct=True,
                    voided=True,
                    void_reason="invalid_question",
                )
            )
            tx.touch()

        store.mutate(path_id, reassess)
        LearningService(store).set_learner_mastery_override(path_id, chain, mastered=True)
        progress = store.load(path_id)

    assert _flags(progress) == {limits: False, derivatives: False, chain: True}
    statuses = [
        learning_policy.objective_status(progress, kp)
        for kp in progress.modules[0].knowledge_points
    ]
    assert statuses == ["learning", "learning", "mastered"]


def test_an_older_linked_assessment_does_not_clear_reassessment(
    sara_instance, as_account, publish_template
):
    path_id = sara_instance.path_id
    limits, _derivatives, _chain = _redefine_everything(as_account, publish_template)
    with as_account("sara"):
        store = LearningStore()
        apply_pending_revision(path_id, store, respect_lease=True)
        flagged_at = store.load(path_id).reassessment_required[limits]

        def stale(tx):
            learning_policy.clear_reassessment(tx.progress, limits, assessed_at=flagged_at - 60)
            tx.touch()

        store.mutate(path_id, stale)
        assert limits in store.load(path_id).reassessment_required

        def fresh(tx):
            learning_policy.clear_reassessment(tx.progress, limits, assessed_at=flagged_at + 1)
            tx.touch()

        store.mutate(path_id, fresh)
        remaining = store.load(path_id).reassessment_required
    assert limits not in remaining and len(remaining) == 2


# ---------------------------------------------------------------------------
# Sources follow the instance's own ids; materials follow the applied revision
# ---------------------------------------------------------------------------


def _source(index: int, ref: str) -> TopicSource:
    return TopicSource(
        id=f"s{index}",
        kind=TopicSourceKind.KNOWLEDGE_BASE,
        source_id=ref,
        label=ref,
        position=index,
    )


@pytest.fixture
def cited_course(as_account, fake_kb_registry, publish_template):
    """Tina's path cites library A from its objective; Sara has her copy."""
    fake_kb_registry.add("kb_a", visibility="public", legacy_refs=("lib_a",))
    fake_kb_registry.add("kb_b", visibility="public", legacy_refs=("lib_b",))
    module = LearningModule(
        id="m0",
        name="Calculus",
        order=0,
        knowledge_points=[
            KnowledgePoint(
                id="kp_limits", name="Limits", type=KnowledgeType.CONCEPT, module_id="m0",
                topic_source_ids=["s0"],
            ),
            KnowledgePoint(
                id="kp_rules",
                name="Rules",
                type=KnowledgeType.PROCEDURE,
                module_id="m0",
                required_visual_tasks=["identification"],
            ),
        ],
    )
    with as_account("tina"):
        LearningService(LearningStore()).create_topic(
            "topic_calculus",
            name="Calculus I",
            modules=[module],
            metadata=TopicMetadata(path_id="topic_calculus", goal="Differentiate"),
            sources=[_source(0, "lib_a")],
        )
    template_id = publish_template(students=("sara",))["template"]["id"]
    with as_account("sara"):
        path_id = asyncio.run(mastery_path.list_topics())["topics"][0]["path_id"]
    return SimpleNamespace(template_id=template_id, path_id=path_id, kbs=fake_kb_registry)


def _swap_to_library_b(as_account, publish_template) -> None:
    with as_account("tina"):
        store = LearningStore()
        topic = store.get_topic("topic_calculus")
        store.put_topic(topic.metadata, [_source(0, "lib_b")])
    publish_template()


def _referenced(kbs, template_id: str) -> list[str]:
    return sorted(kb_id for kb_id, _docs in kbs.references.get(("mastery_template", template_id), []))


def test_migration_rebuilds_the_outline_with_the_instances_own_source_ids(
    cited_course, as_account, publish_template
):
    _swap_to_library_b(as_account, publish_template)
    with as_account("sara"):
        store = LearningStore()
        assert apply_pending_revision(cited_course.path_id, store, respect_lease=True)
        progress = store.load(cited_course.path_id)
        topic = store.get_topic(cited_course.path_id, progress=progress)

    derived = instance_source_id(cited_course.path_id, "s0")
    assert [(s.id, s.source_id) for s in topic.sources] == [(derived, "kb:kb_b")]
    limits = progress.modules[0].knowledge_points[0]
    assert limits.topic_source_ids == [derived]
    # The template keeps the teacher's ids.
    revision = TeachingStore().get_revision(cited_course.template_id, 2)
    assert [s.id for s in revision.sources.sources] == ["s0"]


def test_an_old_question_keeps_the_applied_revisions_materials_until_migration(
    cited_course, as_account, publish_template
):
    _ask(as_account, cited_course.path_id, "kp_limits")
    _swap_to_library_b(as_account, publish_template)
    with as_account("sara"):
        store = LearningStore()
        assert apply_pending_revision(cited_course.path_id, store, respect_lease=True) is None
        topic = store.get_topic(cited_course.path_id)
    # Still on revision 1: its copy cites library A, and A stays protected.
    assert [s.source_id for s in topic.sources] == ["kb:kb_a"]
    assert _referenced(cited_course.kbs, cited_course.template_id) == ["kb_a", "kb_b"]

    with as_account("sara"):
        LearningService(LearningStore()).abandon_active_question(cited_course.path_id)
        assert apply_pending_revision(cited_course.path_id, LearningStore(), respect_lease=True)
    # Nobody is on revision 1 any more: its material is released (shrink only).
    assert _referenced(cited_course.kbs, cited_course.template_id) == ["kb_b"]


def test_a_failed_reference_resync_does_not_undo_the_migration(
    cited_course, as_account, publish_template, monkeypatch, caplog
):
    _swap_to_library_b(as_account, publish_template)

    def refuse(_template_id, **_kwargs):
        raise PermissionError("The current actor cannot read this book.")

    monkeypatch.setattr("deeptutor.teaching.service.sync_material_references", refuse)
    with caplog.at_level(logging.WARNING, logger="deeptutor.teaching.instances"):
        with as_account("sara"):
            notice = apply_pending_revision(cited_course.path_id, LearningStore(), respect_lease=True)
            origin = LearningStore().load_origin(cited_course.path_id)
    assert notice.to_revision == 2 and origin.revision == 2
    assert any("material references" in record.getMessage() for record in caplog.records)
    # The older material is kept (never over-released), not granted anew.
    assert _referenced(cited_course.kbs, cited_course.template_id) == ["kb_a", "kb_b"]


# ---------------------------------------------------------------------------
# Root recovery regression: stuck on a withdrawn material's old question
# ---------------------------------------------------------------------------


def _auth(name: str) -> dict:
    return {"Authorization": f"Bearer {name}"}


def test_skipping_a_stuck_old_question_lets_the_update_apply(
    cited_course, as_account, publish_template, teaching_http, teaching_users
):
    client = teaching_http.client
    path_id = cited_course.path_id
    scheduler = SpacedRepetitionScheduler()
    with as_account("sara"):
        store = LearningStore()

        def earn(tx):
            # Earned mastery and a scheduled review on the other objective.
            LearningService(store)._apply_grade(
                tx.progress,
                question_id="q-rules",
                knowledge_point_id="kp_rules",
                module_id="m0",
                user_answer="2x",
                expected_answer="2x",
                question_type="short",
                scheduler=scheduler,
            )
            tx.touch()

        store.mutate(path_id, earn)
        earned = store.load(path_id)
    _ask(as_account, path_id, "kp_limits")
    # An admin withdraws the old material; the teacher publishes a new revision.
    cited_course.kbs.force_private("kb_a")
    _swap_to_library_b(as_account, publish_template)

    stuck = client.get(f"/api/mastery-paths/topics/{path_id}", headers=_auth("sara"))
    assert stuck.status_code == 200
    assert stuck.json()["origin"]["revision"] == 1  # the open question holds it

    skipped = client.post(f"/api/mastery-paths/progress/{path_id}/skip-question", headers=_auth("sara"))
    assert skipped.status_code == 200 and skipped.json()["skipped"] is True

    opened = client.get(f"/api/mastery-paths/topics/{path_id}", headers=_auth("sara"))
    assert opened.status_code == 200
    assert opened.json()["origin"]["revision"] == 2
    with as_account("sara"):
        progress = LearningStore().load(path_id)
    # Nothing unrelated was cleared, and the old question was neither graded
    # nor answered on the learner's behalf.
    assert progress.mastery_levels["kp_rules"] == earned.mastery_levels["kp_rules"]
    assert progress.repetition_states.keys() == earned.repetition_states.keys()
    assert progress.review_queue == earned.review_queue
    assert [a.question_id for a in progress.quiz_attempts] == ["q-rules"]
    assert progress.pending_question is None
    # The withdrawn material was not granted back to anyone.
    assert cited_course.kbs.records["kb_a"].visibility == "private"
    assert _referenced(cited_course.kbs, cited_course.template_id) == ["kb_b"]

    # A revoked assignment still closes the course, skip included.
    with as_account("tina"):
        TeachingService().revoke(cited_course.template_id, teaching_users["sara"]["id"])
    for method, url in (
        ("GET", f"/api/mastery-paths/topics/{path_id}"),
        ("POST", f"/api/mastery-paths/progress/{path_id}/skip-question"),
        ("POST", f"/api/mastery-paths/topics/{path_id}/teacher-update/ack"),
    ):
        assert client.request(method, url, headers=_auth("sara")).status_code == 403, url


def test_skip_question_works_on_an_active_teacher_instance(
    sara_instance, as_account, teaching_http
):
    path_id = sara_instance.path_id
    _ask(as_account, path_id)
    response = teaching_http.client.post(
        f"/api/mastery-paths/progress/{path_id}/skip-question", headers=_auth("sara")
    )
    assert response.status_code == 200 and response.json()["skipped"] is True
    with as_account("sara"):
        assert LearningStore().load(path_id).pending_question is None


# ---------------------------------------------------------------------------
# Turn admission: the one gate applies the update
# ---------------------------------------------------------------------------


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


def _probe(monkeypatch, seen: dict) -> None:
    async def _loop(self, context, _stream):
        seen["kbs"] = list(context.knowledge_bases)
        return {}

    monkeypatch.setattr(AgenticLoopPipeline, "run", _loop)
    monkeypatch.setattr("deeptutor.capabilities.setup.binding._intro_pending", lambda _c: False)


@pytest.mark.asyncio
async def test_a_turn_holding_the_lease_applies_the_update_at_admission(
    cited_course, as_account, publish_template, monkeypatch
):
    await asyncio.to_thread(_swap_to_library_b, as_account, publish_template)
    seen: dict = {}
    _probe(monkeypatch, seen)
    stream = _Stream()
    with as_account("sara"):
        store = LearningStore()
        await asyncio.to_thread(store.acquire_path_lease, cited_course.path_id, "s1", "t1")
        await MasteryLoopPipeline(language="en").run(_context(cited_course.path_id), stream)
        origin = await asyncio.to_thread(store.load_origin, cited_course.path_id)

    assert stream.errors == []
    assert origin.revision == 2
    # The turn is taught from the revision it was admitted on.
    assert seen["kbs"] == ["kb:kb_b"]


@pytest.mark.asyncio
async def test_a_turn_never_migrates_under_another_turns_lease(
    cited_course, as_account, publish_template, monkeypatch
):
    await asyncio.to_thread(_swap_to_library_b, as_account, publish_template)
    seen: dict = {}
    _probe(monkeypatch, seen)
    with as_account("sara"):
        store = LearningStore()
        await asyncio.to_thread(store.acquire_path_lease, cited_course.path_id, "s2", "t-other")
        await MasteryLoopPipeline(language="en").run(
            _context(cited_course.path_id, "t1"), _Stream()
        )
        origin = await asyncio.to_thread(store.load_origin, cited_course.path_id)

    assert origin.revision == 1
    # Still on its applied revision, with that revision's materials.
    assert seen["kbs"] == ["kb:kb_a"]


@pytest.mark.asyncio
async def test_switching_onto_a_teacher_path_applies_its_update(
    cited_course, as_account, publish_template, teacher_draft, monkeypatch
):
    from deeptutor.capabilities.mastery.loop import _path_binder
    from deeptutor.tools.mastery_tool import MasterySwitchTool

    await asyncio.to_thread(_swap_to_library_b, as_account, publish_template)
    await asyncio.to_thread(teacher_draft, "sara", path_id="topic_own")
    seen: dict = {}

    async def _loop(self, context, _stream):
        seen["switched"] = await MasterySwitchTool().execute(
            path_id=cited_course.path_id,
            _mastery_path_id=context.metadata["mastery_path_id"],
            _session_id="s1",
            _turn_id="t1",
            _bind_active_path=_path_binder(context),
        )
        seen["kbs"] = list(context.knowledge_bases)
        return {}

    async def _forget(_session_id, _path_id):
        return None

    monkeypatch.setattr(AgenticLoopPipeline, "run", _loop)
    monkeypatch.setattr("deeptutor.capabilities.setup.binding._intro_pending", lambda _c: False)
    monkeypatch.setattr("deeptutor.capabilities.mastery.binding._remember_on_session", _forget)
    with as_account("sara"):
        store = LearningStore()
        await asyncio.to_thread(store.acquire_path_lease, "topic_own", "s1", "t1")
        await MasteryLoopPipeline(language="en").run(_context("topic_own"), _Stream())
        origin = await asyncio.to_thread(store.load_origin, cited_course.path_id)

    assert seen["switched"].success, seen["switched"].content
    assert origin.revision == 2
    assert seen["kbs"] == ["kb:kb_b"]


# ---------------------------------------------------------------------------
# A redefined objective starts again: no pre-redefinition evidence stays live
# ---------------------------------------------------------------------------

_VISUAL = {"task": "identification", "key_status": "verified"}


def _answer(
    store: LearningStore, path_id: str, question_id: str, *, visual: dict | None = None
) -> None:
    scheduler = SpacedRepetitionScheduler()

    def grade(tx):
        LearningService(store)._apply_grade(
            tx.progress,
            question_id=question_id,
            knowledge_point_id="kp_rules",
            module_id="m0",
            user_answer="2x",
            expected_answer="2x",
            question_type="short",
            scheduler=scheduler,
            visual_context=dict(visual) if visual else None,
        )
        tx.touch()

    store.mutate(path_id, grade)


def _practise_rules_then_redefine(cited_course, as_account, publish_template) -> None:
    with as_account("sara"):
        store = LearningStore()
        for index in range(3):
            _answer(store, cited_course.path_id, f"q-old-{index}", visual=_VISUAL)
        before = store.load(cited_course.path_id)
    assert learning_policy.visual_achievements(before, "kp_rules") == ["identification"]
    assert before.repetition_states["kp_rules"].review_count >= 1
    publish_template(kp_decisions={"kp_rules": KpDecision(kind="redefined")})
    with as_account("sara"):
        assert apply_pending_revision(cited_course.path_id, LearningStore(), respect_lease=True)


def test_a_repair_after_redefinition_does_not_resurrect_the_old_schedule(
    cited_course, as_account, publish_template
):
    _practise_rules_then_redefine(cited_course, as_account, publish_template)
    with as_account("sara"):
        store = LearningStore()
        _answer(store, cited_course.path_id, "q-new")
        LearningService(store).repair_question(
            cited_course.path_id,
            "q-new",
            action="void",
            reason="wrong answer key",
            scheduler=SpacedRepetitionScheduler(),
        )
        progress = store.load(cited_course.path_id)

    # Nothing live is left for the new objective, so nothing is scheduled.
    assert "kp_rules" not in progress.repetition_states
    assert all(task.knowledge_point_id != "kp_rules" for task in progress.review_queue)
    assert [e for e in progress.learning_evidence if e.knowledge_point_id == "kp_rules"] == []
    # The old answers remain, voided, as the audit trail.
    reasons = {a.question_id: a.void_reason for a in progress.quiz_attempts}
    assert reasons["q-old-0"] == learning_policy.REDEFINED_VOID_REASON


def test_a_replayed_older_linked_assessment_does_not_resurrect_the_old_schedule(
    cited_course, as_account, publish_template
):
    from deeptutor.learning import assessment

    _practise_rules_then_redefine(cited_course, as_account, publish_template)

    def linked(created_at: float, attempt_id: str) -> bool:
        record = assessment.AssessmentRecord(
            session_id="s-linked",
            question_id=attempt_id,
            question="Differentiate x^2",
            source="deep_question",
            assessment_type="quiz",
            result="correct",
            mastery_path_id=cited_course.path_id,
            knowledge_point_id="kp_rules",
            created_at=created_at,
        )
        return assessment._apply_linked_retention(
            record, result="correct", attempt_id=attempt_id
        )

    with as_account("sara"):
        flagged_at = LearningStore().load(cited_course.path_id).reassessment_required["kp_rules"]
        # An event from before the redefinition assessed the old objective.
        assert linked(flagged_at - 5, "linked-old") is False
        progress = LearningStore().load(cited_course.path_id)
        assert "kp_rules" not in progress.repetition_states
        assert "kp_rules" in progress.reassessment_required
        # A later one is a first assessment of the new objective.
        assert linked(flagged_at + 5, "linked-new") is True
        progress = LearningStore().load(cited_course.path_id)

    events = [e for e in progress.learning_evidence if e.knowledge_point_id == "kp_rules"]
    assert [e.evidence_id for e in events] == ["linked-new"]
    assert progress.repetition_states["kp_rules"].review_count <= 1
    assert "kp_rules" not in progress.reassessment_required


def test_the_tutor_sees_a_redefined_objective_as_not_yet_covered(
    cited_course, as_account, publish_template
):
    _practise_rules_then_redefine(cited_course, as_account, publish_template)
    with as_account("sara"):
        progress = LearningStore().load(cited_course.path_id)

    # What mastery_status reports to the tutor every turn.
    assert learning_policy.learning_stage(progress, "kp_rules") == "not_yet_covered"
    assert learning_policy.visual_achievements(progress, "kp_rules") == []
    report = learning_policy.objective_report(progress, "kp_rules")
    assert report["correct_count"] == 0
    assert {a["voided"] for a in report["attempts"]} == {True}
    assert {a["void_reason"] for a in report["attempts"]} == {
        learning_policy.REDEFINED_VOID_REASON
    }
    assert report["errors"] == []


def test_old_visual_evidence_does_not_pass_the_new_objectives_gate(
    cited_course, as_account, publish_template
):
    _practise_rules_then_redefine(cited_course, as_account, publish_template)
    with as_account("sara"):
        store = LearningStore()
        for index in range(3):
            _answer(store, cited_course.path_id, f"q-text-{index}")  # no visual task
        progress = store.load(cited_course.path_id)

    rules = progress.modules[0].knowledge_points[1]
    assert progress.mastery_levels["kp_rules"] >= learning_policy.gate_threshold(rules.type)
    assert learning_policy.visual_achievements(progress, "kp_rules") == []
    assert learning_policy.is_assessed_mastered(progress, rules) is False

    with as_account("sara"):
        _answer(store, cited_course.path_id, "q-visual-new", visual=_VISUAL)
        progress = store.load(cited_course.path_id)
    assert learning_policy.is_assessed_mastered(progress, rules) is True


def test_lease_acquired_between_check_and_write_blocks_migration(
    sara_instance, as_account, edit_calculus_draft, publish_template, monkeypatch
):
    """A lease acquired after the outer check but before BEGIN IMMEDIATE must
    prevent the migration — the inner re-check catches the race."""
    _publish_edit(edit_calculus_draft, publish_template)
    with as_account("sara"):
        store = LearningStore()
        # Acquire a lease so the DB row exists.
        store.acquire_path_lease(sara_instance.path_id, "s1", "t_other", bind_session=False)
        # Bypass the outer check to simulate a race (lease arrived between
        # the outer check and BEGIN IMMEDIATE).
        monkeypatch.setattr(
            "deeptutor.teaching.instances._lease_blocks", lambda *_a, **_kw: False
        )
        notice = apply_pending_revision(
            sara_instance.path_id, store, respect_lease=True
        )
        assert notice is None, "migration must not proceed when lease acquired mid-race"
        assert store.load_origin(sara_instance.path_id).revision == 1
