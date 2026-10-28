"""Batch 3 / P2: teacher-path material availability and migration repairs.

B3-19 per-source availability, T10-M2 applied-revision repair before a
deferred migration, the B2-19 unsettled withdrawal counts and the public
lease read used inside the migration transaction (B2-14).
"""

from __future__ import annotations

import sqlite3

import pytest

from deeptutor.learning.models import PendingQuestion
from deeptutor.learning.storage import LearningStore
from deeptutor.teaching import access
from deeptutor.teaching.instances import apply_pending_revision
from deeptutor.teaching.models import KpDecision
from deeptutor.teaching.store import TeachingStore


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
