"""Audit fixes, batch B (spec docs/superpowers/specs/2026-11-07-audit-fixes-b.md).

§1 reports of content and reviews, §2 telling the people in content that it
went, §5 courses in use, §7 the connection of connected knowledge bases
(§3, partners for administrators only, was reverted on 2026-11-07).
"""

from __future__ import annotations

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


def _uid(teaching_users, name):
    return teaching_users[name]["id"]


def _listings():
    from deeptutor.catalog.listings import ListingService

    return ListingService()


def _flags():
    from deeptutor.catalog.flags import FlagService

    return FlagService()


def _kinds(teaching_users, name) -> list[str]:
    from deeptutor.notifications import service as notifications

    return [n["kind"] for n in notifications.list_for(_uid(teaching_users, name))]


def _notices(teaching_users, name, kind) -> list[dict]:
    from deeptutor.notifications import service as notifications

    return [n for n in notifications.list_for(_uid(teaching_users, name)) if n["kind"] == kind]


def _market_set(as_account, owner="tina", title="Week 1"):
    from deeptutor.practice_sets.service import PracticeSetService

    with as_account(owner):
        created = PracticeSetService().create(title)
        PracticeSetService().update(created["id"], questions=QUESTIONS)
        PracticeSetService().publish(created["id"])
    return created["id"]


def _list(as_account, kind, item_id, owner="tina"):
    with as_account(owner):
        _listings().save(kind, item_id, **META)
        _listings().submit(kind, item_id)
    with as_account("adam"):
        if _listings().review_queue():
            _listings().approve(kind, item_id)


def _join(as_account, name, kind, item_id):
    from deeptutor.catalog.enrollment import join
    from deeptutor.multi_user.context import get_current_user

    with as_account(name):
        join(get_current_user(), kind, item_id)


def _listed_set(as_account, *joiners):
    set_id = _market_set(as_account)
    _list(as_account, "practice_set", set_id)
    for name in joiners:
        _join(as_account, name, "practice_set", set_id)
    return set_id


# ── §1 reports ─────────────────────────────────────────────


def test_a_learner_reports_content_once_and_an_administrator_takes_it_down(as_account, teaching_users):
    from deeptutor.catalog.listings import ListingConflict, ListingForbidden, status
    from deeptutor.catalog.market import detail
    from deeptutor.multi_user.context import get_current_user

    set_id = _listed_set(as_account, "sara")
    with as_account("sara"):
        assert detail(get_current_user(), "practice_set", set_id)["can_report"] is True
        _flags().report("practice_set", set_id, "wrong", "Two plus two is not three.")
        assert detail(get_current_user(), "practice_set", set_id)["reported"] is True
        with pytest.raises(ListingConflict):
            _flags().report("practice_set", set_id, "advertising")  # one report per person
    with as_account("sam"):  # listed: anyone signed in may report it
        _flags().report("practice_set", set_id, "other", "Looks copied.")
    with as_account("tina"):
        assert detail(get_current_user(), "practice_set", set_id)["can_report"] is False
        with pytest.raises(ListingForbidden):
            _flags().report("practice_set", set_id, "wrong")
        with pytest.raises(ListingForbidden):
            _flags().queue()  # administrators only
    # Reports never take anything down by themselves.
    assert status("practice_set", set_id) == "listed"
    assert "report_submitted" in _kinds(teaching_users, "adam")

    with as_account("adam"):
        [group] = _flags().queue()
        assert group["count"] == 2 and group["target"] == "item" and group["title"] == "Arithmetic"
        assert group["reasons"] == {"wrong": 1, "other": 1}
        assert {r["note"] for r in group["reports"]} == {"Two plus two is not three.", "Looks copied."}
        _flags().handle("practice_set", set_id, "take_down", reason="Wrong answers.")
        assert _flags().queue() == []
    assert status("practice_set", set_id) == "taken_down"
    for name in ("sara", "sam"):
        [notice] = _notices(teaching_users, name, "report_resolved")
        assert notice["params"]["outcome"] == "taken_down" and notice["params"]["target"] == "item"


def test_other_reason_needs_a_note_and_unlisted_content_is_not_reportable_by_strangers(as_account):
    from deeptutor.catalog.listings import ListingError, ListingNotFound

    set_id = _listed_set(as_account, "sara")
    with as_account("sam"), pytest.raises(ListingError):
        _flags().report("practice_set", set_id, "other", "  ")
    with as_account("sam"), pytest.raises(ListingError):
        _flags().report("practice_set", set_id, "spam")
    with as_account("tina"):
        _listings().unlist("practice_set", set_id)
    with as_account("sam"), pytest.raises(ListingNotFound):
        _flags().report("practice_set", set_id, "wrong")
    with as_account("sara"):  # she joined it: she still may
        _flags().report("practice_set", set_id, "wrong")


def test_a_reported_review_is_deleted_or_kept_and_the_reporter_hears_it(as_account, teaching_users):
    from deeptutor.catalog.listings import ListingConflict, ListingForbidden
    from deeptutor.catalog.reviews import ReviewService

    set_id = _listed_set(as_account, "sara", "sam")
    sara = _uid(teaching_users, "sara")
    with as_account("sara"):
        ReviewService().save("practice_set", set_id, 1, "Buy cheap essays at example.com")
        with pytest.raises(ListingForbidden):
            _flags().report("practice_set", set_id, "advertising", review_user_id=sara)
    with as_account("sam"):
        [review] = ReviewService().list_for("practice_set", set_id)["reviews"]
        assert review["can_report"] and not review["reported"]
        _flags().report("practice_set", set_id, "advertising", review_user_id=sara)
        [review] = ReviewService().list_for("practice_set", set_id)["reviews"]
        assert review["reported"]
    with as_account("adam"):
        [group] = _flags().queue()
        assert group["target"] == "review" and group["review"]["text"].startswith("Buy cheap")
        _flags().handle("practice_set", set_id, "delete_review", review_user_id=sara)
        assert _flags().queue() == []
    with as_account("sam"):
        assert ReviewService().list_for("practice_set", set_id)["reviews"] == []
    [notice] = _notices(teaching_users, "sam", "report_resolved")
    assert notice["params"]["outcome"] == "review_deleted" and notice["params"]["target"] == "review"

    # Dismissed: the review stays and the reporter is told so; dismissing again changes nothing.
    with as_account("sara"):
        ReviewService().save("practice_set", set_id, 4, "Fine.")
    with as_account("sam"):
        _flags().report("practice_set", set_id, "inappropriate", review_user_id=sara)
    with as_account("adam"):
        _flags().handle("practice_set", set_id, "dismiss", review_user_id=sara)
        with pytest.raises(ListingConflict):
            _flags().handle("practice_set", set_id, "dismiss", review_user_id=sara)
    with as_account("sam"):
        assert len(ReviewService().list_for("practice_set", set_id)["reviews"]) == 1
    outcomes = [n["params"]["outcome"] for n in _notices(teaching_users, "sam", "report_resolved")]
    assert sorted(outcomes) == ["dismissed", "review_deleted"]


def test_report_routes_are_registered():
    from deeptutor.api.routers import catalog

    paths = {(m, r.path) for r in catalog.router.routes for m in getattr(r, "methods", set()) or set()}
    assert ("POST", "/items/{kind}/{item_id}/report") in paths
    assert ("POST", "/items/{kind}/{item_id}/reviews/{user_id}/report") in paths
    assert ("GET", "/review/reports") in paths
    assert ("POST", "/review/reports/handle") in paths


# ── §2 telling the people in it ────────────────────────────


def test_taking_down_unlisting_and_archiving_tell_who_joined(as_account, teaching_users):
    from deeptutor.catalog.market import detail
    from deeptutor.multi_user.context import get_current_user
    from deeptutor.practice_sets.service import PracticeSetService

    taken = _listed_set(as_account, "sara")
    with as_account("adam"):
        _listings().take_down("practice_set", taken, "Wrong.")
    [notice] = _notices(teaching_users, "sara", "content_withdrawn")
    assert notice["params"]["how"] == "taken_down" and notice["params"]["title"] == "Arithmetic"
    with as_account("sara"):
        seen = detail(get_current_user(), "practice_set", taken)
    # Still in what she joined, with why instead of "go study".
    assert seen["joined"] and seen["closed"] and seen["unavailable"] == "taken_down"
    assert "content_withdrawn" not in _kinds(teaching_users, "sam")  # never joined
    assert "content_withdrawn" not in _kinds(teaching_users, "tina")  # its author hears otherwise

    unlisted = _listed_set(as_account, "sam")
    with as_account("tina"):
        _listings().unlist("practice_set", unlisted)
    [notice] = _notices(teaching_users, "sam", "content_withdrawn")
    assert notice["params"]["how"] == "unlisted"
    with as_account("sam"):
        seen = detail(get_current_user(), "practice_set", unlisted)
    assert seen["joined"] and not seen["closed"] and seen["unavailable"] is None  # still studied

    with as_account("tina"):
        PracticeSetService().archive(unlisted)
    hows = sorted(n["params"]["how"] for n in _notices(teaching_users, "sam", "content_withdrawn"))
    assert hows == ["archived", "unlisted"]
    with as_account("sam"):
        assert detail(get_current_user(), "practice_set", unlisted)["unavailable"] == "archived"


def test_a_taken_down_course_stays_among_what_one_learns(as_account, teaching_users):
    from deeptutor.tasks.courses import CourseService, assigned_courses
    from deeptutor.tasks.service import StudentTasks, TaskService

    with as_account("tina"):
        note = TaskService().create("note", "Read")
        course = CourseService().create("Waves")
        CourseService().update(course["id"], task_ids=[note["id"]])
        CourseService().publish(course["id"])
    _list(as_account, "course", course["id"])
    _join(as_account, "sara", "course", course["id"])
    with as_account("sara"):
        StudentTasks().mark_done(note["id"])
        [mine] = assigned_courses()
    assert mine["unavailable"] is None
    with as_account("adam"):
        _listings().take_down("course", course["id"], "Off topic.")
    [notice] = _notices(teaching_users, "sara", "content_withdrawn")
    assert notice["params"]["how"] == "taken_down" and notice["link"] == f"/tasks?course={course['id']}"
    with as_account("sara"):
        [kept] = assigned_courses()
    assert kept["id"] == course["id"] and kept["unavailable"] == "taken_down"
    assert [t["state"] for t in kept["tasks"]] == ["completed"]  # her record stays


# ── §5 courses in use ──────────────────────────────────────


def test_a_new_lesson_in_a_published_course_tells_who_joined(as_account, teaching_users):
    from deeptutor.tasks.courses import CourseService

    with as_account("tina"):
        course = CourseService().create("Waves")
        course = CourseService().add_new_task(course["id"], "note", "Read")
        CourseService().publish(course["id"])
    _list(as_account, "course", course["id"])
    _join(as_account, "sara", "course", course["id"])
    assert "task_published" not in _kinds(teaching_users, "sara")
    with as_account("tina"):
        CourseService().add_new_task(course["id"], "note", "Revise")
    [notice] = _notices(teaching_users, "sara", "task_published")
    assert notice["params"]["course"] == "Waves" and notice["params"]["title"] == "Revise"
    assert notice["link"] == f"/tasks?course={course['id']}"
    assert "task_published" not in _kinds(teaching_users, "sam")  # never joined
    assert "task_published" not in _kinds(teaching_users, "tina")


def test_an_archived_practice_set_in_a_course_says_its_author_archived_it(as_account, teaching_users):
    from deeptutor.practice_sets.service import PracticeSetService
    from deeptutor.tasks.courses import CourseService
    from deeptutor.tasks.service import StudentTasks

    set_id = _market_set(as_account)
    with as_account("tina"):
        course = CourseService().create("Arithmetic course")
        course = CourseService().add_practice_set(course["id"], set_id)
        CourseService().publish(course["id"])
    _list(as_account, "course", course["id"])
    _join(as_account, "sara", "course", course["id"])
    with as_account("sara"):
        [lesson] = StudentTasks().assigned()
    assert lesson["state"] == "open" and lesson["target_archived"] is False
    with as_account("tina"):
        PracticeSetService().archive(set_id)
        [authored] = CourseService().get(course["id"])["tasks"]
    assert authored["target_archived"] is True  # the author sees it in the course editor
    with as_account("sara"):
        [lesson] = StudentTasks().assigned()
    assert lesson["state"] == "closed" and lesson["target_archived"] is True


def test_market_counts_include_who_came_through_a_course(as_account, teaching_users):
    from deeptutor.catalog.enrollment import joined_count, members
    from deeptutor.tasks.courses import CourseService

    set_id = _market_set(as_account)
    with as_account("tina"):
        course = CourseService().create("Arithmetic course")
        course = CourseService().add_practice_set(course["id"], set_id)
        CourseService().publish(course["id"])
    _list(as_account, "course", course["id"])
    _list(as_account, "practice_set", set_id)
    _join(as_account, "sara", "course", course["id"])
    _join(as_account, "sam", "practice_set", set_id)
    # The same people as the learning report reads.
    assert joined_count("practice_set", set_id) == 2 == len(members("practice_set", set_id))
    with as_account("tina"):
        [listing] = [row for row in _listings().mine() if row["id"] == set_id]
    assert listing["learners"] == 2


# ── §7 connected knowledge bases ───────────────────────────


@pytest.mark.parametrize("admin, field, expected", [
    (False, {"server_url": "http://169.254.169.254"}, 403),
    (False, {"api_key": "stolen"}, 403),
    (False, {"server_url": "https://lightrag.example"}, 200),  # sent back unchanged
    (False, {"search_mode": "naive"}, 200),
    (True, {"server_url": "https://other.example"}, 200),
])
def test_only_administrators_change_where_a_connected_kb_connects(monkeypatch, tmp_path, admin, field, expected):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from deeptutor.api.routers import knowledge as knowledge_router
    from deeptutor.multi_user.models import CurrentUser, UserScope

    class _Service:
        def __init__(self) -> None:
            self.config = {
                "type": "lightrag_server", "rag_provider": "lightrag-server",
                "server_url": "https://lightrag.example", "api_key": "secret",
            }

        def get_kb_config(self, _name):
            return dict(self.config)

        def set_kb_config(self, _name, config):
            self.config.update(config)

    service = _Service()
    monkeypatch.setattr(knowledge_router, "_kb_config_target", lambda name, **_kw: (service, name, tmp_path))
    user = CurrentUser("u_admin" if admin else "u_sara", "adam" if admin else "sara",
                       "admin" if admin else "student", UserScope(kind="user", user_id="u", root=tmp_path))
    monkeypatch.setattr(knowledge_router, "get_current_user", lambda: user)
    app = FastAPI()
    app.include_router(knowledge_router.router, prefix="/api")
    with TestClient(app) as client:
        response = client.put("/api/knowledge-bases/remote/config", json=field)
    assert response.status_code == expected, response.text
    if expected == 403:
        assert service.config["server_url"] == "https://lightrag.example" and service.config["api_key"] == "secret"
