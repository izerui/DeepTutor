"""Homework was removed (spec 2026-11-05).

Startup clean-up of an old data dir (``remove_homework``) and the refusal of
new homework tasks, by the service and by the API.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from deeptutor.notifications import service as notifications

# The ``tasks`` and ``task_submissions`` tables as databases made before 2026-11-05 have them.
OLD_SCHEMA = """
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('mastery_path', 'practice_set', 'book', 'homework', 'note')),
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    target_id TEXT NOT NULL DEFAULT '',
    settings_json TEXT NOT NULL DEFAULT '{}',
    due_at REAL,
    open_at REAL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'archived')),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    published_at REAL
);
CREATE TABLE task_submissions (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    student_id TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    files_json TEXT NOT NULL DEFAULT '[]',
    attempt INTEGER NOT NULL DEFAULT 1,
    submitted_at REAL NOT NULL,
    late INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted', 'returned', 'graded')),
    ai_score REAL,
    ai_reason TEXT NOT NULL DEFAULT '',
    score REAL,
    comment TEXT NOT NULL DEFAULT '',
    return_reason TEXT NOT NULL DEFAULT '',
    graded_by TEXT NOT NULL DEFAULT '',
    graded_at REAL,
    PRIMARY KEY (task_id, student_id)
);
CREATE INDEX idx_task_submissions_student ON task_submissions(student_id);
"""

HOMEWORK_KINDS = ("homework_submitted", "homework_graded", "homework_regraded", "homework_returned")


@pytest.fixture
def ids(teaching_users):
    return {name: row["id"] for name, row in teaching_users.items()}


def _old_tasks_db() -> None:
    from deeptutor.tasks.store import db as task_db

    path = task_db.path()
    assert not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(OLD_SCHEMA)
    finally:
        conn.close()


def _task_enrollments(item_id: str) -> int:
    from deeptutor.catalog.store import db as catalog_db

    with catalog_db.read() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM enrollments WHERE kind = 'task' AND item_id = ?", (item_id,)
        ).fetchone()[0] if conn else 0


@pytest.fixture
def old_data(as_account, ids, in_course):
    """An old data dir: a course holding a note and a homework Sara handed in."""
    from types import SimpleNamespace

    from deeptutor.catalog import enrollment
    from deeptutor.catalog.store import db as catalog_db
    from deeptutor.publishing import store as publish_store
    from deeptutor.tasks.service import StudentTasks, TaskService
    from deeptutor.tasks.store import db as task_db
    from deeptutor.tasks.store import files_root

    _old_tasks_db()
    with as_account("tina"):
        note = TaskService().create("note", "Read chapter 1")["id"]
    course_id = in_course(note)
    with as_account("sara"):
        StudentTasks().mark_done(note)

    homework = "task_oldhomework1"
    now = time.time()
    with task_db.write() as conn:
        conn.execute(
            "INSERT INTO tasks (id, owner_user_id, kind, title, status, created_at, updated_at, published_at) "
            "VALUES (?, ?, 'homework', 'Essay', 'published', ?, ?, ?)",
            (homework, ids["tina"], now, now, now),
        )
        conn.execute("INSERT INTO course_items VALUES (?, ?, 1)", (course_id, homework))
        conn.execute("INSERT INTO task_completions VALUES (?, ?, ?, 0, 0)", (homework, ids["sara"], now))
        conn.execute(
            "INSERT INTO task_submissions (task_id, student_id, text, files_json, submitted_at, status) "
            "VALUES (?, ?, 'Waves carry energy.', '[{\"name\": \"a.txt\", \"size\": 1}]', ?, 'submitted')",
            (homework, ids["sara"], now),
        )
    folder = files_root() / homework / ids["sara"]
    folder.mkdir(parents=True)
    (folder / "a.txt").write_bytes(b"x")
    with publish_store.write() as conn:
        conn.execute(
            "INSERT INTO publish_items (kind, item_id, everyone, updated_by, updated_at) VALUES ('task', ?, 0, ?, ?)",
            (homework, ids["tina"], now),
        )
        conn.executemany(
            "INSERT INTO publish_targets VALUES ('task', ?, 'user', ?)",
            [(homework, ids["sara"]), (homework, ids["sam"])],
        )
    with catalog_db.write() as conn:
        conn.execute(
            "INSERT INTO enrollments (kind, item_id, user_id, via, joined_at) VALUES ('task', ?, ?, ?, ?)",
            (homework, ids["sara"], enrollment.MIGRATED, now),
        )
    notifications.notify([ids["tina"]], "homework_submitted", {"title": "Essay"}, link=f"/start?task={homework}")
    notifications.notify([ids["sara"]], "homework_graded", {"title": "Essay"}, link=f"/start?task={homework}")
    return SimpleNamespace(note=note, homework=homework, course=course_id, folder=folder)


def test_old_homework_goes_and_the_rest_stays(as_account, ids, old_data):
    from deeptutor.publishing import scope as scopes
    from deeptutor.tasks.courses import CourseService
    from deeptutor.tasks.service import StudentTasks, remove_homework
    from deeptutor.tasks.store import db as task_db
    from deeptutor.tasks.store import files_root

    note_scope = scopes.get_scope(scopes.TASK, old_data.note)
    note_enrollments = _task_enrollments(old_data.note)
    assert scopes.get_scope(scopes.TASK, old_data.homework) is not None
    assert _task_enrollments(old_data.homework) == 1

    assert remove_homework() == 1

    with task_db.read() as conn:
        assert [tuple(r) for r in conn.execute("SELECT id FROM tasks")] == [(old_data.note,)]
        assert [tuple(r) for r in conn.execute("SELECT course_id, task_id FROM course_items")] == [
            (old_data.course, old_data.note)]
        assert [tuple(r) for r in conn.execute("SELECT task_key, student_id FROM task_completions")] == [
            (old_data.note, ids["sara"])]
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'task_submissions'"
        ).fetchone() is None
    assert not files_root().exists()
    assert scopes.get_scope(scopes.TASK, old_data.homework) is None
    assert _task_enrollments(old_data.homework) == 0
    for name in ("tina", "sara"):
        assert not {n["kind"] for n in notifications.list_for(ids[name])} & set(HOMEWORK_KINDS)
    assert "course_published" in {n["kind"] for n in notifications.list_for(ids["sara"])}

    # The note and its course are untouched.
    assert scopes.get_scope(scopes.TASK, old_data.note).users == note_scope.users
    assert _task_enrollments(old_data.note) == note_enrollments
    with as_account("tina"):
        assert [t["id"] for t in CourseService().get(old_data.course)["tasks"]] == [old_data.note]
    with as_account("sara"):
        [entry] = StudentTasks().assigned()
    assert (entry["key"], entry["state"]) == (old_data.note, "completed")

    # Once done, it does nothing.
    assert remove_homework() == 0
    with task_db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


@pytest.mark.parametrize("step", [
    "deeptutor.catalog.enrollment.forget_item",
    "deeptutor.notifications.service.forget_kinds",
])
def test_a_failed_step_leaves_the_homework_for_the_next_start(as_account, ids, old_data, monkeypatch, step):
    import importlib

    from deeptutor.publishing import scope as scopes
    from deeptutor.tasks.service import StudentTasks, remove_homework
    from deeptutor.tasks.store import db as task_db
    from deeptutor.tasks.store import files_root

    module_name, name = step.rsplit(".", 1)
    module = importlib.import_module(module_name)
    real = getattr(module, name)
    calls: list[int] = []

    def broken_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database is locked")
        return real(*args, **kwargs)

    note_scope = scopes.get_scope(scopes.TASK, old_data.note)
    note_enrollments = _task_enrollments(old_data.note)
    with monkeypatch.context() as patch:
        patch.setattr(module, name, broken_once)
        with pytest.raises(RuntimeError):
            remove_homework()
        with task_db.read() as conn:
            assert conn.execute(
                "SELECT kind FROM tasks WHERE id = ?", (old_data.homework,)
            ).fetchone()[0] == "homework"
            assert conn.execute("SELECT COUNT(*) FROM task_submissions").fetchone()[0] == 1
            assert conn.execute(
                "SELECT COUNT(*) FROM course_items WHERE task_id = ?", (old_data.homework,)
            ).fetchone()[0] == 1
        assert (old_data.folder / "a.txt").exists()
        # The next start, with the store working again, finishes the job.
        assert remove_homework() == 1

    with task_db.read() as conn:
        assert [tuple(r) for r in conn.execute("SELECT id FROM tasks")] == [(old_data.note,)]
        assert [tuple(r) for r in conn.execute("SELECT task_id FROM course_items")] == [(old_data.note,)]
        assert [tuple(r) for r in conn.execute("SELECT task_key FROM task_completions")] == [(old_data.note,)]
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'task_submissions'"
        ).fetchone() is None
    assert not files_root().exists()
    assert scopes.get_scope(scopes.TASK, old_data.homework) is None
    assert _task_enrollments(old_data.homework) == 0
    for user in ("tina", "sara"):
        assert not {n["kind"] for n in notifications.list_for(ids[user])} & set(HOMEWORK_KINDS)
    assert scopes.get_scope(scopes.TASK, old_data.note).users == note_scope.users
    assert _task_enrollments(old_data.note) == note_enrollments
    with as_account("sara"):
        [entry] = StudentTasks().assigned()
    assert (entry["key"], entry["state"]) == (old_data.note, "completed")
    assert remove_homework() == 0


def test_removing_homework_without_any_data_does_nothing(teaching_users):
    from deeptutor.tasks.service import remove_homework
    from deeptutor.tasks.store import db as task_db

    assert remove_homework() == 0
    assert not task_db.path().exists()  # no database made for nothing
    assert remove_homework() == 0


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
        with as_account(name), pytest.raises(TaskError, match="Unknown kind of task"):
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
