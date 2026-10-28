"""Batch 3 / P2: teacher-path material availability and migration repairs.

B3-19 per-source availability, T10-M2 applied-revision repair before a
deferred migration, the B2-19 unsettled withdrawal counts and the public
lease read used inside the migration transaction (B2-14).
"""

from __future__ import annotations

import sqlite3

import pytest

from deeptutor.knowledge import registry as kb_registry
from deeptutor.learning.models import PendingQuestion, TopicSource, TopicSourceKind
from deeptutor.learning.storage import LearningStore
from deeptutor.teaching import access
from deeptutor.teaching.instances import apply_pending_revision
from deeptutor.teaching.models import KpDecision
from deeptutor.teaching.store import TeachingStore

def _auth(name: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {name}"}


# ---- B3-19: a withdrawn KB shows as unavailable on the student's copy ----------


@pytest.fixture
def toms_kbs(teaching_users, as_account, make_kb):
    """Tom's (teacher, not admin) two public KBs in his account library."""
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    from deeptutor.multi_user.paths import get_account_path_service

    records = {}
    with as_account("tom"):
        root = get_account_path_service().get_knowledge_bases_root()
        root.mkdir(parents=True, exist_ok=True)
        manager = KnowledgeBaseManager(base_dir=str(root))
        for name in ("algebra", "geometry"):
            make_kb(manager, name)
            record = kb_registry.ensure_registered(
                root, name, created_by=teaching_users["tom"]["id"]
            )
            kb_registry.set_visibility(record.kb_id, "public")
            records[name] = kb_registry.get_kb(record.kb_id)
    return records


def test_withdrawn_kb_source_is_reported_unavailable_on_the_teacher_path(
    toms_kbs, teacher_draft, publish_template, teaching_http, as_account
):
    teacher_draft(
        sources=(
            TopicSource(
                id="s0", kind=TopicSourceKind.KNOWLEDGE_BASE,
                source_id=toms_kbs["algebra"].ref, label="Algebra notes", position=0,
            ),
            TopicSource(
                id="s1", kind=TopicSourceKind.KNOWLEDGE_BASE,
                source_id=toms_kbs["geometry"].ref, label="Geometry notes", position=1,
            ),
        )
    )
    publish_template(students=("sara",))
    client = teaching_http.client
    [row] = client.get("/api/mastery-paths/topics", headers=_auth("sara")).json()["topics"]
    path_id = row["path_id"]

    before = client.get(f"/api/mastery-paths/topics/{path_id}", headers=_auth("sara")).json()
    assert before["origin"]["materials_available"] is True
    assert {s["label"]: s["available"] for s in before["sources"]} == {
        "Algebra notes": True,
        "Geometry notes": True,
    }

    withdrawn = client.put(
        f"/api/knowledge-bases/{toms_kbs['algebra'].ref}/visibility",
        json={"visibility": "private", "confirm": True}, headers=_auth("tom"),
    )
    assert withdrawn.status_code == 200, withdrawn.text

    after = client.get(f"/api/mastery-paths/topics/{path_id}", headers=_auth("sara")).json()
    assert after["origin"]["materials_available"] is False
    assert {s["label"]: s["available"] for s in after["sources"]} == {
        "Algebra notes": False,
        "Geometry notes": True,
    }
    # Response only: the stored copy is not rewritten.
    with as_account("sara"):
        stored = LearningStore().get_topic(path_id).sources
    assert {source.label: source.available for source in stored} == {
        "Algebra notes": True,
        "Geometry notes": True,
    }


def test_a_learners_own_path_keeps_its_stored_availability(teaching_http, teacher_draft):
    teacher_draft(
        "sara",
        path_id="topic_own",
        sources=(
            TopicSource(
                id="s0", kind=TopicSourceKind.KNOWLEDGE_BASE,
                source_id="kb:does-not-exist", label="Gone", position=0,
            ),
        ),
    )
    payload = teaching_http.client.get(
        "/api/mastery-paths/topics/topic_own", headers=_auth("sara")
    ).json()
    assert payload.get("origin") is None
    assert [s["available"] for s in payload["sources"]] == [True]


# ---- T10-M2: a stale applied_revision is repaired even when the move waits ------


def test_stale_applied_revision_is_repaired_while_the_update_waits(
    sara_instance, as_account, edit_calculus_draft, publish_template, teaching_users
):
    sara = teaching_users["sara"]["id"]
    ids = edit_calculus_draft()
    publish_template(
        kp_decisions={
            ids.limits: KpDecision(kind="redefined"),
            ids.renamed: KpDecision(kind="reworded", from_id=ids.derivatives),
        }
    )
    with as_account("sara"):
        apply_pending_revision(sara_instance.path_id, LearningStore(), respect_lease=True)
    # The copy committed revision 2, but the teaching record was not updated ...
    # (``record_instance`` only moves forward, so roll the row back directly.)
    conn = sqlite3.connect(TeachingStore().db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE mastery_assignments SET applied_revision = 1 "
                "WHERE template_id = ? AND student_user_id = ?",
                (sara_instance.template_id, sara),
            )
    finally:
        conn.close()
    assert TeachingStore().get_assignment(sara_instance.template_id, sara).applied_revision == 1
    # ... then the teacher publishes revision 3 and the student has a question open.
    publish_template(kp_decisions={ids.limits: KpDecision(kind="redefined")})
    assert TeachingStore().get_template(sara_instance.template_id).current_revision == 3
    with as_account("sara"):
        store = LearningStore()
        point = store.load(sara_instance.path_id).modules[0].knowledge_points[0].id

        def ask(tx):
            tx.progress.pending_question = PendingQuestion(
                question_id="q-open", knowledge_point_id=point, expected_answer="x"
            )
            tx.touch()

        store.mutate(sara_instance.path_id, ask)
        assert apply_pending_revision(sara_instance.path_id, store, respect_lease=True) is None
        assert store.load_origin(sara_instance.path_id).revision == 2
    assignment = TeachingStore().get_assignment(sara_instance.template_id, sara)
    assert assignment.applied_revision == 2


# ---- B2-19 leftover: an unsettled withdrawal carries the last real counts --------


class _Referenced(Exception):
    def __init__(self, referrer_ids) -> None:
        super().__init__("referenced")
        self.referrer_ids = tuple(referrer_ids)


def test_unsettled_withdrawal_reports_the_last_rounds_counts(
    teacher_draft, publish_template
):
    teacher_draft(path_id="topic_calculus")
    wide = publish_template(path_id="topic_calculus", students=("sara", "sam"))["template"]["id"]
    teacher_draft(path_id="topic_algebra")
    narrow = publish_template(path_id="topic_algebra", students=("sara",))["template"]["id"]
    rounds = iter([{wide, narrow}, {wide}, {narrow}])
    assert access.WITHDRAWAL_ROUNDS == 3

    def attempt(_bound):
        raise _Referenced(next(rounds))

    with pytest.raises(access.WithdrawalConsentRequired) as caught:
        access.run_confirmed_withdrawal(
            attempt, _Referenced, confirm=True,
            expected_paths=None, expected_students=None, resource="knowledge base",
        )
    detail = caught.value.detail
    assert detail["code"] == access.WITHDRAWAL_CONFIRMATION_CODE
    assert detail["changed"] is True
    assert (detail["affected_paths"], detail["affected_students"]) == (1, 1)


def test_unsettled_without_counts_keeps_its_old_shape():
    detail = access.withdrawal_unsettled("book")
    assert (detail["affected_paths"], detail["affected_students"]) == (0, 0)
    assert detail["changed"] is True


# ---- B2-14 leftover: the lease is read through a public transaction method -----


def test_transaction_reads_the_paths_lease(sara_instance, as_account):
    with as_account("sara"):
        store = LearningStore()
        assert store.mutate(sara_instance.path_id, lambda tx: tx.lease_turn_id())[1] is None
        store.acquire_path_lease(sara_instance.path_id, "s1", "t1", bind_session=False)
        assert store.mutate(sara_instance.path_id, lambda tx: tx.lease_turn_id())[1] == "t1"
