"""Batch 1 of the 2026-11-02 plan: tasks, homework, notifications, the student home.

Spec: docs/superpowers/specs/2026-11-03-batch1-design.md
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from deeptutor.classes.service import ClassService
from deeptutor.notifications import service as notifications
from deeptutor.practice_sets.service import PracticeConflict, PracticeSetService
from deeptutor.publishing import scope as scopes
from deeptutor.publishing.scope import Scope
from deeptutor.tasks.service import StudentTasks, TaskError, TaskForbidden, TaskNotFound, TaskService
from deeptutor.tasks.service import suggest_grade as real_suggest_grade

QUESTIONS = [
    {"question": "2 + 2 = ?", "question_type": "choice", "options": {"A": "3", "B": "4"},
     "correct_answer": "B", "explanation": "Add."},
]


@pytest.fixture(autouse=True)
def _no_model(monkeypatch):
    """Homework suggestions never reach a real model; tests that want one set it."""

    async def _quiet(_task_id, _student_id):
        return None

    monkeypatch.setattr("deeptutor.tasks.service.suggest_grade", _quiet)


def _class_id(as_account) -> str:
    with as_account("tina"):
        [row] = ClassService().list_classes()
    return row["id"]


def _kinds(as_account, name: str) -> list[str]:
    with as_account(name):
        return [n["kind"] for n in notifications.list_for(_uid(name))]


_users: dict[str, str] = {}


def _uid(name: str) -> str:
    return _users[name]


@pytest.fixture(autouse=True)
def _remember(teaching_users):
    _users.clear()
    _users.update({name: row["id"] for name, row in teaching_users.items()})


def _homework(as_account, *, due_at: float | None = None, scope: Scope | None = None) -> str:
    with as_account("tina"):
        service = TaskService()
        task = service.create("homework", "Essay")
        service.update(task["id"], description="Write about waves.", due_at=due_at)
        with scopes.chosen_scope(scope):
            service.publish(task["id"])
    return task["id"]


def _entry(as_account, name: str, key: str):
    with as_account(name):
        return {e["key"]: e for e in StudentTasks().assigned()}.get(key)


def _submit(as_account, name: str, task_id: str, text: str = "Waves carry energy.", files=()):
    with as_account(name):
        return asyncio.run(StudentTasks().submit(task_id, text, list(files)))


# ── the homework round trip ────────────────────────────────


def test_homework_goes_out_comes_back_and_is_graded(as_account):
    task_id = _homework(as_account)
    assert "task_published" in _kinds(as_account, "sara")
    assert _entry(as_account, "sara", task_id)["state"] == "open"

    done = _submit(as_account, "sara", task_id, files=[("notes.txt", b"draft")])
    assert done["state"] == "completed" and done["submission"]["status"] == "submitted"
    _submit(as_account, "sam", task_id)
    with as_account("tina"):
        [ping] = [n for n in notifications.list_for(_uid("tina")) if n["kind"] == "homework_submitted"]
        assert ping["count"] == 2  # summed per task and day
        detail = TaskService().detail(task_id)
    sara = next(s for s in detail["students"] if s["username"] == "sara")
    assert sara["submission"]["files"] == [{"name": "notes.txt", "size": 5}]

    with as_account("tina"):
        TaskService().send_back(task_id, _uid("sara"), "Add an example.")
    entry = _entry(as_account, "sara", task_id)
    assert entry["state"] == "open" and entry["submission"]["return_reason"] == "Add an example."
    assert "homework_returned" in _kinds(as_account, "sara")

    _submit(as_account, "sara", task_id, text="Waves carry energy, like sound.")
    with as_account("tina"):
        TaskService().grade(task_id, _uid("sara"), 92, "Good.")
    entry = _entry(as_account, "sara", task_id)
    assert entry["state"] == "completed"
    assert (entry["submission"]["score"], entry["submission"]["comment"]) == (92, "Good.")
    assert entry["submission"]["attempt"] == 2
    assert "ai_score" not in entry["submission"]  # the suggestion is the teacher's only
    assert "homework_graded" in _kinds(as_account, "sara")
    with as_account("sara"), pytest.raises(Exception):
        asyncio.run(StudentTasks().submit(task_id, "again", []))


def test_the_ai_suggestion_reaches_the_teacher_only(as_account, monkeypatch):
    monkeypatch.setattr("deeptutor.tasks.service.suggest_grade", real_suggest_grade)  # with a stub model

    async def fake_complete(prompt: str, system_prompt: str):
        assert "Waves carry energy" in prompt
        return '{"score": 88, "reason": "Clear, one example missing."}'

    monkeypatch.setattr("deeptutor.services.llm.complete", fake_complete)
    task_id = _homework(as_account)
    _submit(as_account, "sara", task_id)
    with as_account("tina"):
        sara = next(s for s in TaskService().detail(task_id)["students"] if s["username"] == "sara")
    assert (sara["submission"]["ai_score"], sara["submission"]["ai_reason"]) == (88, "Clear, one example missing.")


def test_homework_files_open_for_their_student_and_teacher_only(as_account):
    task_id = _homework(as_account)
    _submit(as_account, "sara", task_id, files=[("photo.png", b"\x89PNG")])
    for name, allowed in (("sara", True), ("tina", True), ("sam", False), ("tom", False)):
        with as_account(name):
            if allowed:
                assert TaskService().file_path(task_id, _uid("sara"), "photo.png").read_bytes() == b"\x89PNG"
            else:
                with pytest.raises(TaskNotFound):
                    TaskService().file_path(task_id, _uid("sara"), "photo.png")
    with as_account("sara"), pytest.raises(TaskError):
        asyncio.run(StudentTasks().submit(task_id, "x", [("run.exe", b"MZ")]))


# ── rules for publishing a task ────────────────────────────


def test_homework_never_goes_to_everyone_and_admins_assign_none(as_account):
    with as_account("tina"):
        task = TaskService().create("homework", "Essay")
        with scopes.chosen_scope(Scope(everyone=True)), pytest.raises(TaskError):
            TaskService().publish(task["id"])
        assert scopes.get_scope(scopes.TASK, task["id"]) is None
    with as_account("adam"), pytest.raises(TaskForbidden):
        TaskService().create("homework", "Admin essay")


def test_a_task_must_not_reach_students_its_content_does_not(as_account):
    with as_account("tina"):
        practice = PracticeSetService()
        created = practice.create("Week 1")
        practice.update(created["id"], questions=QUESTIONS)
        with scopes.chosen_scope(Scope(users=[_uid("sara")])):
            practice.publish(created["id"])
        task = TaskService().create("practice_set", "Do week 1")
        TaskService().update(task["id"], target_id=created["id"])
        with pytest.raises(TaskError, match="Some of these students"):
            TaskService().publish(task["id"])  # the class has Sam too
        assert scopes.get_scope(scopes.TASK, task["id"]) is None
        with scopes.chosen_scope(Scope(users=[_uid("sara")])):
            TaskService().publish(task["id"])


# ── what students see, and when a task counts as done ──────


def test_a_practice_set_shows_once_and_completion_stays(as_account):
    with as_account("tina"):
        practice = PracticeSetService()
        created = practice.create("Week 1")
        practice.update(created["id"], questions=QUESTIONS)
        practice.publish(created["id"])
    virtual = f"practice_set:{created['id']}"
    assert _entry(as_account, "sara", virtual)["state"] == "open"
    assert "content_published" in _kinds(as_account, "sara")
    with as_account("tina"):
        task = TaskService().create("practice_set", "Do week 1")
        TaskService().update(task["id"], target_id=created["id"], due_at=time.time() + 3600)
        TaskService().publish(task["id"])
    with as_account("sara"):
        keys = [e["key"] for e in StudentTasks().assigned()]
        assert task["id"] in keys and virtual not in keys  # R23: named by a task, listed once
        qid = PracticeSetService().take(created["id"])["questions"][0]["question_id"]
        asyncio.run(PracticeSetService().submit(created["id"], {qid: "B"}))
    assert _entry(as_account, "sara", task["id"])["state"] == "completed"
    with as_account("tina"):
        PracticeSetService().archive(created["id"])
    # R24: completed stays completed although the set was withdrawn.
    assert _entry(as_account, "sara", task["id"])["state"] == "completed"


def test_leaving_the_class_closes_a_task_and_coming_back_restores_it(as_account):
    task_id = _homework(as_account)
    class_id = _class_id(as_account)
    with as_account("sara"):
        ClassService().leave(class_id)
    assert _entry(as_account, "sara", task_id) is None  # nothing done yet: it simply goes
    with as_account("adam"):
        ClassService().add_students(class_id, [_uid("sara")])
    _submit(as_account, "sara", task_id)
    with as_account("sara"):
        ClassService().leave(class_id)
    entry = _entry(as_account, "sara", task_id)
    assert entry["state"] == "completed"  # their own record stays, in their history
    with as_account("tina"):
        TaskService().archive(task_id)
        detail = TaskService().detail(task_id)
    assert next(s for s in detail["students"] if s["username"] == "sara")["in_scope"] is False


def test_notes_are_marked_done_by_the_student(as_account):
    with as_account("tina"):
        task = TaskService().create("note", "Revise chapter 3")
        TaskService().publish(task["id"])
    with as_account("sara"):
        assert StudentTasks().mark_done(task["id"])["state"] == "completed"
    with as_account("tina"):
        sara = next(s for s in TaskService().detail(task["id"])["students"] if s["username"] == "sara")
    assert sara["completed"] and sara["self_reported"]


def test_book_tasks_complete_when_every_chosen_page_was_opened(as_account, teaching_books, monkeypatch):
    from deeptutor.book.models import Chapter, Progress, Spine
    from deeptutor.publishing.service import PublishService

    spine = Spine(book_id=teaching_books.public.book_id, chapters=[
        Chapter(id="c1", title="Light", page_ids=["p1", "p2"]), Chapter(id="c2", title="Lenses", page_ids=["p3"]),
    ])
    monkeypatch.setattr("deeptutor.multi_user.book_access.engine_for",
                        lambda record: SimpleNamespace(load_spine=lambda book_id: spine))
    visited: list[str] = []
    monkeypatch.setattr("deeptutor.multi_user.book_access.resolve_book", lambda ref: SimpleNamespace(
        load_progress=lambda book_id: Progress(book_id=book_id, visited_page_ids=list(visited))))
    with as_account("tom"):
        PublishService().set_scope(scopes.BOOK, teaching_books.public.book_id, everyone=True,
                                   users=[], classes=[], confirm=True)
        task = TaskService().create("book", "Read about light")
        TaskService().update(task["id"], target_id=teaching_books.public.book_id, settings={"chapter_ids": ["c1"]})
        TaskService().publish(task["id"])
        assert TaskService().get(task["id"])["settings"]["page_ids"] == ["p1", "p2"]
    visited.append("p1")
    assert _entry(as_account, "sara", task["id"])["state"] == "open"
    visited.append("p2")
    assert _entry(as_account, "sara", task["id"])["state"] == "completed"


# ── deadlines ──────────────────────────────────────────────


def test_deadlines_mark_overdue_remind_once_and_announce_changes(as_account):
    task_id = _homework(as_account, due_at=time.time() + 3600)
    for _ in range(2):
        assert _entry(as_account, "sara", task_id)["state"] == "open"
    assert _kinds(as_account, "sara").count("task_due_soon") == 1
    with as_account("tina"):
        TaskService().update(task_id, due_at=time.time() - 60)
    assert _entry(as_account, "sara", task_id)["state"] == "overdue"
    assert "task_due_changed" in _kinds(as_account, "sara")
    done = _submit(as_account, "sara", task_id)
    assert done["state"] == "completed" and done["late"]


def test_answers_shown_after_the_deadline_pin_it(as_account):
    with as_account("tina"):
        practice = PracticeSetService()
        created = practice.create("Quiz")
        practice.update(created["id"], questions=QUESTIONS, reveal="after_due", due_at=time.time() + 60)
        practice.publish(created["id"])
        practice.update(created["id"], due_at=time.time() - 1)
        with pytest.raises(PracticeConflict):  # R10
            practice.update(created["id"], due_at=time.time() + 3600)
        practice.update(created["id"], due_at=time.time() - 120)  # earlier is fine


# ── account events ─────────────────────────────────────────


def test_a_departing_teachers_homework_goes_to_a_co_teacher(teaching_users, as_account):
    from deeptutor.api.routers import auth as auth_router

    homework = _homework(as_account)
    with as_account("tina"):
        note = TaskService().create("note", "Revise")
        TaskService().publish(note["id"])
    asyncio.run(auth_router.update_user_role(
        "tina", auth_router.SetRoleRequest(role="student"),
        current=auth_router.TokenPayload(username="adam", role="admin", user_id=_uid("adam")),
    ))
    with as_account("tom"):
        assert TaskService().get(homework)["kind"] == "homework"  # Tom teaches the class too
    with as_account("adam"):
        assert TaskService().get(note["id"])["kind"] == "note"
    assert "tasks_transferred" in _kinds(as_account, "tom")


def test_a_deleted_students_homework_and_notifications_go(teaching_users, as_account):
    from deeptutor.multi_user.account_handoff import run_stages
    from deeptutor.tasks.store import files_root

    task_id = _homework(as_account)
    _submit(as_account, "sara", task_id, files=[("a.txt", b"x")])
    assert (files_root() / task_id / _uid("sara")).exists()
    failed, _ = run_stages(_uid("sara"), _uid("adam"), ("task_records", "notifications"))
    assert failed == []
    assert not (files_root() / task_id / _uid("sara")).exists()
    assert notifications.list_for(_uid("sara")) == []
    with as_account("tina"):
        assert all(s["username"] != "sara" or s["submission"] is None for s in TaskService().detail(task_id)["students"])


def test_teachers_are_told_of_class_invitations_they_send(teaching_users, as_account, seed_user):
    stu = seed_user("stu", role="student")["id"]
    with as_account("tina"):
        ClassService().invite(_class_id(as_account), stu)
    [note] = notifications.list_for(stu)
    assert note["kind"] == "class_invitation" and note["params"]["class_name"] == "Teaching fixtures"
    notifications.mark_read(stu)
    assert notifications.unread_count(stu) == 0


# ── the student home ───────────────────────────────────────


def test_the_home_lists_open_tasks_and_counts_history(as_account):
    from deeptutor.api.routers import home

    open_task = _homework(as_account)
    with as_account("tina"):
        note = TaskService().create("note", "Revise")
        TaskService().publish(note["id"])
    with as_account("sara"):
        StudentTasks().mark_done(note["id"])
        payload = asyncio.run(home.home())
    assert [t["key"] for t in payload["assigned"]] == [open_task]
    assert payload["history_count"] == 1
    assert payload["assigned"][0]["provenance"] == {"kind": "teacher", "name": "tina"}
