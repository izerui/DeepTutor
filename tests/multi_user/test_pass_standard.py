"""Three pass standards for the scored mastery gate (local extension, 2026-10-31).

A learner chooses the standard of their own path; a student's copy of a
teacher path takes the teacher's, published with the revision, and cannot
change it.
"""

from __future__ import annotations

import asyncio

import pytest

from deeptutor.api.routers import mastery_path
from deeptutor.learning import policy
from deeptutor.learning.mastery import compute_mastery
from deeptutor.learning.models import KnowledgePoint, KnowledgeType, LearningProgress
from deeptutor.learning.storage import LearningStore


def _passes(answers: list[bool], standard: str) -> bool:
    kp = KnowledgePoint(id="kp", module_id="m", name="Chain rule", type=KnowledgeType.PROCEDURE)
    progress = LearningProgress(
        book_id="p", pass_standard=standard, mastery_levels={"kp": compute_mastery(answers)}
    )
    return policy.is_assessed_mastered(progress, kp)


@pytest.mark.parametrize(
    ("answers", "relaxed", "standard", "strict"),
    [
        ([True], False, False, False),
        ([True, True], True, True, False),
        ([True, True, True], True, True, True),
        ([False, True, True], True, False, False),
        ([False, True, True, True], True, True, False),
        ([False, True, True, True, True], True, True, False),
        ([False, True, True, True, True, True], True, True, True),
        ([True, True, True, True, True, False], True, False, False),
    ],
)
def test_the_same_answers_pass_by_standard(answers, relaxed, standard, strict):
    assert _passes(answers, "relaxed") is relaxed
    assert _passes(answers, "standard") is standard
    assert _passes(answers, "strict") is strict


def test_older_paths_read_as_strict_and_explanations_are_unaffected():
    assert LearningProgress.model_validate({"book_id": "old"}).pass_standard == "strict"
    concept = KnowledgePoint(id="c", module_id="m", name="Limits", type=KnowledgeType.CONCEPT)
    progress = LearningProgress(book_id="p", pass_standard="relaxed", mastery_levels={"c": 0.9})
    assert not policy.is_assessed_mastered(progress, concept)
    assert policy.gate_threshold(KnowledgeType.CONCEPT, progress) == 1.0


def _auth(name: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {name}"}


def test_learners_set_their_own_paths_standard(teaching_http, teacher_draft, as_account):
    teacher_draft("sara", path_id="topic_own")
    client = teaching_http.client
    url = "/api/mastery-paths/topics/topic_own"
    assert client.get(url, headers=_auth("sara")).json()["pass_standard"] == "strict"

    saved = client.put(f"{url}/pass-standard", json={"pass_standard": "relaxed"}, headers=_auth("sara"))
    assert saved.status_code == 200 and saved.json()["pass_standard"] == "relaxed"
    with as_account("sara"):
        assert LearningStore().load("topic_own").pass_standard == "relaxed"
    bad = client.put(f"{url}/pass-standard", json={"pass_standard": "lenient"}, headers=_auth("sara"))
    assert bad.status_code == 422


def test_students_follow_the_teachers_standard_and_cannot_change_it(
    teaching_http, teacher_draft, publish_template, as_account
):
    teacher_draft()
    with as_account("tina"):
        store = LearningStore()

        def standard(value):
            def apply(tx):
                tx.progress.pass_standard = value
                tx.touch()

            store.mutate("topic_calculus", apply)

        standard("standard")
    template_id = publish_template(students=("sara",))["template"]["id"]
    with as_account("sara"):
        [topic] = asyncio.run(mastery_path.list_topics())["topics"]
    path_id = topic["path_id"]
    assert topic["pass_standard"] == "standard"

    refused = teaching_http.client.put(
        f"/api/mastery-paths/topics/{path_id}/pass-standard",
        json={"pass_standard": "relaxed"},
        headers=_auth("sara"),
    )
    assert refused.status_code == 403

    # The teacher loosens the standard; the student gets it with the update.
    with as_account("tina"):
        standard("relaxed")
        from deeptutor.teaching.service import TeachingService

        preview = asyncio.run(TeachingService().preview_publish(template_id))
    assert preview["changes"]["pass_standard"] == "relaxed"
    assert preview["changes"]["previous_pass_standard"] == "standard"
    publish_template()
    with as_account("sara"):
        from deeptutor.teaching.instances import apply_pending_revision

        apply_pending_revision(path_id, LearningStore(), respect_lease=False)
        assert LearningStore().load(path_id).pass_standard == "relaxed"
