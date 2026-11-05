"""Platform batch 3: the learners of courses and Mastery Paths.

Spec: docs/superpowers/specs/2026-11-04-platform-batch3-design.md

Everything here is reached by joining.
"""

from __future__ import annotations

import asyncio

import pytest

from deeptutor.notifications import service as notifications

QUESTIONS = [
    {"question": "2 + 2 = ?", "question_type": "choice", "options": {"A": "3", "B": "4"},
     "correct_answer": "B", "explanation": "", "points": 2},
]
META = dict(title="Arithmetic", summary="Add.", stages=["primary"], subjects=["primary.math"],
            difficulty="beginner", tags=[])


@pytest.fixture(autouse=True)
def _offline(monkeypatch, teaching_users):
    async def ungraded(pending):
        return {}

    monkeypatch.setattr("deeptutor.practice_sets.service._judge", ungraded)


def _uid(teaching_users, name):
    return teaching_users[name]["id"]


def _kinds(user_id):
    return [n["kind"] for n in notifications.list_for(user_id)]


def _list_and_approve(as_account, kind, item_id, owner="tina"):
    from deeptutor.catalog.listings import ListingService

    with as_account(owner):
        ListingService().save(kind, item_id, **META)
        ListingService().submit(kind, item_id)
    with as_account("adam"):
        if ListingService().review_queue():
            ListingService().approve(kind, item_id)


def _join(as_account, name, kind, item_id):
    from deeptutor.catalog.enrollment import join
    from deeptutor.multi_user.context import get_current_user

    with as_account(name):
        join(get_current_user(), kind, item_id)


def _leave(as_account, name, kind, item_id):
    from deeptutor.catalog.enrollment import leave
    from deeptutor.multi_user.context import get_current_user

    with as_account(name):
        leave(get_current_user(), kind, item_id)


@pytest.fixture
def course(as_account):
    """Tina's listed course: a practice set and a note, in that order."""
    from types import SimpleNamespace

    from deeptutor.practice_sets.service import PracticeSetService
    from deeptutor.tasks.courses import CourseService
    from deeptutor.tasks.service import TaskService

    with as_account("tina"):
        practice = PracticeSetService().create("Week 1")
        PracticeSetService().update(practice["id"], questions=QUESTIONS)
        PracticeSetService().publish(practice["id"])
        exercise = TaskService().create("practice_set", "Do week 1")
        TaskService().update(exercise["id"], target_id=practice["id"])
        note = TaskService().create("note", "Read chapter 1")
        created = CourseService().create("Arithmetic course")
        CourseService().update(created["id"], task_ids=[exercise["id"], note["id"]])
        CourseService().publish(created["id"])
    _list_and_approve(as_account, "course", created["id"])
    return SimpleNamespace(id=created["id"], set_id=practice["id"], exercise=exercise["id"], note=note["id"])


def _answer(as_account, name, set_id, choice="B"):
    from deeptutor.practice_sets.service import PracticeSetService

    with as_account(name):
        qid = PracticeSetService().take(set_id)["questions"][0]["question_id"]
        asyncio.run(PracticeSetService().submit(set_id, {qid: choice}))


def _mark_done(as_account, name, task_id):
    from deeptutor.tasks.service import StudentTasks

    with as_account(name):
        StudentTasks().mark_done(task_id)


def _reports():
    from deeptutor.catalog.reports import LearnerReportService

    return LearnerReportService()


# ── reports (§1) ───────────────────────────────────────────


def test_course_report_shows_its_learners(as_account, teaching_users, course):
    _join(as_account, "sara", "course", course.id)
    _join(as_account, "sam", "course", course.id)
    _answer(as_account, "sara", course.set_id)
    _mark_done(as_account, "sara", course.note)
    _leave(as_account, "sam", "course", course.id)
    with as_account("tina"):
        report = _reports().report("course", course.id)
    assert [row["username"] for row in report["learners"]] == ["sara"]  # Sam left
    sara = report["learners"][0]
    assert sara["tasks"]["done"] == 2 and sara["tasks"]["total"] == 2  # answered, marked done
    assert set(sara["tasks"]) == {"done", "total", "last"}  # no homework columns (spec 2026-11-05)
    assert [p["set_id"] for p in sara["practice_sets"]] == [course.set_id]
    with as_account("tina"):
        csv_text = _reports().export("course", course.id)
        detail = _reports().learner("course", course.id, _uid(teaching_users, "sara"))
    assert csv_text.splitlines()[1].startswith("sara,")
    assert "homework" not in csv_text.splitlines()[0]
    assert detail["tasks"]["done"] == 2


def test_only_the_author_and_administrators_read_the_report(as_account, teaching_users, course):
    from deeptutor.catalog.listings import ListingNotFound

    _join(as_account, "sara", "course", course.id)
    with as_account("tom"), pytest.raises(ListingNotFound):
        _reports().report("course", course.id)
    with as_account("sara"), pytest.raises(ListingNotFound):  # a learner who joined it
        _reports().report("course", course.id)
    # Spec 2026-11-05 §2: administrators read it as its author does.
    with as_account("tina"):
        theirs = _reports().report("course", course.id)
    with as_account("adam"):
        assert _reports().report("course", course.id) == theirs
    with as_account("tina"), pytest.raises(ListingNotFound):
        _reports().learner("course", course.id, _uid(teaching_users, "sam"))  # never joined


def _market_path(as_account, teacher_draft):
    from deeptutor.teaching.models import PublishRequest
    from deeptutor.teaching.service import TeachingService

    teacher_draft()
    with as_account("tina"):
        service = TeachingService()
        template = service.register("topic_calculus", "")
        preview = asyncio.run(service.preview_publish(template.id))
        asyncio.run(service.publish(template.id, PublishRequest(draft_revision=preview["draft_revision"])))
    _list_and_approve(as_account, "mastery_path", template.id)
    return template.id


def test_path_report_and_its_points(as_account, teaching_users, teacher_draft, quiet_change_model):
    from deeptutor.api.routers import mastery_path

    template_id = _market_path(as_account, teacher_draft)
    _join(as_account, "sara", "mastery_path", template_id)
    with as_account("sara"):
        asyncio.run(mastery_path.list_topics())  # her copy is made
    with as_account("tina"):
        report = _reports().report("mastery_path", template_id)
        points = _reports().points("mastery_path", template_id, template_id)
    assert [row["username"] for row in report["learners"]] == ["sara"]
    assert report["learners"][0]["paths"][0]["kp_total"] == 3
    assert {p["name"] for p in points["points"]} == {"Limits", "Derivatives", "Chain rule"}
    assert all(p["not_mastered"] == [_uid(teaching_users, "sara")] for p in points["points"])


# ── announcements (§3) ─────────────────────────────────────


def _announcements():
    from deeptutor.catalog.announcements import AnnouncementService

    return AnnouncementService()


def test_announcements_reach_members_only(as_account, teaching_users, course):
    from deeptutor.catalog.listings import ListingForbidden

    _join(as_account, "sara", "course", course.id)
    with as_account("tina"):
        posted = _announcements().post("course", course.id, "Week 1 starts.", pinned=True)
    assert "content_announcement" in _kinds(_uid(teaching_users, "sara"))
    assert "content_announcement" not in _kinds(_uid(teaching_users, "sam"))
    with as_account("sara"):
        assert [a["text"] for a in _announcements().list_for("course", course.id)] == ["Week 1 starts."]
        assert [a["id"] for a in _announcements().mine()] == [posted["id"]]
        with pytest.raises(ListingForbidden):
            _announcements().post("course", course.id, "Hi")
        with pytest.raises(ListingForbidden):
            _announcements().delete(posted["id"])
    with as_account("sam"):
        assert _announcements().list_for("course", course.id) == []
    with as_account("tom"), pytest.raises(ListingForbidden):
        _announcements().update(posted["id"], text="Changed")
    with as_account("tina"):
        assert _announcements().update(posted["id"], text="Week 1 starts Monday.")["text"] == "Week 1 starts Monday."
    _leave(as_account, "sara", "course", course.id)
    with as_account("sara"):
        assert _announcements().list_for("course", course.id) == [] and _announcements().mine() == []
    with as_account("adam"):
        _announcements().delete(posted["id"])
    with as_account("tina"):
        assert _announcements().list_for("course", course.id) == []


def test_announcements_hide_when_taken_down(as_account, course):
    from deeptutor.catalog.listings import ListingService

    _join(as_account, "sara", "course", course.id)
    with as_account("tina"):
        _announcements().post("course", course.id, "Hello")
    with as_account("adam"):
        ListingService().take_down("course", course.id, "Wrong.")
    with as_account("sara"):
        assert _announcements().list_for("course", course.id) == [] and _announcements().mine() == []


# ── material going away (§6) ───────────────────────────────


def test_a_taken_down_book_is_closed_through_a_citing_path_too(as_account, teaching_users, tmp_path):
    import time

    from deeptutor.book import registry as book_registry
    from deeptutor.catalog.store import db
    from deeptutor.multi_user.context import get_current_user
    from deeptutor.publishing import scope as scopes

    tom = _uid(teaching_users, "tom")
    record = book_registry.register_book(
        base_dir=tmp_path / "books", dir_name="book_bk_tom", title="Optics", created_by=tom, visibility="public",
    )
    scopes._store_scope(scopes.BOOK, record.book_id, scopes.Scope(), actor_id=tom)
    # Tina's path cites it; its readers read the book through the path.
    book_registry.register_book_reference_reader("test_path", lambda referrer_id, user_id: True)
    book_registry.replace_book_references(
        "test_path", "tpl_x", [record.book_id], created_by=_uid(teaching_users, "tina"),
    )
    with as_account("sara"):
        assert book_registry.can_read_book(record, get_current_user())
    now = time.time()
    with db.write() as conn:
        conn.execute(
            "INSERT INTO listings (kind, item_id, owner_user_id, status, created_at, updated_at, blocked) "
            "VALUES ('book', ?, ?, 'taken_down', ?, ?, 1)",
            (record.book_id, tom, now, now),
        )
    with as_account("sara"):
        assert not book_registry.can_read_book(record, get_current_user())
    with as_account("tom"):
        assert book_registry.can_read_book(record, get_current_user())  # its owner


def test_authors_hear_when_cited_material_goes(as_account, teaching_users, monkeypatch):
    from deeptutor.catalog import material_notice

    monkeypatch.setattr(
        material_notice, "_citing",
        lambda kind, item_id, owner: [("mastery_path", "tpl_1", "Calculus", _uid(teaching_users, "tina"))],
    )
    material_notice.notify_lost("book", "bk_1", owner=_uid(teaching_users, "tom"), name="Optics")
    material_notice.notify_lost("book", "bk_1", owner=_uid(teaching_users, "tom"), name="Optics")
    notices = [n for n in notifications.list_for(_uid(teaching_users, "tina")) if n["kind"] == "material_withdrawn"]
    assert len(notices) == 1 and notices[0]["params"]["material"] == "Optics"  # once a day


def test_a_course_reading_a_book_is_found(as_account, teaching_users, course):
    from deeptutor.catalog.material_notice import _citing
    from deeptutor.tasks.store import db as task_db

    with task_db.write() as conn:
        conn.execute("UPDATE tasks SET kind = 'book', target_id = 'bk_9' WHERE id = ?", (course.exercise,))
    assert _citing("book", "bk_9", _uid(teaching_users, "tom")) == [
        ("course", course.id, "Arithmetic course", _uid(teaching_users, "tina")),
    ]
    assert _citing("book", "bk_9", _uid(teaching_users, "tina")) == []  # her own course


# ── handing over (§7) ──────────────────────────────────────


def test_a_course_task_goes_to_the_successor(as_account, teaching_users, course):
    from deeptutor.tasks.service import transfer_tasks
    from deeptutor.tasks.store import db as task_db

    transfer_tasks(_uid(teaching_users, "tina"), _uid(teaching_users, "adam"))
    with task_db.read() as conn:
        owner = conn.execute("SELECT owner_user_id FROM tasks WHERE id = ?", (course.note,)).fetchone()[0]
    assert owner == _uid(teaching_users, "adam")


# ── the routes ─────────────────────────────────────────────


def test_report_and_announcement_routes(as_account, teaching_users, course, monkeypatch):
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    import deeptutor.api.routers.auth as auth_router
    from deeptutor.api.routers import catalog
    from deeptutor.services.auth import TokenPayload

    tokens = {n: TokenPayload(username=n, role=r["role"], user_id=r["id"]) for n, r in teaching_users.items()}
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "decode_token", lambda token: tokens.get(token))
    app = FastAPI()
    app.include_router(catalog.router, prefix="/api/catalog", dependencies=[Depends(auth_router.require_auth)])
    client = TestClient(app)

    def call(method, name, url, **kwargs):
        return client.request(method, url, headers={"Authorization": f"Bearer {name}"}, **kwargs)

    _join(as_account, "sara", "course", course.id)
    assert call("GET", "tina", f"/api/catalog/reports/course/{course.id}?days=7").json()["period_days"] == 7
    assert call("GET", "tom", f"/api/catalog/reports/course/{course.id}").status_code == 404
    export = call("GET", "tina", f"/api/catalog/reports/course/{course.id}/export")
    assert export.headers["content-type"].startswith("text/csv") and "sara" in export.text
    posted = call("POST", "tina", f"/api/catalog/items/course/{course.id}/announcements", json={"text": "Hi"})
    assert posted.status_code == 200
    assert [a["text"] for a in call("GET", "sara", "/api/catalog/announcements/mine").json()["announcements"]] == ["Hi"]
    assert call("DELETE", "sara", f"/api/catalog/announcements/{posted.json()['id']}").status_code == 403
