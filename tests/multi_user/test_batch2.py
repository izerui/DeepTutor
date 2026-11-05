"""Batch 2 of the 2026-11-02 plan: comments, opening times, teacher courses and the calendar.

(Departments, grades and class announcements went with classes.)

Spec: docs/superpowers/specs/2026-11-03-batch2-design.md
"""

from __future__ import annotations

import asyncio
import time

import pytest

from deeptutor.comments import service as comments
from deeptutor.notifications import service as notifications
from deeptutor.practice_sets.service import PracticeSetService
from deeptutor.publishing import scope as scopes
from deeptutor.tasks.courses import CourseService, assigned_courses
from deeptutor.tasks.service import StudentTasks, TaskConflict, TaskError, TaskService

QUESTIONS = [
    {"question": "2 + 2 = ?", "question_type": "choice", "options": {"A": "3", "B": "4"},
     "correct_answer": "B", "explanation": "Add."},
]


@pytest.fixture
def ids(teaching_users):
    return {name: row["id"] for name, row in teaching_users.items()}


def _kinds(user_id: str) -> list[str]:
    return [n["kind"] for n in notifications.list_for(user_id)]


def _entry(as_account, name: str, key: str):
    with as_account(name):
        return {e["key"]: e for e in StudentTasks().assigned()}.get(key)


# ── comments ───────────────────────────────────────────────


def _answered_set(as_account, deliver) -> tuple[str, str]:
    with as_account("tina"):
        service = PracticeSetService()
        created = service.create("Week 1")
        service.update(created["id"], questions=QUESTIONS)
        service.publish(created["id"])
    deliver(scopes.PRACTICE_SET, created["id"], "sara", "sam")
    with as_account("sara"):
        qid = PracticeSetService().take(created["id"])["questions"][0]["question_id"]
        result = asyncio.run(PracticeSetService().submit(created["id"], {qid: "A"}))
    return created["id"], result["submission_id"]


def test_comments_are_between_the_author_and_one_student(ids, as_account, deliver):
    set_id, submission_id = _answered_set(as_account, deliver)
    ref = f"{set_id}:{submission_id}"
    with as_account("tom"), pytest.raises(comments.CommentNotFound):
        comments.write("practice_submission", ref, ids["sara"], "Not mine to say.")
    with as_account("tina"):
        comments.write("practice_submission", ref, ids["sara"], "Check the addition.")
        comments.write("practice_submission", ref, ids["sara"], "Check the addition again.")
    assert _kinds(ids["sara"]).count("teacher_comment") == 1  # an edit does not notify
    with as_account("sara"):
        [mine] = comments.for_student("practice_submission", set_id)
    assert mine["text"] == "Check the addition again." and mine["author"] == "tina"
    with as_account("sam"):
        assert comments.for_student("practice_submission", set_id) == []
    with as_account("tom"), pytest.raises(comments.CommentNotFound):
        comments.get(mine["id"])


def test_path_comments_need_the_paths_author_and_its_student(
    ids, as_account, teacher_draft, publish_template, seed_user
):
    teacher_draft()
    template_id = publish_template(students=("sara",))["template"]["id"]
    with as_account("tina"):
        comments.write("path_point", f"{template_id}:kp1", ids["sara"], "You keep mixing up limits.")
        with pytest.raises(comments.CommentNotFound):
            comments.write("path_point", f"{template_id}:kp1", ids["sam"], "Not assigned to Sam.")
    with as_account("sara"):
        assert [c["text"] for c in comments.for_student("path_point", template_id)] == ["You keep mixing up limits."]


# ── opening times ──────────────────────────────────────────


def test_a_task_shows_only_its_opening_until_it_opens(ids, as_account, monkeypatch, in_course):
    with as_account("tina"):
        task = TaskService().create("note", "Week 2 reading")
        TaskService().update(task["id"], description="Secret until Monday.", open_at=time.time() + 3600)
        with pytest.raises(TaskError):
            TaskService().update(task["id"], due_at=time.time() + 60)  # must open before the deadline
    in_course(task["id"])
    assert "task_published" not in _kinds(ids["sara"])  # announced when it opens
    early = _entry(as_account, "sara", task["id"])
    assert early["state"] == "not_open" and early["description"] == ""
    with as_account("sara"), pytest.raises(TaskConflict):
        StudentTasks().mark_done(task["id"])

    later = time.time() + 7200
    monkeypatch.setattr("deeptutor.tasks.service._now", lambda: later)
    with as_account("sara"):
        StudentTasks().sweep()
        StudentTasks().sweep()
    assert _kinds(ids["sara"]).count("task_opened") == 1
    opened = _entry(as_account, "sara", task["id"])
    assert opened["state"] == "open" and opened["description"] == "Secret until Monday."
    with as_account("tina"):
        with pytest.raises(TaskConflict):  # open now: only earlier
            TaskService().update(task["id"], open_at=later + 3600)
        TaskService().update(task["id"], open_at=time.time())


# ── teacher courses ────────────────────────────────────────


def test_a_course_publishes_its_tasks_once_and_withdrawing_it_keeps_them(ids, as_account, deliver):
    with as_account("tina"):
        courses = CourseService()
        course = courses.create("Waves, term 1")
        course = courses.add_new_task(course["id"], "note", "Read the intro")
        course = courses.add_new_task(course["id"], "note", "Revise")
        first, second = (t["id"] for t in course["tasks"])
        TaskService().update(second, open_at=time.time() + 86400)
    deliver("course", course["id"], "sara", "sam")  # they joined it
    with as_account("tina"):
        courses.publish(course["id"])
        assert all(t["status"] == "published" for t in courses.get(course["id"])["tasks"])
    kinds = _kinds(ids["sara"])
    assert kinds.count("course_published") == 1 and "task_published" not in kinds
    with as_account("sara"):
        [mine] = assigned_courses()
    assert [t["key"] for t in mine["tasks"]] == [first, second]
    assert [t["state"] for t in mine["tasks"]] == ["open", "not_open"]
    with as_account("tina"):
        CourseService().archive(course["id"])
    with as_account("sara"):
        assert assigned_courses() == []
    assert _entry(as_account, "sara", first)["state"] == "open"  # the task stays


# ── the calendar ───────────────────────────────────────────


def test_the_calendar_lists_openings_and_deadlines(ids, as_account, in_course):
    from deeptutor.api.routers import tasks as task_routes

    opens, due = time.time() + 3600, time.time() + 7200
    with as_account("tina"):
        task = TaskService().create("note", "Lab report")
        TaskService().update(task["id"], open_at=opens, due_at=due)
    in_course(task["id"])
    with as_account("tina"):
        teacher = asyncio.run(task_routes.calendar())["events"]
    with as_account("sara"):
        student = asyncio.run(task_routes.calendar())["events"]
    for events in (teacher, student):
        assert [(e["type"], round(e["at"])) for e in events] == [("open", round(opens)), ("due", round(due))]
