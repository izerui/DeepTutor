"""Teacher endpoints over HTTP."""

from __future__ import annotations

BASE = "/api/mastery-paths/teaching"


def _auth(name: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {name}"}


def test_students_and_plain_users_cannot_reach_teacher_routes(teaching_http):
    for name in ("sara", "uma"):
        assert teaching_http.client.get(f"{BASE}/templates", headers=_auth(name)).status_code == 403
        assert teaching_http.client.get(f"{BASE}/students", headers=_auth(name)).status_code == 403


def test_student_picker_exposes_only_public_fields(teaching_http):
    body = teaching_http.client.get(f"{BASE}/students", headers=_auth("tina")).json()

    assert sorted(s["username"] for s in body["students"]) == ["sam", "sara"]
    assert set(body["students"][0]) == {"id", "username", "avatar"}
    narrowed = teaching_http.client.get(f"{BASE}/students?q=sam", headers=_auth("tina")).json()
    assert [s["username"] for s in narrowed["students"]] == ["sam"]


def test_teacher_publishes_and_manages_assignments(teaching_http, teacher_draft, quiet_change_model):
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

    published = client.post(
        f"{BASE}/templates/{template_id}/publish",
        json={
            "draft_revision": preview.json()["draft_revision"],
            "student_ids": [users["sara"]["id"]],
        },
        headers=_auth("tina"),
    )
    assert published.status_code == 200
    assert published.json()["template"]["current_revision"] == 1
    assert published.json()["template"]["assigned_count"] == 1

    added = client.post(
        f"{BASE}/templates/{template_id}/assignments",
        json={"student_ids": [users["sam"]["id"]]},
        headers=_auth("tina"),
    ).json()["assignments"]
    assert sorted(row["username"] for row in added) == ["sam", "sara"]

    revoked = client.delete(
        f"{BASE}/templates/{template_id}/assignments/{users['sam']['id']}", headers=_auth("tina")
    )
    assert revoked.status_code == 200
    rows = client.get(f"{BASE}/templates/{template_id}/assignments", headers=_auth("tina")).json()
    assert {row["username"]: row["status"] for row in rows["assignments"]} == {
        "sara": "active",
        "sam": "revoked",
    }

    assert client.delete(f"{BASE}/templates/{template_id}", headers=_auth("tina")).status_code == 409
    archived = client.post(f"{BASE}/templates/{template_id}/archive", headers=_auth("tina"))
    assert archived.json()["status"] == "archived"


def test_other_teachers_get_404_and_admins_manage_but_do_not_publish(teaching_http, teacher_draft):
    teacher_draft()
    client = teaching_http.client
    template_id = client.post(
        f"{BASE}/templates", json={"path_id": "topic_calculus"}, headers=_auth("tina")
    ).json()["id"]

    assert client.get(f"{BASE}/templates/{template_id}/assignments", headers=_auth("tom")).status_code == 404
    assert client.get(f"{BASE}/templates/{template_id}/assignments", headers=_auth("adam")).status_code == 200
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
