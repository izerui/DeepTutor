"""Platform batch 2: what replaced classes — learners, tasks in courses, joining, taxonomy and tags.

Spec: docs/superpowers/specs/2026-11-03-platform-batch2-design.md

Classes are gone altogether (the retirement migration with them); these are
the rules that outlived them.
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


def _published_set(as_account, owner="tina", title="Week 1"):
    """Published with nobody chosen: it reaches only who joins it."""
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


# ── learners (§2.3) ────────────────────────────────────────


def test_teachers_learners_are_who_joined_their_content(as_account, teaching_users, deliver):
    from deeptutor.catalog.learners import learners_of

    set_id = _published_set(as_account)
    assert learners_of(teaching_users["tina"]["id"]) == set()
    deliver("practice_set", set_id, "sara", "sam")
    assert learners_of(teaching_users["tina"]["id"]) == {
        teaching_users["sara"]["id"], teaching_users["sam"]["id"],
    }
    assert learners_of(teaching_users["tom"]["id"]) == set()


def test_publishing_with_nobody_chosen_reaches_nobody(as_account):
    from deeptutor.publishing import scope as scopes
    from deeptutor.publishing.service import PublishService, ScopeError

    set_id = _published_set(as_account)  # nothing chosen: nobody, no error
    assert scopes._stored_scope(scopes.PRACTICE_SET, set_id).empty
    assert _assigned_sets(as_account, "sara") == []
    with as_account("tina"), pytest.raises(ScopeError):
        PublishService().set_scope("practice_set", set_id, everyone=True, users=[])


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
    from deeptutor.catalog.enrollment import joined_by

    token = "an-old-class-link"
    joined = auth_client.post("/api/auth/register", json={"username": "newbie", "password": "password-123",
                                                          "invite": token})
    assert joined.status_code == 201 and "joined_class" not in joined.json()
    assert joined_by(joined.json()["user_id"]) == {}
    auth_client.put("/api/auth/registration", json={"open": False}, headers={"Authorization": "Bearer admin-token"})
    refused = auth_client.post("/api/auth/register", json={"username": "other", "password": "password-123",
                                                           "invite": token})
    assert refused.status_code == 403 and "administrator" in refused.json()["detail"]


# ── tasks (§5) ─────────────────────────────────────────────


def test_tasks_go_out_with_courses_only(as_account):
    from deeptutor.tasks.courses import CourseService
    from deeptutor.tasks.service import TaskError, TaskService
    from deeptutor.tasks.store import db as task_db

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


def test_joined_sets_and_paths_are_not_tasks(as_account, deliver):
    from deeptutor.tasks.service import StudentTasks

    set_id = _published_set(as_account)
    deliver("practice_set", set_id, "sara")
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
