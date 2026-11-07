"""Polish batch, part A (spec docs/superpowers/specs/2026-11-07-polish-batch.md).

§1 joined practice sets and Mastery Paths that went stay in one's lists,
§2 authors hear of joins and see who came in over the last week, §3 learners
delete their own account and a deleted author's listings leave the market.
"""

from __future__ import annotations

import asyncio
import time

import pytest

QUESTIONS = [
    {"question": "2 + 2 = ?", "question_type": "choice", "options": {"A": "3", "B": "4"},
     "correct_answer": "B", "explanation": "Count.", "points": 2},
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


def _leave(as_account, name, kind, item_id):
    from deeptutor.catalog.enrollment import leave
    from deeptutor.multi_user.context import get_current_user

    with as_account(name):
        leave(get_current_user(), kind, item_id)


def _listed_set(as_account, *joiners, owner="tina"):
    set_id = _market_set(as_account, owner=owner)
    _list(as_account, "practice_set", set_id, owner=owner)
    for name in joiners:
        _join(as_account, name, "practice_set", set_id)
    return set_id


def _assigned(as_account, name):
    from deeptutor.practice_sets.service import PracticeSetService

    with as_account(name):
        return {row["id"]: row for row in PracticeSetService().assigned()}


def _answer(as_account, name, set_id):
    from deeptutor.practice_sets.service import PracticeSetService

    with as_account(name):
        view = PracticeSetService().take(set_id)
        answers = {q["question_id"]: "B" for q in view["questions"]}
        return asyncio.run(PracticeSetService().submit(set_id, answers, revision=view["current_revision"]))


# ── §1 practice sets ───────────────────────────────────────


def test_a_taken_down_set_stays_among_those_joined_with_its_results_read_only(as_account):
    from deeptutor.practice_sets.service import PracticeError, PracticeNotFound, PracticeSetService

    set_id = _listed_set(as_account, "sara", "sam")
    _answer(as_account, "sara", set_id)
    with as_account("adam"):
        _listings().take_down("practice_set", set_id, "Wrong answers.")

    row = _assigned(as_account, "sara")[set_id]
    assert row["unavailable"] == "taken_down" and row["submissions"] == 1 and row["best_score"] == 2
    with as_account("sara"):
        view = PracticeSetService().take(set_id)
        assert view["unavailable"] == "taken_down"
        assert [s["score"] for s in view["submissions"]] == [2]
        assert view["latest_results"] and view["questions"][0]["correct_answer"] == "B"
        with pytest.raises((PracticeNotFound, PracticeError)):
            asyncio.run(PracticeSetService().submit(set_id, {}, revision=1))
    # Sam never answered: the card stays, nothing to show but why.
    assert _assigned(as_account, "sam")[set_id]["unavailable"] == "taken_down"
    with as_account("sam"):
        assert PracticeSetService().take(set_id)["questions"] == []
    # Someone who never joined it gets nothing.
    with as_account("uma"), pytest.raises(PracticeNotFound):
        PracticeSetService().take(set_id)


def test_archived_and_unlisted_sets_are_marked_and_only_unlisted_ones_are_answered(as_account):
    from deeptutor.practice_sets.service import PracticeSetService

    archived = _listed_set(as_account, "sara")
    unlisted = _listed_set(as_account, "sara")
    with as_account("tina"):
        PracticeSetService().archive(archived)
        _listings().unlist("practice_set", unlisted)

    rows = _assigned(as_account, "sara")
    assert rows[archived]["unavailable"] == "archived"
    assert rows[unlisted]["unavailable"] is None and rows[unlisted]["unlisted"] is True
    _answer(as_account, "sara", unlisted)  # out of the market by its author: still answered

    # Leaving takes it off the list, also once it went.
    _leave(as_account, "sara", "practice_set", archived)
    assert archived not in _assigned(as_account, "sara")


# ── §1 Mastery Paths and courses ───────────────────────────


def _market_path(as_account, teacher_draft):
    from deeptutor.teaching.models import PublishRequest
    from deeptutor.teaching.service import TeachingService

    teacher_draft()
    with as_account("tina"):
        service = TeachingService()
        template = service.register("topic_calculus", "")
        preview = asyncio.run(service.preview_publish(template.id))
        asyncio.run(service.publish(template.id, PublishRequest(draft_revision=preview["draft_revision"])))
    return template.id


def _sara_topics(as_account):
    from deeptutor.api.routers import mastery_path

    with as_account("sara"):
        topics = asyncio.run(mastery_path.list_topics())["topics"]
        gone = asyncio.run(mastery_path.list_unavailable_topics())["topics"]
    return topics, gone


def test_a_taken_down_path_stays_listed_read_only_and_an_unlisted_one_is_marked(
    as_account, teacher_draft, quiet_change_model,
):
    from deeptutor.teaching.instances import instance_path_id

    template_id = _market_path(as_account, teacher_draft)
    _list(as_account, "mastery_path", template_id)
    _join(as_account, "sara", "mastery_path", template_id)
    topics, gone = _sara_topics(as_account)
    assert [t["path_id"] for t in topics] == [instance_path_id(template_id)] and gone == []
    assert topics[0]["origin"]["unlisted"] is False

    with as_account("tina"):
        _listings().unlist("mastery_path", template_id)
    topics, gone = _sara_topics(as_account)
    assert topics[0]["origin"]["unlisted"] is True and gone == []

    with as_account("adam"):
        _listings().take_down("mastery_path", template_id, "Copied.")
    topics, gone = _sara_topics(as_account)
    assert topics == []
    [card] = gone
    assert card["path_id"] == instance_path_id(template_id) and card["unavailable"] == "taken_down"
    assert card["objectives"] == 3 and card["mastered"] == 0


def test_an_archived_path_one_joined_stays_as_archived(as_account, teacher_draft, quiet_change_model):
    from deeptutor.teaching.service import TeachingService

    template_id = _market_path(as_account, teacher_draft)
    _list(as_account, "mastery_path", template_id)
    _join(as_account, "sara", "mastery_path", template_id)
    _sara_topics(as_account)  # the copy is made when the list is first read
    with as_account("tina"):
        TeachingService().archive(template_id)
    topics, gone = _sara_topics(as_account)
    assert topics == [] and [c["unavailable"] for c in gone] == ["archived"]


def test_an_unlisted_course_is_marked_among_those_learned(as_account):
    from deeptutor.tasks.courses import CourseService, assigned_courses
    from deeptutor.tasks.service import TaskService

    with as_account("tina"):
        course = CourseService().create("Algebra")
        task = TaskService().create("note", "Read")
        CourseService().update(course["id"], task_ids=[task["id"]])
        CourseService().publish(course["id"])
    _list(as_account, "course", course["id"])
    _join(as_account, "sara", "course", course["id"])
    with as_account("sara"):
        assert assigned_courses()[0]["unlisted"] is False
    with as_account("tina"):
        _listings().unlist("course", course["id"])
    with as_account("sara"):
        [row] = assigned_courses()
    assert row["unlisted"] is True and row["unavailable"] is None


# ── §2 joins ───────────────────────────────────────────────


def test_the_author_hears_of_joins_once_a_day_per_item_counted(as_account, teaching_users):
    set_id = _listed_set(as_account, "sara", "sam")
    [notice] = _notices(teaching_users, "tina", "content_joined")
    assert notice["count"] == 2 and notice["params"]["title"] == "Week 1"
    assert notice["link"] == f"/market/item?kind=practice_set&id={set_id}"
    # Leaving and joining again is not news.
    _leave(as_account, "sam", "practice_set", set_id)
    _join(as_account, "sam", "practice_set", set_id)
    [notice] = _notices(teaching_users, "tina", "content_joined")
    assert notice["count"] == 2


def test_who_came_in_over_the_last_week_shows_per_item_and_in_all(as_account, teaching_users):
    from deeptutor.catalog.market import author_page
    from deeptutor.catalog.store import db
    from deeptutor.multi_user.context import get_current_user

    first = _listed_set(as_account, "sara", "sam")
    second = _listed_set(as_account, "sara")
    # Sam came into the first one long ago.
    with db.write() as conn:
        conn.execute(
            "UPDATE enrollments SET joined_at = ? WHERE item_id = ? AND user_id = ?",
            (time.time() - 30 * 86400, first, _uid(teaching_users, "sam")),
        )
    with as_account("uma"):
        page = author_page(get_current_user(), _uid(teaching_users, "tina"))
    recent = {item["id"]: item["recent_learners"] for item in page["items"]}
    assert recent == {first: 1, second: 1}
    assert page["recent_learners"] == 1  # Sara, once
    assert page["learners"] == 3
    with as_account("tina"):
        mine = {row["id"]: row for row in _listings().mine()}
    assert mine[first]["recent_learners"] == 1 and mine[first]["learners"] == 2


# ── §3 accounts ────────────────────────────────────────────


@pytest.fixture
def auth_on(monkeypatch):
    import deeptutor.api.routers.auth as auth_router

    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "POCKETBASE_ENABLED", False)
    monkeypatch.setattr("deeptutor.services.auth.AUTH_ENABLED", True)
    return auth_router


def _delete_own(auth_router, teaching_users, name, password="password1234"):
    from fastapi import Response

    from deeptutor.services.auth import TokenPayload

    record = teaching_users[name]
    payload = TokenPayload(username=name, role=record["role"], user_id=record["id"])
    return asyncio.run(auth_router.delete_own_account(
        auth_router.DeleteOwnAccount(password=password), Response(), payload=payload,
    ))


def test_a_learner_deletes_their_own_account_and_their_listings_leave_the_market(
    as_account, teaching_users, auth_on,
):
    from fastapi import HTTPException

    from deeptutor.catalog.listings import status
    from deeptutor.catalog.store import db
    from deeptutor.multi_user.identity import get_user_by_id

    set_id = _listed_set(as_account, "sam", owner="sara")
    with pytest.raises(HTTPException) as refused:
        _delete_own(auth_on, teaching_users, "sara", password="wrong-password")
    assert refused.value.status_code == 400
    assert get_user_by_id(_uid(teaching_users, "sara")) is not None

    assert _delete_own(auth_on, teaching_users, "sara") == {"ok": True}
    assert get_user_by_id(_uid(teaching_users, "sara")) is None
    # Handed to the primary administrator, out of the market; the joiner keeps answering.
    assert status("practice_set", set_id) == "unlisted"
    with db.read() as conn:
        owner = conn.execute("SELECT owner_user_id FROM listings WHERE item_id = ?", (set_id,)).fetchone()[0]
    assert owner == _uid(teaching_users, "adam")
    [notice] = _notices(teaching_users, "sam", "content_withdrawn")
    assert notice["params"]["how"] == "author_deleted"
    row = _assigned(as_account, "sam")[set_id]
    assert row["unavailable"] is None and row["unlisted"] is True
    _answer(as_account, "sam", set_id)

    # An administrator puts it back.
    with as_account("adam"):
        _listings().relist_handed_over("practice_set", set_id)
    assert status("practice_set", set_id) == "listed"


def test_administrators_cannot_delete_their_own_account_this_way(teaching_users, auth_on):
    from fastapi import HTTPException

    from deeptutor.multi_user.identity import get_user_by_id

    with pytest.raises(HTTPException) as refused:
        _delete_own(auth_on, teaching_users, "adam")
    assert refused.value.status_code == 403
    assert get_user_by_id(_uid(teaching_users, "adam")) is not None


def test_an_administrator_deleting_an_author_also_takes_their_listings_out(as_account, teaching_users, auth_on):
    from deeptutor.catalog.listings import ListingConflict, status
    from deeptutor.services.auth import TokenPayload

    set_id = _listed_set(as_account, "sam")
    pending = _market_set(as_account, title="Week 2")
    with as_account("tina"):
        _listings().save("practice_set", pending, **META)
        _listings().submit("practice_set", pending)
    own = _listed_set(as_account, "sam", owner="tom")
    with as_account("tom"):
        _listings().unlist("practice_set", own)

    admin = TokenPayload(username="adam", role="admin", user_id=_uid(teaching_users, "adam"))
    assert auth_on._delete_admin_user("tina", admin) == (200, "")
    assert status("practice_set", set_id) == "unlisted"
    assert status("practice_set", pending) == "draft"
    assert "author_deleted" in [n["params"]["how"] for n in _notices(teaching_users, "sam", "content_withdrawn")]
    # An author's own decision to take something out is never overridden here.
    with as_account("adam"), pytest.raises(ListingConflict):
        _listings().relist_handed_over("practice_set", own)
