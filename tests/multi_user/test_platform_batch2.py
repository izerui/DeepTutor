"""Platform batch 2: classes retire.

Spec: docs/superpowers/specs/2026-11-03-platform-batch2-design.md

The fixture class holds Sara and Sam, taught by Tina and Tom. Each test sets
up what was sent through classes, then retires classes.
"""

from __future__ import annotations

import asyncio

import pytest

QUESTIONS = [
    {"question": "2 + 2 = ?", "question_type": "choice", "options": {"A": "3", "B": "4"},
     "correct_answer": "B", "explanation": "", "points": 2},
]
META = dict(title="Arithmetic", summary="Add.", stages=["primary"], subjects=["primary.math"],
            difficulty="beginner", tags=[])


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    async def ungraded(pending):
        return {}

    monkeypatch.setattr("deeptutor.practice_sets.service._judge", ungraded)


def _retire():
    from deeptutor.catalog import migration, retire_classes

    migration.run()  # "everyone" goes to the market first (review H2)
    assert retire_classes.run() is True
    assert retire_classes.retired()


def _class_id(as_account):
    from deeptutor.classes.service import ClassService

    with as_account("adam"):
        [row] = ClassService().list_classes()
    return row["id"]


def _class_set(as_account, owner="tina", title="Week 1"):
    """Published the old way: to the teacher's classes."""
    from deeptutor.practice_sets.service import PracticeSetService

    with as_account(owner):
        created = PracticeSetService().create(title)
        PracticeSetService().update(created["id"], questions=QUESTIONS)
        PracticeSetService().publish(created["id"])
    return created["id"]


def _assigned_sets(as_account, name):
    from deeptutor.practice_sets.service import PracticeSetService

    with as_account(name):
        return [s["id"] for s in PracticeSetService().assigned()]


def _new_student(seed_user, as_user, as_account, name="nina"):
    from deeptutor.classes.service import ClassService

    user = seed_user(name, role="student")
    with as_account("adam"):
        [row] = ClassService().list_classes()
        ClassService().add_students(row["id"], [user["id"]])
    return user


# ── the migration (§2.1) ───────────────────────────────────


def test_class_receivers_become_members_and_classes_stop_counting(
    as_account, teaching_users, seed_user, as_user,
):
    from deeptutor.catalog.enrollment import joined_by
    from deeptutor.classes.service import ClassService
    from deeptutor.publishing import scope as scopes

    set_id = _class_set(as_account)
    assert set(_assigned_sets(as_account, "sara")) == {set_id}
    _retire()
    assert scopes._stored_scope(scopes.PRACTICE_SET, set_id).empty
    assert joined_by(teaching_users["sara"]["id"]) == {("practice_set", set_id): "migrated"}
    assert set(_assigned_sets(as_account, "sara")) == {set_id}
    # Leaving the class takes nothing away; a newcomer to it gets nothing.
    with as_account("adam"):
        ClassService().remove_student(_class_id(as_account), teaching_users["sara"]["id"])
    assert set(_assigned_sets(as_account, "sara")) == {set_id}
    from deeptutor.classes.service import ClassForbidden

    with pytest.raises(ClassForbidden):
        _new_student(seed_user, as_user, as_account)  # nobody joins a class any more (review M1)


def test_books_reach_class_teachers_too_and_student_shares_stay(as_account, teaching_users, tmp_path):
    from deeptutor.book import registry as book_registry
    from deeptutor.catalog.enrollment import members
    from deeptutor.publishing import scope as scopes

    tina = teaching_users["tina"]["id"]
    book = book_registry.register_book(
        base_dir=tmp_path / "books", dir_name="book_bk_cls", title="Waves", created_by=tina, visibility="public",
    )
    scopes._store_scope(scopes.BOOK, book.book_id, scopes.Scope(classes=[_class_id(as_account)]), actor_id=tina)
    shared = book_registry.register_book(
        base_dir=tmp_path / "books", dir_name="book_bk_stu", title="Notes",
        created_by=teaching_users["sara"]["id"], visibility="public",
    )
    scopes._store_scope(scopes.BOOK, shared.book_id, scopes.Scope(classes=[_class_id(as_account)]),
                        actor_id=teaching_users["sara"]["id"])
    _retire()
    assert members("book", book.book_id) == {
        teaching_users[n]["id"] for n in ("sara", "sam", "tom")
    }
    # A student's old share stays as it was: the same classmates keep reading it (review 2 #2).
    assert members("book", shared.book_id) == {teaching_users[n]["id"] for n in ("sam", "tina", "tom")}


def test_a_class_course_keeps_handing_out_its_tasks(as_account, teaching_users):
    from deeptutor.tasks.courses import CourseService, assigned_courses
    from deeptutor.tasks.service import StudentTasks, TaskService

    set_id = _class_set(as_account)
    with as_account("tina"):
        task = TaskService().create("practice_set", "Do week 1")
        TaskService().update(task["id"], target_id=set_id)
        course = CourseService().create("Course")
        CourseService().update(course["id"], task_ids=[task["id"]])
        CourseService().publish(course["id"])
    _retire()
    with as_account("sam"):
        assert [c["id"] for c in assigned_courses()] == [course["id"]]
        assert [t["key"] for t in StudentTasks().assigned()] == [task["id"]]


def test_a_standalone_task_stays_with_those_who_had_it(as_account, teaching_users):
    from deeptutor.tasks.service import StudentTasks, TaskService

    with as_account("tina"):
        task = TaskService().create("note", "Read chapter 3")
        TaskService().publish(task["id"])  # before retirement: to her classes
    _retire()
    with as_account("sara"):
        assert [t["key"] for t in StudentTasks().assigned()] == [task["id"]]


def test_hand_assigned_paths_keep_their_students(
    as_account, teaching_users, teacher_draft, publish_template,
):
    from deeptutor.catalog.enrollment import joined_by

    teacher_draft()
    result = publish_template(students=("sara",))
    _retire()
    assert joined_by(teaching_users["sara"]["id"]) == {("mastery_path", result["template"]["id"]): "migrated"}
    assert ("mastery_path", result["template"]["id"]) not in joined_by(teaching_users["sam"]["id"])


# ── policies (§2.2) ────────────────────────────────────────


def test_what_classes_allowed_is_kept_per_student(as_account, teaching_users):
    from deeptutor.classes.service import ClassService
    from deeptutor.multi_user.grants import load_grant, save_grant
    from deeptutor.multi_user.roles import effective_ai_daily_tokens, effective_student_policy

    sara, sam = teaching_users["sara"]["id"], teaching_users["sam"]["id"]
    with as_account("adam"):
        ClassService().set_student_policy(
            _class_id(as_account), {"allow_partners": True, "allow_personal_book": False, "ai_daily_tokens": 5000},
        )
    grant = load_grant(sam)
    save_grant(sam, {**grant, "student_policy": {"allow_partners": False}})  # his own setting wins
    before = {u: (effective_student_policy(u), effective_ai_daily_tokens(u)) for u in (sara, sam)}
    assert before[sara][0]["allow_partners"] and not before[sam][0]["allow_partners"]
    _retire()
    after = {u: (effective_student_policy(u), effective_ai_daily_tokens(u)) for u in (sara, sam)}
    assert after == before
    assert load_grant(sara)["student_policy"] == {
        "allow_partners": True, "allow_personal_book": False, "ai_daily_tokens": 5000,
    }


def test_a_failed_run_leaves_classes_working(as_account, monkeypatch):
    from deeptutor.catalog import retire_classes

    def broken(accounts):
        raise RuntimeError("disk full")

    from deeptutor.catalog import migration

    migration.run()
    monkeypatch.setattr(retire_classes, "_keep_policies", broken)
    with pytest.raises(RuntimeError):
        retire_classes.run()
    assert not retire_classes.retired()


# ── after retirement (§2.3) ────────────────────────────────


def test_teachers_learners_are_who_joined_their_content(as_account, teaching_users):
    from deeptutor.classes.service import teacher_student_ids

    _class_set(as_account)
    _retire()
    assert teacher_student_ids(teaching_users["tina"]["id"]) == {
        teaching_users["sara"]["id"], teaching_users["sam"]["id"],
    }
    assert teacher_student_ids(teaching_users["tom"]["id"]) == set()


def test_only_administrators_reset_passwords(as_account, teaching_users):
    from deeptutor.classes.service import ClassForbidden, ClassService

    _retire()
    with as_account("tina"), pytest.raises(ClassForbidden):
        ClassService().reset_student_password(teaching_users["sara"]["id"], "new-password-1")
    with as_account("adam"):
        ClassService().reset_student_password(teaching_users["sara"]["id"], "new-password-1")


def test_publishing_no_longer_goes_to_classes(as_account):
    from deeptutor.publishing import scope as scopes
    from deeptutor.publishing.service import PublishService, ScopeError

    _retire()
    set_id = _class_set(as_account)  # nothing chosen: nobody, no error
    assert scopes._stored_scope(scopes.PRACTICE_SET, set_id).empty
    assert _assigned_sets(as_account, "sara") == []
    with as_account("tina"), pytest.raises(ScopeError):
        PublishService().set_scope("practice_set", set_id, everyone=False, users=[], classes=[_class_id(as_account)])


# ── registration (§4) ──────────────────────────────────────


@pytest.fixture
def auth_client(teaching_users, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import deeptutor.api.routers.auth as auth_router
    from deeptutor.services.auth import TokenPayload

    adam = teaching_users["adam"]
    tokens = {"admin-token": TokenPayload(username="adam", role="admin", user_id=adam["id"])}
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "POCKETBASE_ENABLED", False)
    monkeypatch.setattr(auth_router, "decode_token", lambda token: tokens.get(token))
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    return TestClient(app)


def test_invites_no_longer_join_or_admit(as_account, auth_client):
    from deeptutor.classes.service import ClassService, student_class_ids

    with as_account("tina"):
        token = ClassService().regenerate_link(_class_id(as_account))["token"]
    _retire()
    joined = auth_client.post("/api/auth/register", json={"username": "newbie", "password": "password-123",
                                                          "invite": token})
    assert joined.status_code == 201 and joined.json()["joined_class"] is None
    assert student_class_ids(joined.json()["user_id"]) == set()
    auth_client.put("/api/auth/registration", json={"open": False}, headers={"Authorization": "Bearer admin-token"})
    refused = auth_client.post("/api/auth/register", json={"username": "other", "password": "password-123",
                                                           "invite": token})
    assert refused.status_code == 403 and "administrator" in refused.json()["detail"]


# ── tasks (§5) ─────────────────────────────────────────────


def test_tasks_go_out_with_courses_only(as_account):
    from deeptutor.tasks.courses import CourseService
    from deeptutor.tasks.service import TaskError, TaskService
    from deeptutor.tasks.store import db as task_db

    _retire()
    with as_account("tina"):
        task = TaskService().create("note", "Read")
        with pytest.raises(TaskError, match="course"):
            TaskService().publish(task["id"])
        course = CourseService().create("Course")
        CourseService().update(course["id"], task_ids=[task["id"]])
        CourseService().publish(course["id"])
        CourseService().add_new_task(course["id"], "note", "Read more")
    with task_db.read() as conn:
        statuses = {r["title"]: r["status"] for r in conn.execute("SELECT title, status FROM tasks")}
    assert statuses == {"Read": "published", "Read more": "published"}


def test_joined_sets_and_paths_are_not_tasks(as_account):
    from deeptutor.tasks.service import StudentTasks

    set_id = _class_set(as_account)
    with as_account("sara"):
        assert [t["key"] for t in StudentTasks().assigned()] == [f"practice_set:{set_id}"]
    _retire()
    with as_account("sara"):
        assert StudentTasks().assigned() == []
    assert _assigned_sets(as_account, "sara") == [set_id]  # still in her practice sets


# ── teachers and administrators join (§6) ──────────────────


def _listed_set(as_account):
    from deeptutor.catalog.listings import ListingService
    from deeptutor.practice_sets.service import PracticeSetService
    from deeptutor.publishing import scope as scopes

    with as_account("tina"):
        created = PracticeSetService().create("Market set")
        PracticeSetService().update(created["id"], questions=QUESTIONS)
        with scopes.join_only():
            PracticeSetService().publish(created["id"])
        ListingService().save("practice_set", created["id"], **META)
        ListingService().submit("practice_set", created["id"])
    with as_account("adam"):
        ListingService().approve("practice_set", created["id"])
    return created["id"]


def test_a_teacher_joins_answers_and_shows_in_the_results(as_account, teaching_users):
    from deeptutor.catalog.enrollment import join
    from deeptutor.multi_user.context import get_current_user
    from deeptutor.practice_sets.service import PracticeSetService

    _retire()
    set_id = _listed_set(as_account)
    with as_account("tom"):
        join(get_current_user(), "practice_set", set_id)
        assert [s["id"] for s in PracticeSetService().assigned()] == [set_id]
        qid = PracticeSetService().take(set_id)["questions"][0]["question_id"]
        asyncio.run(PracticeSetService().submit(set_id, {qid: "B"}))
    with as_account("tina"):
        report = PracticeSetService().report(set_id)
    tom = next(s for s in report["students"] if s["id"] == teaching_users["tom"]["id"])
    assert tom["attempts"] == 1


def test_a_teacher_joins_a_path_and_gets_a_copy(as_account, teaching_users, teacher_draft, quiet_change_model):
    from deeptutor.api.routers import mastery_path
    from deeptutor.catalog.enrollment import join
    from deeptutor.catalog.listings import ListingService
    from deeptutor.multi_user.context import get_current_user
    from deeptutor.teaching.instances import instance_path_id
    from deeptutor.teaching.models import PublishRequest
    from deeptutor.teaching.service import TeachingService

    _retire()
    teacher_draft()
    with as_account("tina"):
        service = TeachingService()
        template = service.register("topic_calculus", "")
        preview = asyncio.run(service.preview_publish(template.id))
        asyncio.run(service.publish(template.id, PublishRequest(draft_revision=preview["draft_revision"])))
        ListingService().save("mastery_path", template.id, **META)
        ListingService().submit("mastery_path", template.id)
    with as_account("adam"):
        ListingService().approve("mastery_path", template.id)
    with as_account("tom"):
        join(get_current_user(), "mastery_path", template.id)
        paths = [t["path_id"] for t in asyncio.run(mastery_path.list_topics())["topics"]]
    assert instance_path_id(template.id) in paths


def test_an_administrator_joins_a_course(as_account):
    from deeptutor.catalog.enrollment import join
    from deeptutor.catalog.listings import ListingService
    from deeptutor.multi_user.context import get_current_user
    from deeptutor.tasks.courses import CourseService, assigned_courses
    from deeptutor.tasks.service import TaskService

    _retire()
    with as_account("tina"):
        task = TaskService().create("note", "Read")
        course = CourseService().create("Course")
        CourseService().update(course["id"], task_ids=[task["id"]])
        CourseService().publish(course["id"])
        ListingService().save("course", course["id"], **META)
        ListingService().submit("course", course["id"])
    with as_account("adam"):
        ListingService().approve("course", course["id"])
        join(get_current_user(), "course", course["id"])
        assert [c["id"] for c in assigned_courses()] == [course["id"]]


# ── classification and tags (§8) ───────────────────────────


def test_administrators_adjust_the_classification(as_account):
    from deeptutor.catalog import taxonomy
    from deeptutor.catalog.listings import ListingError, ListingService

    set_id = _listed_set(as_account)
    taxonomy.update_node("primary.math", name_zh="小学数学", hidden=True)
    stages = {s["id"]: s for s in taxonomy.tree()["stages"]}
    assert "primary.math" not in [s["id"] for s in stages["primary"]["subjects"]]
    admin = {s["id"]: s for s in taxonomy.admin_tree()["stages"]}
    math = next(s for s in admin["primary"]["subjects"] if s["id"] == "primary.math")
    assert math["hidden"] and math["names"]["zh"] == "小学数学" and math["in_use"] == 1
    with as_account("tina"):
        ListingService().save("practice_set", set_id, **{**META, "title": "Kept"})  # an old choice stays
    with as_account("tom"):
        from deeptutor.practice_sets.service import PracticeSetService
        from deeptutor.publishing import scope as scopes

        other = PracticeSetService().create("Other")
        PracticeSetService().update(other["id"], questions=QUESTIONS)
        with scopes.join_only():
            PracticeSetService().publish(other["id"])
        with pytest.raises(ListingError):
            ListingService().save("practice_set", other["id"], **META)  # a hidden one is not offered

    taxonomy.add_node("primary", "书法", "Calligraphy")
    custom = next(
        s["id"] for s in taxonomy.tree()["stages"][0]["subjects"] if s["names"]["en"] == "Calligraphy"
    )
    assert taxonomy.validate(["primary"], [custom], "")
    first = taxonomy.admin_tree()["stages"][0]["subjects"][0]["id"]
    taxonomy.move_node(first, "down")
    assert taxonomy.admin_tree()["stages"][0]["subjects"][1]["id"] == first
    with pytest.raises(taxonomy.TaxonomyError):
        taxonomy.delete_node("primary.chinese")  # built in: hide instead
    taxonomy.delete_node(custom)
    assert taxonomy.node(custom) is None


def test_merging_tags(as_account):
    from deeptutor.catalog import tags
    from deeptutor.catalog.listings import ListingService

    set_id = _listed_set(as_account)
    with as_account("tina"):
        ListingService().save("practice_set", set_id, **{**META, "tags": ["python3", "Basics"]})
    tags.merge("python3", "Python")
    with as_account("tina"):
        assert ListingService().get("practice_set", set_id)["tags"] == ["Python", "Basics"]
        saved = ListingService().save("practice_set", set_id, **{**META, "tags": ["PYTHON3"]})
    assert saved["tags"] == ["Python"]
    assert tags.list_tags()["merges"][0]["to"] == "Python"
    tags.unmerge("python3")
    with as_account("tina"):
        assert ListingService().save("practice_set", set_id, **{**META, "tags": ["python3"]})["tags"] == ["python3"]


def test_administrators_correct_others_classification_not_text(as_account):
    from deeptutor.catalog.listings import ListingForbidden, ListingService

    set_id = _listed_set(as_account)
    with as_account("adam"):
        fixed = ListingService().classify(
            "practice_set", set_id, stages=["junior"], subjects=["junior.math"], difficulty="elementary", tags=["x"],
        )
    assert fixed["stages"] == ["junior"] and fixed["title"] == "Arithmetic"
    with as_account("tom"), pytest.raises(ListingForbidden):
        ListingService().classify("practice_set", set_id, stages=[], subjects=[], difficulty="", tags=[])
