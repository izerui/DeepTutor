"""Batch 1 of the 2026-11-02 plan: tasks, notifications, the student home.

Spec: docs/superpowers/specs/2026-11-03-batch1-design.md (homework removed:
docs/superpowers/specs/2026-11-05-remove-homework.md)
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from deeptutor.notifications import service as notifications
from deeptutor.practice_sets.service import PracticeSetService
from deeptutor.publishing import scope as scopes
from deeptutor.publishing.scope import Scope
from deeptutor.tasks.service import StudentTasks, TaskError, TaskForbidden, TaskNotFound, TaskService

QUESTIONS = [
    {"question": "2 + 2 = ?", "question_type": "choice", "options": {"A": "3", "B": "4"},
     "correct_answer": "B", "explanation": "Add."},
]


def _kinds(as_account, name: str) -> list[str]:
    with as_account(name):
        return [n["kind"] for n in notifications.list_for(_uid(name))]


_users: dict[str, str] = {}
#: The ``in_course`` / ``deliver`` fixtures of the running test.
_fixtures: dict[str, object] = {}


def _uid(name: str) -> str:
    return _users[name]


@pytest.fixture(autouse=True)
def _remember(teaching_users, in_course, deliver):
    _users.clear()
    _users.update({name: row["id"] for name, row in teaching_users.items()})
    _fixtures.update(in_course=in_course, deliver=deliver)


def _in_course(*task_ids: str, **options) -> str:
    """Tasks go out as part of a course; Sara and Sam joined it."""
    return _fixtures["in_course"](*task_ids, **options)


def _note(as_account, title: str = "Revise") -> str:
    with as_account("tina"):
        note = TaskService().create("note", title)
    _in_course(note["id"], title=title)
    return note["id"]


def _leave_course(name: str, course_id: str) -> None:
    from deeptutor.catalog.enrollment import leave
    from deeptutor.multi_user.models import CurrentUser, UserScope

    leave(CurrentUser(_uid(name), name, "student", UserScope(kind="user", user_id=_uid(name), root=None)),
          "course", course_id)


def _entry(as_account, name: str, key: str):
    with as_account(name):
        return {e["key"]: e for e in StudentTasks().assigned()}.get(key)


# ── rules for publishing a task ────────────────────────────


def test_a_task_never_goes_to_everyone_nor_on_its_own(as_account):
    from deeptutor.tasks.courses import CourseService

    with as_account("tina"):
        task = TaskService().create("note", "Revise")
        # A task goes out with a course, never on its own.
        with pytest.raises(TaskError):
            TaskService().publish(task["id"])
    course_id = _in_course(task["id"], to=(), publish=False)
    with as_account("tina"):
        CourseService().publish(course_id)
    # Nobody is named and nothing reaches everyone: only who joins the course.
    for kind, item_id in ((scopes.TASK, task["id"]), (scopes.COURSE, course_id)):
        scope = scopes.get_scope(kind, item_id)
        assert (scope.everyone, scope.users) == (False, [])


def test_a_task_must_not_reach_students_its_content_does_not(as_account):
    from deeptutor.tasks.courses import CourseService

    with as_account("tina"):
        practice = PracticeSetService()
        created = practice.create("Week 1")
        practice.update(created["id"], questions=QUESTIONS)
        practice.publish(created["id"])
        task = TaskService().create("practice_set", "Do week 1")
        TaskService().update(task["id"], target_id=created["id"])
    # Older data: a draft task whose stored scope names Sara and Sam; the set reaches Sara only.
    scopes._store_scope(scopes.TASK, task["id"], Scope(users=[_uid("sara"), _uid("sam")]), actor_id=_uid("tina"))
    _fixtures["deliver"]("practice_set", created["id"], "sara")
    course_id = _in_course(task["id"], to=(), publish=False)
    with as_account("tina"), pytest.raises(TaskError, match="Some of these students"):
        CourseService().publish(course_id)  # the set does not reach Sam
    _fixtures["deliver"]("practice_set", created["id"], "sam")
    with as_account("tina"):
        CourseService().publish(course_id)
    # The named students of the older scope still receive it.
    assert _entry(as_account, "sam", task["id"])["state"] == "open"


# ── what students see, and when a task counts as done ──────


def test_a_practice_set_task_completes_and_completion_stays(as_account):
    with as_account("tina"):
        practice = PracticeSetService()
        created = practice.create("Week 1")
        practice.update(created["id"], questions=QUESTIONS)
    _fixtures["deliver"]("practice_set", created["id"], "sara")  # she joined it
    with as_account("tina"):
        practice.publish(created["id"])
    assert "content_published" in _kinds(as_account, "sara")
    with as_account("sara"):
        assert StudentTasks().assigned() == []  # a practice set is not a task by itself
    with as_account("tina"):
        task = TaskService().create("practice_set", "Do week 1")
        TaskService().update(task["id"], target_id=created["id"])
    _in_course(task["id"])
    with as_account("sara"):
        keys = [e["key"] for e in StudentTasks().assigned()]
        assert keys == [task["id"]]
        qid = PracticeSetService().take(created["id"])["questions"][0]["question_id"]
        asyncio.run(PracticeSetService().submit(created["id"], {qid: "B"}))
    assert _entry(as_account, "sara", task["id"])["state"] == "completed"
    with as_account("tina"):
        PracticeSetService().archive(created["id"])
    # R24: completed stays completed although the set was withdrawn.
    assert _entry(as_account, "sara", task["id"])["state"] == "completed"


def test_leaving_the_course_closes_a_task_and_coming_back_restores_it(as_account):
    with as_account("tina"):
        task = TaskService().create("note", "Revise")
    task_id = task["id"]
    course_id = _in_course(task_id)
    _leave_course("sara", course_id)
    assert _entry(as_account, "sara", task_id) is None  # nothing done yet: it simply goes
    _fixtures["deliver"]("course", course_id, "sara")
    with as_account("sara"):
        StudentTasks().mark_done(task_id)
    _leave_course("sara", course_id)
    entry = _entry(as_account, "sara", task_id)
    assert entry["state"] == "completed"  # their own record stays, in their history
    with as_account("tina"):
        TaskService().archive(task_id)
        detail = TaskService().detail(task_id)
    assert next(s for s in detail["students"] if s["username"] == "sara")["in_scope"] is False


def test_notes_are_marked_done_by_the_student(as_account):
    note = _note(as_account, "Revise chapter 3")
    # A course announces its tasks once, as the course.
    assert "course_published" in _kinds(as_account, "sara")
    assert _entry(as_account, "sara", note)["state"] == "open"
    with as_account("sara"):
        assert StudentTasks().mark_done(note)["state"] == "completed"
    with as_account("tina"):
        sara = next(s for s in TaskService().detail(note)["students"] if s["username"] == "sara")
    assert sara["completed"] and sara["self_reported"]


def test_book_tasks_complete_when_every_chosen_page_was_opened(as_account, teaching_books, monkeypatch):
    from deeptutor.book.models import Chapter, Progress, Spine

    spine = Spine(book_id=teaching_books.public.book_id, chapters=[
        Chapter(id="c1", title="Light", page_ids=["p1", "p2"]), Chapter(id="c2", title="Lenses", page_ids=["p3"]),
    ])
    monkeypatch.setattr("deeptutor.multi_user.book_access.engine_for",
                        lambda record: SimpleNamespace(load_spine=lambda book_id: spine))
    visited: list[str] = []
    monkeypatch.setattr("deeptutor.multi_user.book_access.resolve_book", lambda ref: SimpleNamespace(
        load_progress=lambda book_id: Progress(book_id=book_id, visited_page_ids=list(visited))))
    # Tom's public book from before the market reaches everyone.
    with as_account("tom"):
        task = TaskService().create("book", "Read about light")
        TaskService().update(task["id"], target_id=teaching_books.public.book_id, settings={"chapter_ids": ["c1"]})
    _in_course(task["id"], owner="tom")
    with as_account("tom"):
        assert TaskService().get(task["id"])["settings"]["page_ids"] == ["p1", "p2"]
    visited.append("p1")
    assert _entry(as_account, "sara", task["id"])["state"] == "open"
    visited.append("p2")
    assert _entry(as_account, "sara", task["id"])["state"] == "completed"


# ── account events ─────────────────────────────────────────


def test_a_departing_teachers_tasks_go_to_the_admin_who_hands_the_course_on(teaching_users, as_account):
    from deeptutor.api.routers import auth as auth_router
    from deeptutor.tasks.courses import CourseService

    with as_account("tina"):
        first = TaskService().create("note", "Read")["id"]
    course_id = _in_course(first)
    note = _note(as_account)
    asyncio.run(auth_router.update_user_role(
        "tina", auth_router.SetRoleRequest(role="student"),
        current=auth_router.TokenPayload(username="adam", role="admin", user_id=_uid("adam")),
    ))
    # Every task goes to the administrator.
    with as_account("adam"):
        assert TaskService().get(first)["kind"] == "note"
        assert TaskService().get(note)["kind"] == "note"
        CourseService().hand_over(course_id, _uid("tom"))
    with as_account("tom"):
        assert TaskService().get(first)["title"] == "Read"
    assert "tasks_transferred" in _kinds(as_account, "tom")


def test_a_deleted_students_completions_and_notifications_go(teaching_users, as_account):
    from deeptutor.multi_user.account_handoff import run_stages

    task_id = _note(as_account)
    with as_account("sara"):
        StudentTasks().mark_done(task_id)
    assert notifications.list_for(_uid("sara"))
    failed, _ = run_stages(_uid("sara"), _uid("adam"), ("task_records", "notifications"))
    assert failed == []
    assert notifications.list_for(_uid("sara")) == []
    with as_account("tina"):
        assert all(s["username"] != "sara" or not s["completed"] for s in TaskService().detail(task_id)["students"])
    # Sam's state is untouched.
    with as_account("sam"):
        StudentTasks().mark_done(task_id)
    with as_account("tina"):
        assert next(s for s in TaskService().detail(task_id)["students"] if s["username"] == "sam")["completed"]


# ── the student home ───────────────────────────────────────


def test_the_home_lists_open_tasks_and_counts_history(as_account):
    from deeptutor.api.routers import home

    open_task = _note(as_account, "Read chapter 1")
    note = _note(as_account)
    with as_account("sara"):
        StudentTasks().mark_done(note)
        payload = asyncio.run(home.home())
    assert [t["key"] for t in payload["assigned"]] == [open_task]
    assert payload["history_count"] == 1
    assert payload["assigned"][0]["provenance"] == {"kind": "teacher", "name": "tina"}
