"""A student cannot change a teacher's outline through any REST route."""

from __future__ import annotations

import pytest

from deeptutor.learning.storage import LearningStore

MODULES = [{"name": "Hacked", "knowledge_points": [{"name": "Anything goes", "type": "concept"}]}]


def _auth(name: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {name}"}


@pytest.mark.parametrize(
    ("method", "url", "body"),
    [
        ("put", "/api/mastery-paths/topics/{p}/map", {"modules": MODULES}),
        ("post", "/api/mastery-paths/topics/{p}/objectives/{kp}/override", {"mastered": True}),
        ("post", "/api/mastery-paths/progress/{p}/init-modules", {"modules": MODULES}),
        (
            "post",
            "/api/mastery-paths/progress/{p}/import-from-book",
            {"chapters": [{"title": "X", "knowledge_points": ["Y"]}]},
        ),
        (
            "post",
            "/api/mastery-paths/progress/{p}/generate-from-notebook",
            {"notebook_id": "nb", "records": [{"id": "r1"}]},
        ),
        ("post", "/api/mastery-paths/progress/{p}/generate-from-reading", {"workspace_id": "w1"}),
        ("patch", "/api/mastery-paths/progress/{p}", {"name": "Mine now"}),
        ("delete", "/api/mastery-paths/progress/{p}", None),
        ("put", "/api/mastery-paths/topics/{p}/sessions/s1/mode", {"mode": "outline"}),
    ],
)
def test_students_cannot_change_a_teacher_outline(
    teaching_http, sara_instance, as_account, method, url, body
):
    with as_account("sara"):
        point = LearningStore().load(sara_instance.path_id).modules[0].knowledge_points[0].id
    kwargs: dict = {"headers": _auth("sara")}
    if body is not None:
        kwargs["json"] = body

    response = getattr(teaching_http.client, method)(
        url.format(p=sara_instance.path_id, kp=point), **kwargs
    )

    assert response.status_code == 403
    # A locked outline is not a closed course: no unavailable code, its own message.
    assert isinstance(response.json()["detail"], str)
    assert "teacher" in response.json()["detail"].lower()
    with as_account("sara"):
        progress = LearningStore().load(sara_instance.path_id)
    assert progress is not None
    assert progress.name == "Calculus I"
    assert [p.name for p in progress.modules[0].knowledge_points] == [
        "Limits",
        "Derivatives",
        "Chain rule",
    ]
    assert progress.learner_mastery_overrides == {}


def test_redo_resets_only_the_students_own_state(teaching_http, sara_instance, as_account):
    with as_account("sara"):
        store = LearningStore()
        point = store.load(sara_instance.path_id).modules[0].knowledge_points[0].id

        def practise(tx):
            tx.progress.mastery_levels[point] = 0.9
            tx.touch()

        store.mutate(sara_instance.path_id, practise)

    response = teaching_http.client.post(
        f"/api/mastery-paths/progress/{sara_instance.path_id}/redo", headers=_auth("sara")
    )

    assert response.status_code == 200
    with as_account("sara"):
        progress = LearningStore().load(sara_instance.path_id)
    assert progress.mastery_levels == {}
    assert progress.origin.template_id == sara_instance.template_id
    assert len(progress.modules[0].knowledge_points) == 3


def test_study_and_review_modes_stay_available(teaching_http, sara_instance):
    from unittest.mock import AsyncMock, patch

    store = AsyncMock()
    store.get_session.return_value = {"id": "s1"}
    with patch("deeptutor.services.session.get_session_store", return_value=store):
        response = teaching_http.client.put(
            f"/api/mastery-paths/topics/{sara_instance.path_id}/sessions/s1/mode",
            json={"mode": "review"},
            headers=_auth("sara"),
        )
    assert response.status_code == 200


def test_the_teachers_own_draft_stays_editable(teaching_http, sara_instance):
    response = teaching_http.client.patch(
        "/api/mastery-paths/progress/topic_calculus",
        json={"name": "Calculus I (2026)"},
        headers=_auth("tina"),
    )
    assert response.status_code == 200
