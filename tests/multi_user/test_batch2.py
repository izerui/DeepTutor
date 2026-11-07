"""Batch 2 of the 2026-11-02 plan: author courses.

(Departments, grades and class announcements went with classes; comments,
opening times and the calendar went with author management, spec
2026-11-05-remove-teacher-management.)

Spec: docs/superpowers/specs/2026-11-03-batch2-design.md
"""

from __future__ import annotations

import pytest

from deeptutor.notifications import service as notifications
from deeptutor.tasks.courses import CourseService, assigned_courses
from deeptutor.tasks.service import LearnerTasks


@pytest.fixture
def ids(teaching_users):
    return {name: row["id"] for name, row in teaching_users.items()}


def _kinds(user_id: str) -> list[str]:
    return [n["kind"] for n in notifications.list_for(user_id)]


def _entry(as_account, name: str, key: str):
    with as_account(name):
        return {e["key"]: e for e in LearnerTasks().assigned()}.get(key)


# ── author courses ────────────────────────────────────────


def test_a_course_publishes_its_tasks_once_and_withdrawing_it_keeps_them(ids, as_account, deliver):
    with as_account("tina"):
        courses = CourseService()
        course = courses.create("Waves, term 1")
        course = courses.add_new_task(course["id"], "note", "Read the intro")
        course = courses.add_new_task(course["id"], "note", "Revise")
        first, second = (t["id"] for t in course["tasks"])
    deliver("course", course["id"], "sara", "sam")  # they joined it
    with as_account("tina"):
        courses.publish(course["id"])
        assert all(t["status"] == "published" for t in courses.get(course["id"])["tasks"])
    kinds = _kinds(ids["sara"])
    # Audit fix B §6: the first publication announces nothing (nobody can have joined yet).
    assert "course_published" not in kinds and "task_published" not in kinds
    with as_account("sara"):
        [mine] = assigned_courses()
    assert [t["key"] for t in mine["tasks"]] == [first, second]
    assert [t["state"] for t in mine["tasks"]] == ["open", "open"]
    with as_account("tina"):
        CourseService().archive(course["id"])
    with as_account("sara"):
        # Audit fix B §2: the course stays among what she is learning, marked withdrawn.
        [withdrawn] = assigned_courses()
    assert withdrawn["id"] == course["id"] and withdrawn["unavailable"] == "archived"
    assert "content_withdrawn" in _kinds(ids["sara"])
    assert _entry(as_account, "sara", first)["state"] == "open"  # the task stays
