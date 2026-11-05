"""Teacher endpoints over HTTP."""

from __future__ import annotations

BASE = "/api/mastery-paths/teaching"


def _auth(name: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {name}"}


def test_students_and_plain_users_cannot_reach_teacher_routes(teaching_http):
    for name in ("sara", "uma"):
        assert teaching_http.client.get(f"{BASE}/templates", headers=_auth(name)).status_code == 403


def test_teacher_publishes_and_learners_join(teaching_http, teacher_draft, quiet_change_model):
    from tests.multi_user.path_members import join_path

    teacher_draft()
    client, users = teaching_http.client, teaching_http.users

    created = client.post(
        f"{BASE}/templates", json={"path_id": "topic_calculus"}, headers=_auth("tina")
    )
    assert created.status_code == 200
    template_id = created.json()["id"]

    preview = client.post(f"{BASE}/templates/{template_id}/publish/preview", headers=_auth("tina"))
    assert preview.status_code == 200
    assert preview.json()["can_publish"] is True
    assert preview.json()["changes"]["first_publish"] is True
    assert "needs_students" not in preview.json()

    # Nobody is handed the path at publication (spec 2026-11-05 remove-teacher-management §1.2).
    refused = client.post(
        f"{BASE}/templates/{template_id}/publish",
        json={"draft_revision": preview.json()["draft_revision"], "student_ids": [users["sara"]["id"]]},
        headers=_auth("tina"),
    )
    assert refused.status_code == 422
    published = client.post(
        f"{BASE}/templates/{template_id}/publish",
        json={"draft_revision": preview.json()["draft_revision"]},
        headers=_auth("tina"),
    )
    assert published.status_code == 200
    assert published.json()["template"]["current_revision"] == 1
    assert published.json()["template"]["assigned_count"] == 0

    join_path(template_id, users["sara"]["id"])
    listed = client.get(f"{BASE}/templates", headers=_auth("tina")).json()["templates"]
    assert [t["assigned_count"] for t in listed if t["id"] == template_id] == [1]

    assert client.delete(f"{BASE}/templates/{template_id}", headers=_auth("tina")).status_code == 409
    archived = client.post(f"{BASE}/templates/{template_id}/archive", headers=_auth("tina"))
    assert archived.json()["status"] == "archived"


def test_the_assignment_endpoints_are_gone(teaching_http, teacher_draft, publish_template):
    teacher_draft()
    template_id = publish_template(students=("sara",))["template"]["id"]
    client, users = teaching_http.client, teaching_http.users

    for method, url, body in (
        ("get", f"{BASE}/templates/{template_id}/assignments", None),
        ("post", f"{BASE}/templates/{template_id}/assignments", {"student_ids": [users["sam"]["id"]]}),
        ("delete", f"{BASE}/templates/{template_id}/assignments/{users['sara']['id']}", None),
        ("get", f"{BASE}/students", None),
    ):
        kwargs = {"json": body} if body is not None else {}
        response = getattr(client, method)(url, headers=_auth("tina"), **kwargs)
        assert response.status_code in (404, 405), (method, url, response.status_code)
    from deeptutor.teaching.store import TeachingStore

    assert {a.student_user_id for a in TeachingStore().list_assignments(template_id) if a.status == "active"} == {
        users["sara"]["id"]
    }


def test_other_teachers_get_404_and_admins_do_not_publish(teaching_http, teacher_draft):
    teacher_draft()
    client = teaching_http.client
    template_id = client.post(
        f"{BASE}/templates", json={"path_id": "topic_calculus"}, headers=_auth("tina")
    ).json()["id"]

    assert client.post(f"{BASE}/templates/{template_id}/archive", headers=_auth("tom")).status_code == 404
    denied = client.post(f"{BASE}/templates/{template_id}/publish/preview", headers=_auth("adam"))
    assert denied.status_code == 403
    assert "owns" in denied.json()["detail"]


def test_blocked_publish_reports_issues(teaching_http, teacher_draft, quiet_change_model):
    from deeptutor.learning.models import TopicSource, TopicSourceKind

    teacher_draft(
        sources=(
            TopicSource(id="s0", kind=TopicSourceKind.CHAT, source_id="c1", label="A chat", position=0),
        )
    )
    client = teaching_http.client
    template_id = client.post(
        f"{BASE}/templates", json={"path_id": "topic_calculus"}, headers=_auth("tina")
    ).json()["id"]
    preview = client.post(f"{BASE}/templates/{template_id}/publish/preview", headers=_auth("tina")).json()

    response = client.post(
        f"{BASE}/templates/{template_id}/publish",
        json={"draft_revision": preview["draft_revision"]},
        headers=_auth("tina"),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["source_issues"][0]["code"] == "personal_source"
