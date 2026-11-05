"""Two roles: administrators and learners (spec 2026-11-05-one-learner-role).

Everyone who is not an administrator is a learner, whatever their record
stores (``student``, or the older ``teacher`` and ``user``). Learners author,
publish and list content and read the learning reports of what they made;
administrators read those reports too, read-only. Feature switches are gone;
the daily AI limit applies to every learner.
"""

from __future__ import annotations

import csv
import io
import json

import pytest

QUESTIONS = [
    {"question": "2 + 2 = ?", "question_type": "choice", "options": {"A": "3", "B": "4"},
     "correct_answer": "B", "explanation": "Add.", "points": 1},
]
META = {"title": "Arithmetic", "summary": "Add.", "stages": ["primary"], "subjects": ["primary.math"],
        "difficulty": "beginner", "tags": []}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    async def ungraded(pending):
        return {}

    monkeypatch.setattr("deeptutor.practice_sets.service._judge", ungraded)


@pytest.fixture
def http(teaching_users, monkeypatch):
    """The auth, authoring and catalog routers behind real auth; the bearer token is the username."""
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    import deeptutor.api.routers.auth as auth_router
    from deeptutor.api.routers import catalog, home, practice_sets, tasks
    from deeptutor.services.auth import TokenPayload

    tokens = {n: TokenPayload(username=n, role=r["role"], user_id=r["id"]) for n, r in teaching_users.items()}
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "decode_token", lambda token: tokens.get(token))
    app = FastAPI()
    auth = [Depends(auth_router.require_auth)]
    app.include_router(auth_router.router, prefix="/api/auth")
    for router, prefix in (
        (tasks.router, "/api/tasks"), (practice_sets.router, "/api/practice-sets"),
        (home.router, "/api/home"), (catalog.router, "/api/catalog"),
    ):
        app.include_router(router, prefix=prefix, dependencies=auth)
    client = TestClient(app)

    def call(method: str, url: str, name: str, **kwargs):
        return client.request(method, url, headers={"Authorization": f"Bearer {name}"}, **kwargs)

    return call


def _list(http, kind: str, item_id: str, author: str) -> None:
    assert http("PUT", f"/api/catalog/listings/{kind}/{item_id}", author, json=META).status_code == 200
    assert http("POST", f"/api/catalog/listings/{kind}/{item_id}/submit", author).status_code == 200
    # The first listing still waits for an administrator.
    assert http("POST", f"/api/catalog/review/{kind}/{item_id}/approve", "adam").status_code == 200
    mine = http("GET", "/api/catalog/listings/mine", author).json()["listings"]
    assert any(entry["id"] == item_id and entry["status"] == "listed" for entry in mine)


def _published_set(http, author: str) -> str:
    created = http("POST", "/api/practice-sets", author, json={"title": "Week 1"})
    assert created.status_code == 200, created.text
    set_id = created.json()["set"]["id"]
    assert http("PUT", f"/api/practice-sets/{set_id}", author, json={"questions": QUESTIONS}).status_code == 200
    published = http("POST", f"/api/practice-sets/{set_id}/publish", author)
    assert published.status_code == 200, published.text
    return set_id


# ── authoring, publishing, listing, reports ────────────────


@pytest.mark.parametrize("author", ["sara", "tina"])  # a learner, and a legacy teacher record
def test_a_learner_writes_publishes_and_lists_a_practice_set_and_reads_its_results(http, author):
    set_id = _published_set(http, author)
    _list(http, "practice_set", set_id, author)
    taker = "sam"
    assert http("POST", f"/api/catalog/items/practice_set/{set_id}/join", taker).status_code == 200
    taken = http("GET", f"/api/practice-sets/{set_id}/take", taker).json()["set"]
    qid = taken["questions"][0]["question_id"]
    submitted = http("POST", f"/api/practice-sets/{set_id}/submit", taker, json={"answers": {qid: "B"}})
    assert submitted.status_code == 200, submitted.text
    submission_id = submitted.json()["submission_id"]

    report = http("GET", f"/api/practice-sets/{set_id}/report", author)
    assert report.status_code == 200, report.text
    assert [s["username"] for s in report.json()["students"] if s["completed"]] == [taker]
    # Another learner sees nothing of it.
    assert http("GET", f"/api/practice-sets/{set_id}/report", "uma").status_code == 404
    assert http("GET", f"/api/practice-sets/{set_id}/submissions/{submission_id}", "uma").status_code == 404
    # An administrator reads the results, the export and one attempt.
    assert http("GET", f"/api/practice-sets/{set_id}/report", "adam").status_code == 200
    exported = http("GET", f"/api/practice-sets/{set_id}/export", "adam")
    assert exported.status_code == 200
    header = next(csv.reader(io.StringIO(exported.text.lstrip("﻿"))))
    assert header[0] == "learner"
    attempt = http("GET", f"/api/practice-sets/{set_id}/submissions/{submission_id}", "adam")
    assert attempt.status_code == 200 and attempt.json()["attempt"]["questions"][0]["answer"] == "B"


@pytest.mark.parametrize("author", ["sara", "tina"])
def test_a_learner_publishes_and_lists_a_course_and_reads_its_learning_report(http, author, teaching_users):
    course = http("POST", "/api/tasks/courses", author, json={"title": "Algebra"})
    assert course.status_code == 200, course.text
    course_id = course.json()["course"]["id"]
    added = http("POST", f"/api/tasks/courses/{course_id}/tasks", author, json={"kind": "note", "title": "Read"})
    assert added.status_code == 200, added.text
    assert http("POST", f"/api/tasks/courses/{course_id}/publish", author).status_code == 200
    _list(http, "course", course_id, author)
    assert http("POST", f"/api/catalog/items/course/{course_id}/join", "sam").status_code == 200

    url = f"/api/catalog/reports/course/{course_id}"
    report = http("GET", url, author)
    assert report.status_code == 200, report.text
    assert [row["username"] for row in report.json()["learners"]] == ["sam"]
    # Another learner — even one who joined it — cannot read it.
    assert http("GET", url, "sam").status_code == 404
    assert http("GET", url, "uma").status_code == 404
    # An administrator reads the report, its export and one learner's detail.
    admin_view = http("GET", url, "adam")
    assert admin_view.status_code == 200 and admin_view.json()["learners"] == report.json()["learners"]
    assert http("GET", f"{url}/export", "adam").status_code == 200
    sam = teaching_users["sam"]["id"]
    detail = http("GET", f"{url}/learners/{sam}", "adam")
    assert detail.status_code == 200 and detail.json()["username"] == "sam"


def test_every_learner_gets_a_workbench(http):
    for name in ("sara", "tina", "uma"):
        board = http("GET", "/api/home/workbench", name).json()
        assert board["role"] == "learner" and "sent" in board
    assert http("GET", "/api/home/workbench", "adam").json() == {"role": "admin"}
    assert "can_create_path" not in http("GET", "/api/home", "sara").json()


# ── roles in the API ───────────────────────────────────────


def test_status_shows_admin_or_learner_and_no_policy(http):
    for name, role in (("adam", "admin"), ("tina", "learner"), ("sara", "learner"), ("uma", "learner")):
        body = http("GET", "/api/auth/status", name).json()
        assert body["role"] == role
        assert "is_teacher" not in body and "student_policy" not in body
    users = {u["username"]: u["role"] for u in http("GET", "/api/auth/users", "adam").json()}
    assert users == {"adam": "admin", "tina": "learner", "tom": "learner", "sara": "learner",
                     "sam": "learner", "uma": "learner"}


def test_a_role_is_set_as_admin_or_learner_only(http):
    from deeptutor.multi_user.identity import get_user

    for bad in ("teacher", "student", "user"):
        assert http("PUT", "/api/auth/users/sam/role", "adam", json={"role": bad}).status_code == 422
    changed = http("PUT", "/api/auth/users/tina/role", "adam", json={"role": "learner"})
    assert changed.status_code == 200 and changed.json()["role"] == "learner"
    assert get_user("tina")["role"] == "student"
    promoted = http("PUT", "/api/auth/users/sam/role", "adam", json={"role": "admin"})
    assert promoted.status_code == 200 and promoted.json()["role"] == "admin"
    assert get_user("sam")["role"] == "admin"


def test_an_administrator_creates_learners_only(http):
    from deeptutor.multi_user.identity import get_user

    for bad in ("teacher", "student", "admin"):
        response = http("POST", "/api/auth/users", "adam",
                        json={"username": f"new_{bad}", "password": "password1234", "role": bad})
        assert response.status_code == 422
    made = http("POST", "/api/auth/users", "adam", json={"username": "lena", "password": "password1234"})
    assert made.status_code in (200, 201), made.text
    assert made.json()["role"] == "learner"
    assert get_user("lena")["role"] == "student"


def test_hand_over_goes_to_any_active_learner(as_account, teaching_users):
    from deeptutor.tasks.courses import CourseService
    from deeptutor.tasks.service import TaskError

    with as_account("adam"):
        course = CourseService().create("School course")
        with pytest.raises(TaskError):
            CourseService().hand_over(course["id"], teaching_users["adam"]["id"])
        handed = CourseService().hand_over(course["id"], teaching_users["sara"]["id"])
    assert handed["owner_user_id"] == teaching_users["sara"]["id"]


# ── features and the AI limit ──────────────────────────────


@pytest.mark.parametrize("name", ["sara", "tina"])
def test_former_switches_do_not_gate_anything(name, as_account, teaching_users):
    """Personal paths, books and private knowledge bases need no grant (an old stored switch is ignored)."""
    from deeptutor.multi_user.book_access import can_create_book
    from deeptutor.multi_user.grants import grant_path

    grant_path(teaching_users[name]["id"]).write_text(json.dumps({"student_policy": {
        "allow_personal_mastery": False, "allow_personal_book": False, "allow_private_kb": False,
    }}), encoding="utf-8")
    with as_account(name):
        assert can_create_book() is True


def test_the_ai_limit_applies_to_a_legacy_teacher_record(as_account, teaching_users):
    import time
    import uuid

    from deeptutor.multi_user.grants import load_grant, save_grant
    from deeptutor.services.llm.usage_ledger import ledger_path, record_call
    from deeptutor.services.llm.usage_limit import AiLimitReached, admit_call, status_for_current_user

    tina = teaching_users["tina"]["id"]
    save_grant(tina, {**load_grant(tina), "ai_daily_tokens": 100})
    with as_account("tina"):
        record_call(ledger_path(), {"call_id": uuid.uuid4().hex, "total_tokens": 150},
                    started_at=time.time(), source="chat")
        assert status_for_current_user()["limit"] == 100
        with pytest.raises(AiLimitReached):
            admit_call(None)
