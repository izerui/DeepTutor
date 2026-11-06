"""Homework was removed (spec 2026-11-05): new homework tasks are refused, by the service and by the API."""

from __future__ import annotations

import sqlite3

import pytest


def test_a_new_database_has_no_hand_ins(as_account):
    from deeptutor.tasks.service import TaskService
    from deeptutor.tasks.store import db as task_db

    with as_account("tina"):
        TaskService().create("note", "Read")
    with task_db.read() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "task_submissions" not in tables
    with task_db.write() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO tasks (id, owner_user_id, kind, title, created_at, updated_at) "
            "VALUES ('t', 'u', 'homework', 'Essay', 0, 0)"
        )


def test_homework_is_refused_by_the_service(as_account):
    from deeptutor.tasks.courses import CourseService
    from deeptutor.tasks.service import TaskError, TaskService

    for name in ("tina", "adam"):
        with as_account(name), pytest.raises(TaskError, match="Unknown kind of lesson"):
            TaskService().create("homework", "Essay")
    with as_account("tina"):
        course_id = CourseService().create("Waves")["id"]
        with pytest.raises(TaskError):
            CourseService().add_new_task(course_id, "homework", "Essay")
        assert CourseService().get(course_id)["tasks"] == []
        assert TaskService().list_mine() == []


def test_homework_is_refused_by_the_api(teaching_users, monkeypatch):
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    import deeptutor.api.routers.auth as auth_router
    from deeptutor.api.routers import tasks
    from deeptutor.services.auth import TokenPayload

    tokens = {n: TokenPayload(username=n, role=r["role"], user_id=r["id"]) for n, r in teaching_users.items()}
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "decode_token", lambda token: tokens.get(token))
    app = FastAPI()
    app.include_router(tasks.router, prefix="/api/tasks", dependencies=[Depends(auth_router.require_auth)])
    client = TestClient(app)
    tina = {"Authorization": "Bearer tina"}

    assert client.post("/api/tasks", json={"kind": "homework", "title": "Essay"}, headers=tina).status_code == 422
    course = client.post("/api/tasks/courses", json={"title": "Waves"}, headers=tina)
    assert course.status_code == 200, course.text
    course_id = course.json()["course"]["id"]
    assert client.post(f"/api/tasks/courses/{course_id}/tasks", json={"kind": "homework", "title": "Essay"},
                       headers=tina).status_code == 422
    created = client.post("/api/tasks", json={"kind": "note", "title": "Read"}, headers=tina)
    assert created.status_code == 200 and created.json()["task"]["kind"] == "note"
    # The hand-in, grading and file routes are gone.
    task_id = created.json()["task"]["id"]
    sara = teaching_users["sara"]["id"]
    for method, path in (
        ("POST", f"/api/tasks/assigned/{task_id}/submit"),
        ("POST", f"/api/tasks/{task_id}/students/{sara}/grade"),
        ("POST", f"/api/tasks/{task_id}/students/{sara}/send-back"),
        ("GET", f"/api/tasks/{task_id}/students/{sara}/files/a.txt"),
    ):
        assert client.request(method, path, headers=tina).status_code in (404, 405), path
