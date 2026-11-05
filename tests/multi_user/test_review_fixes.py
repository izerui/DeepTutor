"""Fixes from the whole-range review of batches 0–4 (2026-11-03)."""

from __future__ import annotations

import asyncio
import time

import pytest


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    async def ungraded(pending):
        return {}

    monkeypatch.setattr("deeptutor.practice_sets.service._judge", ungraded)


@pytest.fixture
def ids(teaching_users):
    return {name: row["id"] for name, row in teaching_users.items()}


#: The running test's ``in_course`` fixture: tasks go out as part of a course Sara and Sam joined.
_fixtures: dict[str, object] = {}


@pytest.fixture(autouse=True)
def _remember(in_course):
    _fixtures["in_course"] = in_course


def _in_course(*task_ids, **options):
    return _fixtures["in_course"](*task_ids, **options)


# ── tasks ──────────────────────────────────────────────────


def test_a_practice_set_task_is_done_when_it_was_submitted_not_when_noticed(as_account, ids, monkeypatch):
    from deeptutor.practice_sets.service import PracticeSetService
    from deeptutor.tasks.service import StudentTasks, TaskService
    from deeptutor.tasks.store import db

    with as_account("tina"):
        created = PracticeSetService().create("Week 5")
        PracticeSetService().update(created["id"], questions=[
            {"question": "1+1", "question_type": "choice", "options": {"A": "2", "B": "3"}, "correct_answer": "A"}])
        PracticeSetService().publish(created["id"])
        task = TaskService().create("practice_set", "Do week 5")
        TaskService().update(task["id"], target_id=created["id"])
    _in_course(task["id"])  # the course hands out the set too
    with as_account("sara"):
        qid = PracticeSetService().take(created["id"])["questions"][0]["question_id"]
        asyncio.run(PracticeSetService().submit(created["id"], {qid: "A"}))
    submitted = time.time()
    later = submitted + 3600  # she opens her tasks an hour later
    monkeypatch.setattr("deeptutor.tasks.service._now", lambda: later)
    with as_account("sara"):
        entry = StudentTasks().get(task["id"])
    assert entry["state"] == "completed" and "late" not in entry and "due_at" not in entry
    with db.read() as conn:
        assert conn.execute("SELECT completed_at FROM task_completions WHERE task_key = ?",
                            (task["id"],)).fetchone()[0] <= submitted


# ── courses ────────────────────────────────────────────────


def test_a_teacher_turned_student_keeps_draft_courses(as_account, ids):
    from deeptutor.multi_user.content_lifecycle import hand_over_published
    from deeptutor.tasks.courses import CourseService
    from deeptutor.tasks.store import db

    with as_account("tina"):
        draft = CourseService().create("Next term")
        live = CourseService().create("This term")
        CourseService().add_new_task(live["id"], "note", "Intro")
        CourseService().publish(live["id"])
    hand_over_published(ids["tina"], ids["adam"])
    with db.read() as conn:
        owners = dict(conn.execute("SELECT id, owner_user_id FROM courses").fetchall())
    assert owners == {draft["id"]: ids["tina"], live["id"]: ids["adam"]}


def test_a_failed_course_publication_publishes_none_of_its_tasks(as_account):
    from deeptutor.tasks.courses import CourseService
    from deeptutor.tasks.service import TaskError, TaskService

    with as_account("tina"):
        course = CourseService().create("Waves")
        CourseService().add_new_task(course["id"], "note", "Read first")
        course = CourseService().add_new_task(course["id"], "practice_set", "Then practise")  # no set chosen
        with pytest.raises(TaskError):
            CourseService().publish(course["id"])
        assert {TaskService().get(t["id"])["status"] for t in course["tasks"]} == {"draft"}


# ── permissions ────────────────────────────────────────────


def test_administrators_see_no_practice_answers(as_account, ids, deliver):
    from deeptutor.practice_sets.review import PracticeReview
    from deeptutor.practice_sets.service import PracticeForbidden, PracticeSetService
    from deeptutor.practice_sets import store

    with as_account("tina"):
        created = PracticeSetService().create("Week 6")
        PracticeSetService().update(created["id"], questions=[
            {"question": "Why?", "question_type": "short_answer", "correct_answer": "Because."}])
        PracticeSetService().publish(created["id"])
    deliver("practice_set", created["id"], "sara")
    with as_account("sara"):
        qid = PracticeSetService().take(created["id"])["questions"][0]["question_id"]
        result = asyncio.run(PracticeSetService().submit(created["id"], {qid: "Private thoughts."}))
    with store.write() as conn:  # handed to the administrator when Tina left
        conn.execute("UPDATE practice_sets SET owner_user_id = ?", (ids["adam"],))
    with as_account("adam"), pytest.raises(PracticeForbidden):
        PracticeReview().attempt(created["id"], result["submission_id"])


def test_a_failed_first_publication_keeps_no_receivers(as_account):
    from deeptutor.practice_sets.service import PracticeError, PracticeSetService
    from deeptutor.publishing import scope as scopes

    with as_account("tina"):
        created = PracticeSetService().create("Week 7")
        with pytest.raises(PracticeError):
            PracticeSetService().publish(created["id"])  # no question yet
        assert scopes.get_scope(scopes.PRACTICE_SET, created["id"]) is None


def test_partner_groups_follow_the_partners_switch():
    from deeptutor.multi_user.student_features import feature_for

    assert feature_for("/api/partner-groups", "GET", None) == "allow_partners"
    assert feature_for("/api/partner-groups/g1/messages", "POST", None) == "allow_partners"


def test_the_limit_error_survives_error_mapping():
    from deeptutor.services.llm.error_mapping import map_error
    from deeptutor.services.llm.usage_limit import AiLimitReached

    error = AiLimitReached()
    assert map_error(error, provider="openai") is error
    assert str(error) == "Today's AI usage is used up. Come back tomorrow."
