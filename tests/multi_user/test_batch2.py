"""Batch 2 of the 2026-11-02 plan: departments and grades, comments, announcements,
opening times, teacher courses and the calendar.

Spec: docs/superpowers/specs/2026-11-03-batch2-design.md
"""

from __future__ import annotations

import asyncio
import time

import pytest

from deeptutor.classes import org
from deeptutor.classes.announcements import AnnouncementService
from deeptutor.classes.service import ClassForbidden, ClassService
from deeptutor.comments import service as comments
from deeptutor.notifications import service as notifications
from deeptutor.practice_sets.service import PracticeSetService
from deeptutor.publishing import scope as scopes
from deeptutor.publishing.scope import Scope
from deeptutor.publishing.service import PublishService
from deeptutor.tasks.courses import CourseService, assigned_courses
from deeptutor.tasks.service import StudentTasks, TaskConflict, TaskError, TaskService

QUESTIONS = [
    {"question": "2 + 2 = ?", "question_type": "choice", "options": {"A": "3", "B": "4"},
     "correct_answer": "B", "explanation": "Add."},
]


@pytest.fixture(autouse=True)
def _no_model(monkeypatch):
    async def _quiet(_task_id, _student_id):
        return None

    monkeypatch.setattr("deeptutor.tasks.service.suggest_grade", _quiet)


@pytest.fixture
def ids(teaching_users):
    return {name: row["id"] for name, row in teaching_users.items()}


def _class_id(as_account) -> str:
    with as_account("tina"):
        [row] = ClassService().list_classes()
    return row["id"]


def _kinds(user_id: str) -> list[str]:
    return [n["kind"] for n in notifications.list_for(user_id)]


def _entry(as_account, name: str, key: str):
    with as_account(name):
        return {e["key"]: e for e in StudentTasks().assigned()}.get(key)


# ── departments and grades ─────────────────────────────────


def test_admins_keep_the_lists_and_class_managers_pick_from_them(ids, as_account):
    class_id = _class_id(as_account)
    with as_account("tina"), pytest.raises(org.OrgForbidden):
        org.create("department", "Physics")
    with as_account("adam"):
        physics = org.create("department", "Physics")
        year = org.create("grade", "2027")
        with pytest.raises(org.OrgError):
            org.create("department", "Physics")  # names are unique
    with as_account("tina"):
        detail = ClassService().update(class_id, department_id=physics["id"], grade_id=year["id"])
        assert (detail["department_id"], detail["grade_id"]) == (physics["id"], year["id"])
        with pytest.raises(org.OrgError):
            ClassService().update(class_id, department_id="dep_missing")
    with as_account("sara"), pytest.raises(ClassForbidden):
        ClassService().update(class_id, grade_id="")
    with as_account("adam"):
        org.delete("department", physics["id"])
    with as_account("tina"):
        assert ClassService().get(class_id)["department_id"] == ""


def test_only_admins_publish_to_departments_and_grades_and_later_classes_join(
    ids, as_account, teaching_books, seed_user
):
    class_id = _class_id(as_account)
    with as_account("adam"):
        physics = org.create("department", "Physics")
        year = org.create("grade", "2027")
    group = f"department:{physics['id']}+grade:{year['id']}"
    book_id = teaching_books.public.book_id
    with as_account("tom"), pytest.raises(scopes.ScopeForbidden):
        PublishService().set_scope(scopes.BOOK, book_id, everyone=False, users=[], classes=[],
                                   groups=[group], confirm=True)

    from deeptutor.knowledge import registry
    from deeptutor.multi_user.context import get_current_user

    kb = registry.register_kb(base_dir=teaching_books.public.base_dir + "-kbs", name="handbook",
                              kind="uploaded", created_by=ids["adam"])
    with as_account("adam"):
        with scopes.chosen_scope(Scope(groups=[group])):
            registry.set_visibility(kb.kb_id, "public")
    with as_account("sara"):
        assert not registry.can_read(registry.get_kb(kb.kb_id), get_current_user())
    with as_account("tina"):  # the class joins the department's 2027 grade
        ClassService().update(class_id, department_id=physics["id"], grade_id=year["id"])
    with as_account("sara"):
        assert registry.can_read(registry.get_kb(kb.kb_id), get_current_user())
    with as_account("adam"):
        described = PublishService().describe(scopes.KNOWLEDGE_BASE, kb.kb_id)
    assert described["scope"]["groups"] == [{"id": group, "name": "Physics · 2027"}]
    assert described["audience_size"] == 4  # tina, tom, sara, sam


def test_teachers_are_listed_under_departments_for_finding_only(ids, as_account):
    with as_account("adam"):
        physics = org.create("department", "Physics")
        org.set_teacher_departments(ids["tina"], [physics["id"]])
    assert org.teacher_departments() == {ids["tina"]: [physics["id"]]}
    with as_account("tina"), pytest.raises(org.OrgForbidden):
        org.set_teacher_departments(ids["tom"], [physics["id"]])


# ── comments ───────────────────────────────────────────────


def _answered_set(as_account) -> tuple[str, str]:
    with as_account("tina"):
        service = PracticeSetService()
        created = service.create("Week 1")
        service.update(created["id"], questions=QUESTIONS)
        service.publish(created["id"])
    with as_account("sara"):
        qid = PracticeSetService().take(created["id"])["questions"][0]["question_id"]
        result = asyncio.run(PracticeSetService().submit(created["id"], {qid: "A"}))
    return created["id"], result["submission_id"]


def test_comments_are_between_the_author_and_one_student(ids, as_account):
    set_id, submission_id = _answered_set(as_account)
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


# ── announcements ──────────────────────────────────────────


def test_announcements_reach_the_class_and_follow_its_lifecycle(ids, as_account):
    class_id = _class_id(as_account)
    with as_account("sara"), pytest.raises(ClassForbidden):
        AnnouncementService().post(class_id, "I am not a teacher.")
    with as_account("tina"):
        first = AnnouncementService().post(class_id, "Exam on Friday.")
        AnnouncementService().post(class_id, "Bring a calculator.", pinned=True)
    assert "class_announcement" in _kinds(ids["sara"])
    with as_account("sara"):
        assert [a["text"] for a in AnnouncementService().for_home()] == ["Bring a calculator.", "Exam on Friday."]
    with as_account("tom"), pytest.raises(ClassForbidden):
        AnnouncementService().update(first["id"], text="Tom edits Tina's")
    with as_account("tina"):
        ClassService().update(class_id, archived=True)
        with pytest.raises(ClassForbidden):  # read-only for teachers once archived
            AnnouncementService().update(first["id"], text="Too late")
    with as_account("sara"):
        assert AnnouncementService().for_home() == []
    with as_account("adam"):
        AnnouncementService().delete(first["id"])


# ── opening times ──────────────────────────────────────────


def test_a_task_shows_only_its_opening_until_it_opens(ids, as_account, monkeypatch):
    with as_account("tina"):
        task = TaskService().create("note", "Week 2 reading")
        TaskService().update(task["id"], description="Secret until Monday.", open_at=time.time() + 3600)
        with pytest.raises(TaskError):
            TaskService().update(task["id"], due_at=time.time() + 60)  # must open before the deadline
        TaskService().publish(task["id"])
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


def test_a_course_publishes_its_tasks_once_and_withdrawing_it_keeps_them(ids, as_account):
    with as_account("tina"):
        courses = CourseService()
        course = courses.create("Waves, term 1")
        course = courses.add_new_task(course["id"], "note", "Read the intro")
        course = courses.add_new_task(course["id"], "note", "Revise")
        first, second = (t["id"] for t in course["tasks"])
        TaskService().update(second, open_at=time.time() + 86400)
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


def test_a_course_cannot_reach_students_its_published_tasks_do_not(ids, as_account):
    with as_account("tina"):
        task = TaskService().create("note", "Only for Sara")
        with scopes.chosen_scope(Scope(users=[ids["sara"]])):
            TaskService().publish(task["id"])
        course = CourseService().create("Whole class")
        CourseService().update(course["id"], task_ids=[task["id"]])
        with pytest.raises(TaskError, match="does not reach"):
            CourseService().publish(course["id"])
        assert scopes.get_scope(scopes.COURSE, course["id"]) is None


# ── the calendar ───────────────────────────────────────────


def test_the_calendar_lists_openings_and_deadlines(ids, as_account):
    from deeptutor.api.routers import tasks as task_routes

    opens, due = time.time() + 3600, time.time() + 7200
    with as_account("tina"):
        task = TaskService().create("note", "Lab report")
        TaskService().update(task["id"], open_at=opens, due_at=due)
        TaskService().publish(task["id"])
        teacher = asyncio.run(task_routes.calendar())["events"]
    with as_account("sara"):
        student = asyncio.run(task_routes.calendar())["events"]
    for events in (teacher, student):
        assert [(e["type"], round(e["at"])) for e in events] == [("open", round(opens)), ("due", round(due))]


def test_teacher_department_routes_are_not_taken_for_a_kind():
    from deeptutor.api.routers.classes import router

    routes = [(route.path, sorted(route.methods)) for route in router.routes]
    assert routes.index(("/org/teachers/{user_id}", ["PUT"])) < routes.index(("/org/{kind}/{item_id}", ["PUT"]))
