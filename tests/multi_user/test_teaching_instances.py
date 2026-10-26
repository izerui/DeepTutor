"""Students get their own copy of a teacher path, created from their own requests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from deeptutor.api.routers import mastery_path
from deeptutor.learning.storage import LearningStore
from deeptutor.teaching.service import TeachingService
from deeptutor.teaching.store import TeachingStore


def _topics(as_account, name: str) -> list[dict]:
    with as_account(name):
        return asyncio.run(mastery_path.list_topics())["topics"]


def _set_mastery(as_account, name: str, path_id: str, level: float) -> str:
    with as_account(name):
        store = LearningStore()
        point = store.load(path_id).modules[0].knowledge_points[0].id

        def apply(tx):
            tx.progress.mastery_levels[point] = level
            tx.touch()

        store.mutate(path_id, apply)
    return point


def test_each_student_gets_an_independent_instance(
    as_account, teacher_draft, publish_template, teaching_users
):
    teacher_draft()
    template_id = publish_template(students=("sara", "sam"))["template"]["id"]

    sara_topics = _topics(as_account, "sara")
    assert len(sara_topics) == 1
    topic = sara_topics[0]
    assert topic["path_id"] == f"assigned_{template_id}"
    assert topic["origin"]["template_id"] == template_id
    assert topic["origin"]["teacher_name"] == "tina"
    assert topic["origin"]["revision"] == 1
    assert topic["map"]["counts"]["total"] == 3
    assert topic["name"] == "Calculus I"

    _set_mastery(as_account, "sara", topic["path_id"], 0.8)

    sam_topics = _topics(as_account, "sam")
    with as_account("sam"):
        sam_store = LearningStore()
        assert sam_store.load(sam_topics[0]["path_id"]).mastery_levels == {}
        sam_db = sam_store.db_path
    with as_account("sara"):
        assert LearningStore().db_path != sam_db
    assignment = TeachingStore().get_assignment(template_id, teaching_users["sara"]["id"])
    assert (assignment.instance_path_id, assignment.applied_revision) == (topic["path_id"], 1)


def test_listing_again_reuses_the_instance(sara_instance, as_account):
    with as_account("sara"):
        before = LearningStore().load(sara_instance.path_id).version
    _topics(as_account, "sara")
    with as_account("sara"):
        assert LearningStore().load(sara_instance.path_id).version == before


def test_the_teachers_own_listing_creates_nothing(sara_instance, as_account):
    topics = _topics(as_account, "tina")
    assert [t["path_id"] for t in topics] == ["topic_calculus"]
    assert topics[0]["origin"] is None


def test_revoking_hides_the_path_and_reassigning_restores_progress(
    sara_instance, as_account, teaching_users
):
    point = _set_mastery(as_account, "sara", sara_instance.path_id, 0.7)
    with as_account("tina"):
        TeachingService().revoke(sara_instance.template_id, teaching_users["sara"]["id"])

    assert _topics(as_account, "sara") == []
    with as_account("sara"):
        assert asyncio.run(mastery_path.list_topic_index())["topics"] == []
        with pytest.raises(HTTPException) as denied:
            asyncio.run(
                mastery_path.require_instance_access(
                    SimpleNamespace(path_params={"path_id": sara_instance.path_id})
                )
            )
    assert denied.value.status_code == 403

    with as_account("tina"):
        TeachingService().assign(sara_instance.template_id, [teaching_users["sara"]["id"]])

    assert [t["path_id"] for t in _topics(as_account, "sara")] == [sara_instance.path_id]
    with as_account("sara"):
        assert LearningStore().load(sara_instance.path_id).mastery_levels[point] == 0.7


def test_archiving_closes_the_path(sara_instance, as_account):
    with as_account("tina"):
        TeachingService().archive(sara_instance.template_id)

    assert _topics(as_account, "sara") == []
    with as_account("sara"):
        with pytest.raises(HTTPException):
            asyncio.run(
                mastery_path.require_instance_access(
                    SimpleNamespace(path_params={"book_id": sara_instance.path_id})
                )
            )


def test_get_topic_describes_the_teacher(sara_instance, as_account):
    with as_account("sara"):
        payload = asyncio.run(mastery_path.get_topic(sara_instance.path_id))
    assert payload["origin"]["teacher_name"] == "tina"
    assert payload["origin"]["kind"] == "teacher_template"


# ---- root preflight items 6/7 and binding constraints -----------------------


def _auth(name: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {name}"}


def _personal_path(teacher_draft, name: str, path_id: str) -> str:
    return teacher_draft(name, path_id=path_id)


def test_personal_paths_never_open_the_teaching_store(
    as_account, teacher_draft, monkeypatch
):
    from deeptutor.learning import navigation
    from deeptutor.learning.service import LearningService

    _personal_path(teacher_draft, "sara", "topic_own")
    _personal_path(teacher_draft, "uma", "topic_uma")

    def _refuse(*_args, **_kwargs):
        raise AssertionError("TeachingStore was opened for a path without an origin")

    monkeypatch.setattr(TeachingStore, "__init__", _refuse)

    with as_account("sara"):
        for key in ("path_id", "book_id"):
            assert (
                asyncio.run(
                    mastery_path.require_instance_access(
                        SimpleNamespace(path_params={key: "topic_own"})
                    )
                )
                is None
            )
        assert asyncio.run(mastery_path.get_topic("topic_own"))["origin"] is None
        index = asyncio.run(mastery_path.list_topic_index())["topics"]
        assert [row["path_id"] for row in index] == ["topic_own"]
        progress = asyncio.run(mastery_path.list_all_progress())
        assert [row["book_id"] for row in progress["summaries"]] == ["topic_own"]
        assert [row["path_id"] for row in LearningService().list_path_overviews()] == [
            "topic_own"
        ]
        assert [card["path_id"] for card in navigation.topic_cards()["topics"]] == ["topic_own"]
        assert navigation.find_topic("topic_own")["path_id"] == "topic_own"
    # Not a student: listing never looks for assignments either.
    assert [t["path_id"] for t in _topics(as_account, "uma")] == ["topic_uma"]


def test_legacy_progress_list_and_every_path_route_close_a_revoked_instance(
    sara_instance, teaching_http, teacher_draft, teaching_users, as_account
):
    from deeptutor.teaching.instances import INSTANCE_UNAVAILABLE

    client = teaching_http.client
    _personal_path(teacher_draft, "sara", "topic_own")
    path_id = sara_instance.path_id

    listed = client.get("/api/mastery-paths/progress", headers=_auth("sara")).json()
    assert {row["book_id"] for row in listed["summaries"]} == {path_id, "topic_own"}
    assert client.get(f"/api/mastery-paths/topics/{path_id}", headers=_auth("sara")).status_code == 200

    with as_account("tina"):
        TeachingService().revoke(sara_instance.template_id, teaching_users["sara"]["id"])

    listed = client.get("/api/mastery-paths/progress", headers=_auth("sara")).json()
    assert [row["book_id"] for row in listed["summaries"]] == ["topic_own"]
    topics = client.get("/api/mastery-paths/topics", headers=_auth("sara")).json()["topics"]
    assert [row["path_id"] for row in topics] == ["topic_own"]
    index = client.get("/api/mastery-paths/topics/index", headers=_auth("sara")).json()
    assert [row["path_id"] for row in index["topics"]] == ["topic_own"]

    for method, url in (
        ("GET", f"/api/mastery-paths/topics/{path_id}"),
        ("GET", f"/api/mastery-paths/topics/{path_id}/sessions"),
        ("GET", f"/api/mastery-paths/topics/{path_id}/review-settings"),
        ("GET", f"/api/mastery-paths/progress/{path_id}"),
        ("GET", f"/api/mastery-paths/progress/{path_id}/map"),
        ("GET", f"/api/mastery-paths/progress/{path_id}/board"),
        ("GET", f"/api/mastery-paths/progress/{path_id}/events"),
        ("POST", f"/api/mastery-paths/progress/{path_id}/skip-question"),
        ("DELETE", f"/api/mastery-paths/progress/{path_id}"),
    ):
        response = client.request(method, url, headers=_auth("sara"))
        assert response.status_code == 403, url
        assert response.json()["detail"] == INSTANCE_UNAVAILABLE

    # Revoking retains the student's copy; the personal path stays open.
    with as_account("sara"):
        assert LearningStore().exists(path_id)
    assert client.get("/api/mastery-paths/topics/topic_own", headers=_auth("sara")).status_code == 200


def test_archived_instances_leave_the_course_and_navigation_lists(sara_instance, as_account):
    from deeptutor.learning import navigation
    from deeptutor.learning.service import LearningService

    with as_account("sara"):
        assert [row["path_id"] for row in LearningService().list_path_overviews()] == [
            sara_instance.path_id
        ]
        assert [card["path_id"] for card in navigation.topic_cards()["topics"]] == [
            sara_instance.path_id
        ]
        assert navigation.find_topic(sara_instance.path_id) is not None
    with as_account("tina"):
        TeachingService().archive(sara_instance.template_id)
    with as_account("sara"):
        assert LearningService().list_path_overviews() == []
        assert navigation.topic_cards()["topics"] == []
        assert navigation.find_topic(sara_instance.path_id) is None
        assert asyncio.run(mastery_path.list_all_progress())["summaries"] == []


def test_websocket_subscription_refuses_a_closed_instance(
    sara_instance, teaching_http, as_account, teaching_users
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from deeptutor.teaching.instances import INSTANCE_UNAVAILABLE

    app = FastAPI()
    app.include_router(mastery_path.ws_router, prefix="/ws")
    client = TestClient(app)

    with client.websocket_connect("/ws/mastery-paths?token=sara") as socket:
        socket.send_json({"type": "subscribe", "path_id": sara_instance.path_id})
        assert socket.receive_json()["type"] == "subscribed"

    with as_account("tina"):
        TeachingService().revoke(sara_instance.template_id, teaching_users["sara"]["id"])

    with client.websocket_connect("/ws/mastery-paths?token=sara") as socket:
        socket.send_json({"type": "subscribe", "path_id": sara_instance.path_id})
        assert socket.receive_json() == {"type": "error", "content": INSTANCE_UNAVAILABLE}


def test_other_content_workspaces_neither_create_nor_list_instances(
    as_account, teacher_draft, publish_template
):
    from deeptutor.services.workspace import get_content_workspace_service
    from deeptutor.services.workspace.context import workspace_context

    teacher_draft()
    template_id = publish_template(students=("sara",))["template"]["id"]
    with as_account("sara"):
        workspace_id = get_content_workspace_service().create_workspace("Physics")["workspace_id"]
        with workspace_context(workspace_id):
            assert asyncio.run(mastery_path.list_topics())["topics"] == []
            assert not LearningStore().exists(f"assigned_{template_id}")
        assert not LearningStore().exists(f"assigned_{template_id}")

    assert [t["path_id"] for t in _topics(as_account, "sara")] == [f"assigned_{template_id}"]
    with as_account("sara"), workspace_context(workspace_id):
        assert asyncio.run(mastery_path.list_topics())["topics"] == []


def test_applied_revision_survives_new_revisions_and_reassignment(
    sara_instance, as_account, teaching_users
):
    teaching = TeachingStore()
    sara_id = teaching_users["sara"]["id"]
    first = teaching.get_revision(sara_instance.template_id, 1)
    template = teaching.get_template(sara_instance.template_id)
    teaching.add_revision(
        first.model_copy(update={"revision": 2}),
        aliases=template.kp_aliases,
        title=template.title,
    )

    _topics(as_account, "sara")
    assignment = teaching.get_assignment(sara_instance.template_id, sara_id)
    assert (assignment.instance_path_id, assignment.applied_revision) == (
        sara_instance.path_id,
        1,
    )
    with as_account("sara"):
        assert LearningStore().load_origin(sara_instance.path_id).revision == 1

    with as_account("tina"):
        TeachingService().revoke(sara_instance.template_id, sara_id)
        TeachingService().assign(sara_instance.template_id, [sara_id])
    _topics(as_account, "sara")
    assignment = teaching.get_assignment(sara_instance.template_id, sara_id)
    assert (assignment.status, assignment.applied_revision) == ("active", 1)


def test_an_unrecorded_copy_is_recorded_at_its_own_revision(
    as_account, teacher_draft, publish_template, teaching_users
):
    from deeptutor.teaching.instances import instance_path_id, materialize_instance

    teacher_draft()
    template_id = publish_template(students=("sara",))["template"]["id"]
    teaching = TeachingStore()
    template = teaching.get_template(template_id)
    revision = teaching.get_revision(template_id, 1)
    path_id = instance_path_id(template_id)
    with as_account("sara"):
        # The copy committed but its bookkeeping did not (e.g. a crash between).
        materialize_instance(LearningStore(), path_id, template, revision)
    teaching.add_revision(
        revision.model_copy(update={"revision": 2}),
        aliases=template.kp_aliases,
        title=template.title,
    )

    assert [t["path_id"] for t in _topics(as_account, "sara")] == [path_id]
    assignment = teaching.get_assignment(template_id, teaching_users["sara"]["id"])
    assert (assignment.instance_path_id, assignment.applied_revision) == (path_id, 1)


def test_a_personal_path_under_the_instance_id_is_never_adopted(
    as_account, teacher_draft, publish_template, teaching_users
):
    teacher_draft()
    template_id = publish_template(students=("sara",))["template"]["id"]
    _personal_path(teacher_draft, "sara", f"assigned_{template_id}")

    topics = _topics(as_account, "sara")
    assert [(t["path_id"], t["origin"]) for t in topics] == [(f"assigned_{template_id}", None)]
    assignment = TeachingStore().get_assignment(template_id, teaching_users["sara"]["id"])
    assert (assignment.instance_path_id, assignment.applied_revision) == ("", 0)


def test_two_copies_with_shared_source_ids_coexist(as_account, teacher_draft, publish_template):
    from deeptutor.learning.models import TopicSource, TopicSourceKind
    from deeptutor.teaching.instances import materialize_instance

    teacher_draft()
    template_id = publish_template()["template"]["id"]
    teaching = TeachingStore()
    template = teaching.get_template(template_id)
    revision = teaching.get_revision(template_id, 1)
    revision.sources.sources = [
        TopicSource(id="src_shared", kind=TopicSourceKind.KNOWLEDGE_BASE, label="Notes")
    ]
    with as_account("sara"):
        store = LearningStore()
        materialize_instance(store, "assigned_a", template, revision)
        materialize_instance(store, "assigned_b", template, revision)
        ids = {
            path_id: [source.id for source in store.get_topic(path_id).sources]
            for path_id in ("assigned_a", "assigned_b")
        }
    assert len(ids["assigned_a"]) == len(ids["assigned_b"]) == 1
    assert ids["assigned_a"] != ids["assigned_b"]
