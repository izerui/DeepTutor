"""Teachers no longer manage learners (spec 2026-11-05-remove-teacher-management).

No comments, no naming who receives content, no "learners who need
attention", no deadlines, opening times or calendar. Older data keeps
working: stored deadlines and opening times are not read, ``after_due``
behaves as ``after_submit``, and accounts an older scope named still reach it.
"""

from __future__ import annotations

import asyncio
import importlib.util
import time

import pytest

from deeptutor.practice_sets.service import PracticeSetService
from deeptutor.publishing import scope as scopes
from deeptutor.tasks.service import StudentTasks, TaskService

QUESTIONS = [
    {"question": "2 + 2 = ?", "question_type": "choice", "options": {"A": "3", "B": "4"},
     "correct_answer": "B", "explanation": "Add."},
]


@pytest.fixture
def http(teaching_users, monkeypatch):
    """The task, practice-set, notification, home and market routers behind real auth."""
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    import deeptutor.api.routers.auth as auth_router
    from deeptutor.api.routers import catalog, home, notifications, practice_sets, tasks
    from deeptutor.services.auth import TokenPayload

    tokens = {n: TokenPayload(username=n, role=r["role"], user_id=r["id"]) for n, r in teaching_users.items()}
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "decode_token", lambda token: tokens.get(token))
    app = FastAPI()
    auth = [Depends(auth_router.require_auth)]
    for router, prefix in (
        (tasks.router, "/api/tasks"), (practice_sets.router, "/api/practice-sets"),
        (notifications.router, "/api/notifications"),
        (home.router, "/api/home"), (catalog.router, "/api/catalog"),
    ):
        app.include_router(router, prefix=prefix, dependencies=auth)
    client = TestClient(app)

    def call(method: str, url: str, name: str = "tina", **kwargs):
        return client.request(method, url, headers={"Authorization": f"Bearer {name}"}, **kwargs)

    return call


def _draft_set(http) -> str:
    created = http("POST", "/api/practice-sets", json={"title": "Week 1"}).json()["set"]
    assert http("PUT", f"/api/practice-sets/{created['id']}", json={"questions": QUESTIONS}).status_code == 200
    return created["id"]


# ── naming who receives content ────────────────────────────


def test_publishing_names_nobody(http, teaching_users):
    """Audit fix B §6: publishing takes no receivers; whatever an older client sends names nobody."""
    sara = teaching_users["sara"]["id"]
    set_id = _draft_set(http)
    published = http("POST", f"/api/practice-sets/{set_id}/publish", json={"scope": {"users": [sara]}})
    assert published.status_code == 200, published.text
    assert scopes.get_scope(scopes.PRACTICE_SET, set_id).users == []

    course = http("POST", "/api/tasks/courses", json={"title": "Waves"}).json()["course"]
    http("POST", f"/api/tasks/courses/{course['id']}/tasks", json={"kind": "note", "title": "Read"})
    assert http("POST", f"/api/tasks/courses/{course['id']}/publish",
                json={"scope": {"users": [sara]}}).status_code == 200
    assert scopes.get_scope(scopes.COURSE, course["id"]).users == []

    # Nothing changes who receives it any more.
    assert http("PUT", f"/api/publish/practice_set/{set_id}/scope",
                json={"everyone": False, "users": [sara]}).status_code in (404, 405)
    assert http("GET", f"/api/publish/practice_set/{set_id}/scope").status_code in (404, 405)
    assert http("GET", "/api/publish/people?q=sa").status_code in (404, 405)


def test_an_older_named_scope_still_reaches_its_learner(as_account, teaching_users):
    sara = teaching_users["sara"]["id"]
    with as_account("tina"):
        created = PracticeSetService().create("Remedial: Week 1")
        PracticeSetService().update(created["id"], questions=QUESTIONS)
    # Older data: a remedial set sent to Sara by name.
    scopes._store_scope(scopes.PRACTICE_SET, created["id"], scopes.Scope(users=[sara]),
                        actor_id=teaching_users["tina"]["id"])
    with as_account("tina"):
        PracticeSetService().publish(created["id"])
    with as_account("sara"):
        assert [s["id"] for s in PracticeSetService().assigned()] == [created["id"]]
        assert PracticeSetService().take(created["id"])["questions"]
    with as_account("sam"):
        assert PracticeSetService().assigned() == []


def test_teacher_remedial_and_comments_are_gone(http):
    assert importlib.util.find_spec("deeptutor.comments") is None
    assert importlib.util.find_spec("deeptutor.practice_sets.remedial") is None
    assert importlib.util.find_spec("deeptutor.catalog.attention") is None
    assert importlib.util.find_spec("deeptutor.catalog.learners") is None
    for path in ("/api/practice-sets/remedial/from-set", "/api/practice-sets/remedial/from-path"):
        assert http("POST", path, json={}).status_code in (404, 405), path


# ── deadlines, opening times, the calendar ─────────────────


def test_updates_refuse_deadlines_and_opening_times(http):
    task = http("POST", "/api/tasks", json={"kind": "note", "title": "Read"}).json()["task"]
    assert "due_at" not in task and "open_at" not in task
    for body in ({"due_at": time.time() + 3600}, {"open_at": time.time() + 60}, {"due_at": None}):
        assert http("PUT", f"/api/tasks/{task['id']}", json=body).status_code == 422, body
    assert http("PUT", f"/api/tasks/{task['id']}", json={"description": "Chapter 1"}).status_code == 200

    set_id = _draft_set(http)
    for body in ({"due_at": time.time() + 3600}, {"clear_due": True}, {"reveal": "after_due"}):
        assert http("PUT", f"/api/practice-sets/{set_id}", json=body).status_code == 422, body
    updated = http("PUT", f"/api/practice-sets/{set_id}", json={"reveal": "after_submit"}).json()["set"]
    assert updated["reveal"] == "after_submit" and "due_at" not in updated
    assert http("GET", "/api/tasks/calendar").status_code in (404, 405)


def test_an_older_task_past_its_deadline_is_just_open(as_account, in_course, teaching_users, monkeypatch):
    from deeptutor.notifications import service as notifications
    from deeptutor.tasks.store import db

    with as_account("tina"):
        task = TaskService().create("note", "Revise")
    with db.write() as conn:  # older data: a deadline passed, an opening time still to come
        conn.execute("UPDATE tasks SET due_at = ?, open_at = ? WHERE id = ?",
                     (time.time() - 3600, time.time() + 3600, task["id"]))
    in_course(task["id"])
    with as_account("sara"):
        [entry] = StudentTasks().assigned()
        assert entry["state"] == "open" and entry["description"] == ""
        assert not {"due_at", "open_at", "late"} & set(entry)
        done = StudentTasks().mark_done(task["id"])
    assert done["state"] == "completed" and "late" not in done
    with as_account("tina"):
        detail = TaskService().detail(task["id"])
    assert "due_at" not in detail and all("late" not in s for s in detail["students"])
    kinds = {n["kind"] for n in notifications.list_for(teaching_users["sara"]["id"])}
    assert not kinds & {"task_due_soon", "task_opened", "task_due_changed"}


def test_an_older_after_due_set_shows_answers_after_submitting(as_account, deliver):
    from deeptutor.practice_sets import store

    with as_account("tina"):
        created = PracticeSetService().create("Quiz")
        PracticeSetService().update(created["id"], questions=QUESTIONS)
        PracticeSetService().publish(created["id"])
    with store.write() as conn:  # older data: answers after a deadline still a day away
        conn.execute("UPDATE practice_sets SET reveal = 'after_due', due_at = ? WHERE id = ?",
                     (time.time() + 86400, created["id"]))
    deliver(scopes.PRACTICE_SET, created["id"], "sara")
    with as_account("sara"):
        taken = PracticeSetService().take(created["id"])
        assert not taken["answers_revealed"] and "correct_answer" not in taken["questions"][0]
        qid = taken["questions"][0]["question_id"]
        result = asyncio.run(PracticeSetService().submit(created["id"], {qid: "B"}))
    assert result["answers_revealed"] and result["score"] == 1 and "late" not in result
    assert result["results"][0]["correct_answer"] == "B"
    with as_account("tina"):
        report = PracticeSetService().report(created["id"])
    assert "due_at" not in report and all("late" not in s for s in report["students"])


# ── learners who need attention ────────────────────────────


def test_no_attention_on_the_workbench_or_in_reports(http, as_account, in_course):
    import csv
    import io

    with as_account("tina"):
        task = TaskService().create("note", "Read")
    course_id = in_course(task["id"])
    board = http("GET", "/api/home/workbench").json()
    assert set(board) == {"role", "sent"}
    report = http("GET", f"/api/catalog/reports/course/{course_id}")
    assert report.status_code == 200, report.text
    rows = report.json()["learners"]
    assert {row["username"] for row in rows} == {"sara", "sam"}
    assert all("flags" not in row for row in rows)
    exported = http("GET", f"/api/catalog/reports/course/{course_id}/export")
    header = next(csv.reader(io.StringIO(exported.text.lstrip("﻿"))))
    assert "attention" not in header and header[0] == "learner"


def test_scores_cannot_be_changed_by_hand():
    """Spec 2026-11-05: scores come from the checker or the AI judge; the author only reads attempts."""
    from deeptutor.api.routers import practice_sets
    from deeptutor.practice_sets.review import PracticeReview

    assert not hasattr(PracticeReview, "regrade")
    assert not [r for r in practice_sets.router.routes if "/questions/" in getattr(r, "path", "")]
