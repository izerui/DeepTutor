"""B3-P16 item 2: a learner's author path opens from a direct link.

The learner's copy used to be created only when the path list was read, so a
bookmarked ``/learning/mastery/assigned_<tpl>/sessions`` answered 404 until the
list had been opened once. Closure rules (R1/R8, B2-18) stay as they were: a
revoked (left) or archived assignment never gains a copy, and a closed existing copy
still answers 403 ``teacher_path_unavailable``.
"""

from __future__ import annotations

from tests.multi_user.path_members import leave_path


BASE = "/api/mastery-paths"


def _auth(name: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {name}"}


def _publish_to_sara(author_draft, publish_template) -> str:
    author_draft()
    result = publish_template(learners=("sara",))
    return result["template"]["id"]


def test_direct_open_creates_the_assigned_copy(teaching_http, author_draft, publish_template):
    template_id = _publish_to_sara(author_draft, publish_template)
    path_id = f"assigned_{template_id}"
    client = teaching_http.client

    sessions = client.get(f"{BASE}/topics/{path_id}/sessions", headers=_auth("sara"))
    assert sessions.status_code == 200, sessions.text
    assert sessions.json()["path_id"] == path_id

    topic = client.get(f"{BASE}/topics/{path_id}", headers=_auth("sara"))
    assert topic.status_code == 200, topic.text
    assert topic.json()["origin"]["template_id"] == template_id

    # The list still shows exactly one copy afterwards.
    listed = client.get(f"{BASE}/topics", headers=_auth("sara")).json()["topics"]
    assert [row["path_id"] for row in listed] == [path_id]


def test_direct_open_without_assignment_stays_404(teaching_http, author_draft, publish_template):
    template_id = _publish_to_sara(author_draft, publish_template)
    client = teaching_http.client

    for name in ("sam", "tina"):
        response = client.get(
            f"{BASE}/topics/assigned_{template_id}/sessions", headers=_auth(name)
        )
        assert response.status_code == 404, (name, response.text)


def test_left_before_first_open_gets_no_copy(teaching_http, author_draft, publish_template):
    template_id = _publish_to_sara(author_draft, publish_template)
    client, users = teaching_http.client, teaching_http.users
    leave_path(template_id, users["sara"]["id"])

    response = client.get(f"{BASE}/topics/assigned_{template_id}", headers=_auth("sara"))
    assert response.status_code == 404
    assert client.get(f"{BASE}/topics", headers=_auth("sara")).json()["topics"] == []


def test_closed_existing_copy_keeps_author_path_unavailable(
    teaching_http, author_draft, publish_template
):
    template_id = _publish_to_sara(author_draft, publish_template)
    client, users = teaching_http.client, teaching_http.users
    path_id = f"assigned_{template_id}"
    assert client.get(f"{BASE}/topics/{path_id}", headers=_auth("sara")).status_code == 200

    leave_path(template_id, users["sara"]["id"])
    response = client.get(f"{BASE}/topics/{path_id}/sessions", headers=_auth("sara"))
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "teacher_path_unavailable"
