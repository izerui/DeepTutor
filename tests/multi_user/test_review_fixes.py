"""Fixes from the whole-range review of batches 0–4 (2026-11-03)."""

from __future__ import annotations

import asyncio
import time

import pytest

from deeptutor.notifications import service as notifications


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    async def ungraded(pending):
        return {}

    async def no_suggestion(_task_id, _student_id):
        return None

    monkeypatch.setattr("deeptutor.practice_sets.service._judge", ungraded)
    monkeypatch.setattr("deeptutor.tasks.service.suggest_grade", no_suggestion)


@pytest.fixture
def ids(teaching_users):
    return {name: row["id"] for name, row in teaching_users.items()}


def _homework(as_account, ids, owner="tina"):
    from deeptutor.tasks.service import StudentTasks, TaskService

    with as_account(owner):
        task = TaskService().create("homework", "Essay")
        TaskService().publish(task["id"])
    with as_account("sara"):
        asyncio.run(StudentTasks().submit(task["id"], "Waves carry energy.", []))
    return task["id"]


# ── tasks ──────────────────────────────────────────────────


def test_an_administrator_who_took_over_homework_cannot_read_or_grade_it(as_account, ids):
    from deeptutor.tasks.service import TaskForbidden, TaskService

    task_id = _homework(as_account, ids)
    from deeptutor.tasks.store import db

    # As if no co-teacher was left: the homework task went to the administrator.

    with db.write() as conn:
        conn.execute("UPDATE tasks SET owner_user_id = ? WHERE id = ?", (ids["adam"], task_id))
    with as_account("adam"):
        row = next(s for s in TaskService().detail(task_id)["students"] if s["username"] == "sara")
        assert row["submission"] is None  # handed in, but not shown
        with pytest.raises(TaskForbidden):
            TaskService().grade(task_id, ids["sara"], 90, "")
        with pytest.raises(TaskForbidden):
            TaskService().send_back(task_id, ids["sara"], "redo")
        TaskService().archive(task_id)  # withdrawing is what they may do


def test_grading_a_returned_hand_in_counts_it_again(as_account, ids):
    from deeptutor.tasks.service import StudentTasks, TaskService

    task_id = _homework(as_account, ids)
    with as_account("tina"):
        TaskService().send_back(task_id, ids["sara"], "Add a diagram.")
        TaskService().grade(task_id, ids["sara"], 70, "Fine after all.")
    with as_account("sara"):
        assert StudentTasks().get(task_id)["state"] == "completed"


def test_a_task_that_is_open_cannot_be_closed_again_by_an_opening_time(as_account):
    from deeptutor.tasks.service import TaskConflict, TaskService

    with as_account("tina"):
        task = TaskService().create("note", "Read")
        TaskService().publish(task["id"])  # open at once
        with pytest.raises(TaskConflict):
            TaskService().update(task["id"], open_at=time.time() + 3600)
        TaskService().update(task["id"], open_at=None)  # clearing keeps it open: allowed


def test_a_task_published_after_its_opening_is_announced_once(as_account, ids):
    from deeptutor.tasks.service import StudentTasks, TaskService

    with as_account("tina"):
        task = TaskService().create("note", "Read")
        TaskService().update(task["id"], open_at=time.time() - 60)
        TaskService().publish(task["id"])
    with as_account("sara"):
        StudentTasks().sweep()
        StudentTasks().assigned()
    kinds = [n["kind"] for n in notifications.list_for(ids["sara"])]
    assert kinds.count("task_published") == 1 and "task_opened" not in kinds


def test_a_practice_set_task_is_done_when_it_was_submitted_not_when_noticed(as_account, ids, monkeypatch):
    from deeptutor.practice_sets.service import PracticeSetService
    from deeptutor.tasks.service import StudentTasks, TaskService
    from deeptutor.tasks.store import db

    due = time.time() + 600
    with as_account("tina"):
        created = PracticeSetService().create("Week 5")
        PracticeSetService().update(created["id"], questions=[
            {"question": "1+1", "question_type": "choice", "options": {"A": "2", "B": "3"}, "correct_answer": "A"}])
        PracticeSetService().publish(created["id"])
        task = TaskService().create("practice_set", "Do week 5")
        TaskService().update(task["id"], target_id=created["id"], due_at=due)
        TaskService().publish(task["id"])
    with as_account("sara"):
        qid = PracticeSetService().take(created["id"])["questions"][0]["question_id"]
        asyncio.run(PracticeSetService().submit(created["id"], {qid: "A"}))
    later = due + 3600  # she opens her tasks only after the deadline
    monkeypatch.setattr("deeptutor.tasks.service._now", lambda: later)
    with as_account("sara"):
        assert StudentTasks().get(task["id"])["late"] is False
    with db.read() as conn:
        assert conn.execute("SELECT completed_at FROM task_completions WHERE task_key = ?",
                            (task["id"],)).fetchone()[0] < due


def test_needing_attention_reads_each_students_progress_as_that_student(as_account, ids, monkeypatch):
    from deeptutor.classes import attention
    from deeptutor.multi_user.context import get_current_user
    from deeptutor.tasks.service import StudentTasks

    seen = []
    monkeypatch.setattr(StudentTasks, "assigned", lambda self: seen.append(get_current_user().id) or [])
    attention.clear_cache()
    with as_account("tina"):
        attention.for_teacher(ids["tina"])
        assert get_current_user().id == ids["tina"]  # restored afterwards
    assert sorted(seen) == sorted([ids["sara"], ids["sam"]])
    attention.clear_cache()


# ── courses ────────────────────────────────────────────────


def test_a_teacher_turned_student_keeps_draft_courses(as_account, ids):
    from deeptutor.classes.lifecycle import hand_over_published
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


def test_a_published_task_added_to_a_published_course_must_reach_its_students(as_account, ids):
    from deeptutor.publishing import scope as scopes
    from deeptutor.tasks.courses import CourseService
    from deeptutor.tasks.service import TaskError, TaskService

    with as_account("tina"):
        course = CourseService().create("Waves")
        course = CourseService().add_new_task(course["id"], "note", "Intro")
        CourseService().publish(course["id"])
        narrow = TaskService().create("note", "Only Sara")
        with scopes.chosen_scope(scopes.Scope(users=[ids["sara"]])):
            TaskService().publish(narrow["id"])
        with pytest.raises(TaskError):
            CourseService().update(course["id"], task_ids=[course["tasks"][0]["id"], narrow["id"]])
        assert [t["id"] for t in CourseService().get(course["id"])["tasks"]] == [course["tasks"][0]["id"]]


# ── permissions ────────────────────────────────────────────


def test_administrators_see_no_practice_answers(as_account, ids):
    from deeptutor.practice_sets.review import PracticeReview
    from deeptutor.practice_sets.service import PracticeForbidden, PracticeSetService
    from deeptutor.practice_sets import store

    with as_account("tina"):
        created = PracticeSetService().create("Week 6")
        PracticeSetService().update(created["id"], questions=[
            {"question": "Why?", "question_type": "short_answer", "correct_answer": "Because."}])
        PracticeSetService().publish(created["id"])
    with as_account("sara"):
        qid = PracticeSetService().take(created["id"])["questions"][0]["question_id"]
        result = asyncio.run(PracticeSetService().submit(created["id"], {qid: "Private thoughts."}))
    with store.write() as conn:  # handed to the administrator when Tina left
        conn.execute("UPDATE practice_sets SET owner_user_id = ?", (ids["adam"],))
    with as_account("adam"), pytest.raises(PracticeForbidden):
        PracticeReview().attempt(created["id"], result["submission_id"])


def test_a_failed_first_publication_keeps_no_receivers(as_account, ids):
    from deeptutor.practice_sets.service import PracticeError, PracticeSetService
    from deeptutor.publishing import scope as scopes

    with as_account("tina"):
        created = PracticeSetService().create("Week 7")
        PracticeSetService().update(created["id"], questions=[
            {"question": "1+1", "question_type": "choice", "options": {"A": "2", "B": "3"}, "correct_answer": "A"}],
            reveal="after_due")
        with scopes.chosen_scope(scopes.Scope(users=[ids["sam"]])), pytest.raises(PracticeError):
            PracticeSetService().publish(created["id"])  # after the deadline, but no deadline
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
