"""Batch 4 of the 2026-11-02 plan: learners' averages, hand-over summaries,
own courses on the home page, teacher courses in one's own.

(The withdrawn-material notices went with classes.)

Spec: docs/superpowers/specs/2026-11-03-batch4-design.md
"""

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


#: The running test's ``deliver`` fixture, and who joins what Tina publishes here.
_fixtures: dict[str, object] = {}


@pytest.fixture(autouse=True)
def _remember(deliver):
    _fixtures.update(deliver=deliver, learners=("sara", "sam"))


@pytest.fixture
def third_student(teaching_users, seed_user):
    """A third learner, so an average can be shown."""
    stu = seed_user("sue", role="student")
    teaching_users["sue"] = stu
    _fixtures["learners"] = ("sara", "sam", "sue")
    return stu


def _kinds(user_id):
    return [n["kind"] for n in notifications.list_for(user_id)]


QUESTIONS = [
    {"question": "2 + 2 = ?", "question_type": "choice", "options": {"A": "3", "B": "4"},
     "correct_answer": "B", "explanation": "", "points": 2},
]


def _set(as_account, **settings):
    from deeptutor.practice_sets.service import PracticeSetService

    with as_account("tina"):
        created = PracticeSetService().create("Week 4")
        PracticeSetService().update(created["id"], questions=QUESTIONS, **settings)
        PracticeSetService().publish(created["id"])
    _fixtures["deliver"]("practice_set", created["id"], *_fixtures["learners"])  # they joined it
    return created["id"]


def _answer(as_account, name, set_id, choice):
    from deeptutor.practice_sets.service import PracticeSetService

    with as_account(name):
        qid = PracticeSetService().take(set_id)["questions"][0]["question_id"]
        asyncio.run(PracticeSetService().submit(set_id, {qid: choice}))


def _take(as_account, name, set_id):
    from deeptutor.practice_sets.service import PracticeSetService

    with as_account(name):
        return PracticeSetService().take(set_id)


# ── learners' averages (§1) ────────────────────────────────


def test_a_practice_average_needs_three_scores(as_account, third_student):
    set_id = _set(as_account)
    _answer(as_account, "sara", set_id, "B")
    _answer(as_account, "sam", set_id, "A")
    assert _take(as_account, "sara", set_id)["class_average"] is None  # two scores reveal each other
    _answer(as_account, "sue", set_id, "B")
    assert _take(as_account, "sara", set_id)["class_average"] == round(2 * 2 / 3, 1)


def test_no_average_before_the_answers_show(as_account, third_student):
    set_id = _set(as_account, due_at=time.time() + 3600, reveal="after_due")
    for name in ("sara", "sam", "sue"):
        _answer(as_account, name, set_id, "B")
    assert _take(as_account, "sara", set_id)["class_average"] is None


def test_a_homework_average_shows_beside_a_graded_one(as_account, third_student, teaching_users, in_course):
    from deeptutor.tasks.service import StudentTasks, TaskService

    with as_account("tina"):
        task = TaskService().create("homework", "Essay")
    in_course(task["id"], to=("sara", "sam", "sue"))
    for name in ("sara", "sam", "sue"):
        with as_account(name):
            asyncio.run(StudentTasks().submit(task["id"], "text", []))
    with as_account("tina"):
        TaskService().grade(task["id"], teaching_users["sara"]["id"], 90, "")
        TaskService().grade(task["id"], teaching_users["sam"]["id"], 60, "")
    with as_account("sara"):
        assert StudentTasks().get(task["id"])["submission"]["class_average"] is None
    with as_account("tina"):
        TaskService().grade(task["id"], teaching_users["sue"]["id"], 75, "")
    with as_account("sara"):
        assert StudentTasks().get(task["id"])["submission"]["class_average"] == 75.0
    with as_account("sam"):
        sam_view = StudentTasks().get(task["id"])["submission"]
    assert sam_view["class_average"] == 75.0 and "ranking" not in sam_view


# ── hand-over summaries (§3) ───────────────────────────────


def test_whoever_takes_over_gets_one_summary(as_account, teaching_users):
    from deeptutor.multi_user.content_lifecycle import hand_over_published

    ids = {name: row["id"] for name, row in teaching_users.items()}
    _set(as_account)
    moved = hand_over_published(ids["tina"], ids["adam"])
    assert moved["practice_sets"] == 1
    [notice] = [n for n in notifications.list_for(ids["adam"]) if n["kind"] == "content_transferred"]
    assert notice["params"]["from"] == "tina" and notice["params"]["practice_sets"] == 1
    assert "tasks_transferred" not in _kinds(ids["adam"])  # not a second notice for the same hand-over


def test_a_deleted_teachers_courses_go_even_without_tasks(as_account, teaching_users):
    from deeptutor.tasks.courses import CourseService
    from deeptutor.tasks.service import transfer_tasks
    from deeptutor.tasks.store import db

    ids = {name: row["id"] for name, row in teaching_users.items()}
    with as_account("tina"):
        CourseService().create("Term plan")  # a course holding no task yet
    counts: dict[str, int] = {}
    transfer_tasks(ids["tina"], ids["adam"], counts=counts)  # the deletion hand-over
    assert counts == {"tasks": 0, "courses": 1}
    with db.read() as conn:
        assert conn.execute("SELECT owner_user_id FROM courses").fetchone()[0] == ids["adam"]


# ── own courses (§4, §5) ───────────────────────────────────


def _publish_course(as_account, title="Waves, term 1"):
    from deeptutor.tasks.courses import CourseService

    with as_account("tina"):
        course = CourseService().create(title)
        CourseService().add_new_task(course["id"], "note", "Read the intro")
    _fixtures["deliver"]("course", course["id"], "sara")  # she joined it
    with as_account("tina"):
        CourseService().publish(course["id"])
    return course["id"]


def test_a_student_references_a_teachers_course_until_it_stops_reaching_them(as_account):
    from deeptutor.services.courses import get_course_service
    from deeptutor.services.courses_state import build_course_state, build_course_resource_candidates
    from deeptutor.tasks.courses import CourseService

    course_id = _publish_course(as_account)
    with as_account("sara"):
        candidates = asyncio.run(build_course_resource_candidates())["teacher_course"]
        assert candidates == [{"ref_id": course_id, "label": "Waves, term 1"}]
        own = get_course_service().create(name="My waves")
        get_course_service().attach_resource(own.id, kind="teacher_course", ref_id=course_id, label="Waves, term 1")
        [row] = asyncio.run(build_course_state(own.id))["resources"]
        assert row["available"] and row["detail"]["done"] == 0 and row["detail"]["total"] == 1
    with as_account("tina"):
        CourseService().archive(course_id)
    with as_account("sara"):
        [row] = asyncio.run(build_course_state(own.id))["resources"]
        assert row["available"] is False


def test_only_a_course_sent_to_the_student_can_be_attached(as_account, teaching_users, monkeypatch):
    from fastapi import HTTPException

    from deeptutor.api.routers import courses as course_routes
    from deeptutor.services.courses import get_course_service

    course_id = _publish_course(as_account)
    with as_account("sara"):
        own = get_course_service().create(name="Mine")
        bad = course_routes.AttachCourseResourceRequest(kind="teacher_course", ref_id="crs_nope", label="x")
        with pytest.raises(HTTPException):
            asyncio.run(course_routes.attach_course_resource(own.id, bad))
        good = course_routes.AttachCourseResourceRequest(kind="teacher_course", ref_id=course_id, label="fake")
        made = asyncio.run(course_routes.attach_course_resource(own.id, good))["resource"]
    assert made["label"] == "Waves, term 1"  # the course's own title, not what the client sent


def test_the_home_lists_ones_own_courses(as_account):
    from deeptutor.api.routers.home import _mine_courses
    from deeptutor.services.courses import get_course_service

    with as_account("sara"):
        get_course_service().create(name="Algebra")
        archived = get_course_service().create(name="Old term")
        get_course_service().set_status(archived.id, "archived")
        assert [c["name"] for c in _mine_courses()] == ["Algebra"]
