"""Author page stats and the market's featured rows.

Spec: docs/superpowers/specs/2026-11-06-platform-wording-and-authors.md §2, §3
"""

from __future__ import annotations

import time

import pytest

QUESTIONS = [
    {"question": "2 + 2 = ?", "question_type": "choice", "options": {"A": "3", "B": "4"},
     "correct_answer": "B", "explanation": "", "points": 2},
]

META = dict(
    title="Arithmetic", summary="Add small numbers.", stages=["primary"], subjects=["primary.math"],
    difficulty="beginner", tags=["Addition"],
)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    async def ungraded(pending):
        return {}

    monkeypatch.setattr("deeptutor.practice_sets.service._judge", ungraded)


def _listings():
    from deeptutor.catalog.listings import ListingService

    return ListingService()


def _listed_set(as_account, owner="tina", title="Week 1"):
    from deeptutor.practice_sets.service import PracticeSetService
    from deeptutor.publishing import scope as scopes

    with as_account(owner):
        created = PracticeSetService().create(title)
        PracticeSetService().update(created["id"], questions=QUESTIONS)
        with scopes.join_only():
            PracticeSetService().publish(created["id"])
        _listings().save("practice_set", created["id"], **{**META, "title": title})
        result = _listings().submit("practice_set", created["id"])
    if result["status"] == "pending":
        with as_account("adam"):
            _listings().approve("practice_set", created["id"])
    return created["id"]


def _rate(item_id, *stars, kind="practice_set"):
    """Reviews written straight into the store, one per made-up reader."""
    from deeptutor.catalog.store import db

    now = time.time()
    with db.write() as conn:
        for n, value in enumerate(stars):
            conn.execute(
                "INSERT INTO reviews (kind, item_id, user_id, stars, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (kind, item_id, f"reader-{n}", value, now, now),
            )


def _learners(monkeypatch, counts):
    monkeypatch.setattr(
        "deeptutor.catalog.market.enrollment.joined_counts",
        lambda keys=None: {("practice_set", k): v for k, v in counts.items()},
    )


def _listed_at(item_id, at):
    from deeptutor.catalog.store import db

    with db.write() as conn:
        conn.execute("UPDATE listings SET listed_at = ? WHERE item_id = ?", (at, item_id))


def _featured(as_account, name="sam"):
    from deeptutor.catalog.market import featured
    from deeptutor.multi_user.context import get_current_user

    with as_account(name):
        return featured(get_current_user())


def _author(as_account, author_id, name="sam"):
    from deeptutor.catalog.market import author_page
    from deeptutor.multi_user.context import get_current_user

    with as_account(name):
        return author_page(get_current_user(), author_id)


def test_author_page_counts_items_and_weights_the_rating(as_account, teaching_users, monkeypatch):
    first = _listed_set(as_account, title="One")
    second = _listed_set(as_account, title="Two")
    third = _listed_set(as_account, title="Three")  # nobody rated it
    _rate(first, 5, 5, 5)  # 5.0 by three
    _rate(second, 2)  # 2.0 by one
    _learners(monkeypatch, {first: 4, second: 1})
    page = _author(as_account, teaching_users["tina"]["id"])
    assert page["item_count"] == 3
    assert page["rating_count"] == 4
    assert page["rating"] == pytest.approx((5 * 3 + 2) / 4, abs=0.01)  # weighted, unrated left out
    assert page["learners"] == 5
    by_id = {i["id"]: i for i in page["items"]}
    assert by_id[first]["rating"] == 5.0 and by_id[first]["rating_count"] == 3 and by_id[first]["learners"] == 4
    assert by_id[third]["rating"] is None and by_id[third]["rating_count"] == 0


def test_author_page_without_ratings(as_account, teaching_users):
    _listed_set(as_account)
    page = _author(as_account, teaching_users["tina"]["id"])
    assert page["item_count"] == 1 and page["rating"] is None and page["rating_count"] == 0


def test_featured_popular_orders_by_learners_then_newest_and_caps_at_eight(as_account, monkeypatch):
    ids = [_listed_set(as_account, title=f"Set {n}") for n in range(10)]
    base = time.time() - 1000
    for n, item_id in enumerate(ids):
        _listed_at(item_id, base + n)  # later index = newer
    # ids[0] and ids[1] tie: the newer comes first.
    _learners(monkeypatch, {ids[0]: 9, ids[1]: 9, ids[2]: 7, ids[3]: 3})
    popular = [e["id"] for e in _featured(as_account)["popular"]]
    assert len(popular) == 8
    assert popular[:4] == [ids[1], ids[0], ids[2], ids[3]]
    # The rest have no learners: newest first.
    assert popular[4:] == [ids[9], ids[8], ids[7], ids[6]]


def test_featured_top_rated_needs_three_ratings_and_caps_at_eight(as_account):
    ids = [_listed_set(as_account, title=f"Set {n}") for n in range(11)]
    base = time.time() - 1000
    for n, item_id in enumerate(ids):
        _listed_at(item_id, base + n)
    _rate(ids[0], 5, 5)  # two ratings only: left out
    _rate(ids[1], 4, 4, 4)
    _rate(ids[2], 4, 4, 4, 4)  # same average, more ratings: before ids[1]
    _rate(ids[3], 5, 5, 5)
    for item_id in ids[4:10]:
        _rate(item_id, 3, 3, 3)  # ties throughout: newest first
    top = _featured(as_account)["top_rated"]
    assert len(top) == 8
    assert [e["id"] for e in top][:3] == [ids[3], ids[2], ids[1]]
    assert [e["id"] for e in top][3:] == [ids[9], ids[8], ids[7], ids[6], ids[5]]
    assert ids[0] not in {e["id"] for e in top} and ids[10] not in {e["id"] for e in top}
    entry = top[0]
    # Same shape as a search entry.
    assert {"kind", "id", "title", "author", "learners", "rating", "rating_count", "joined", "favorited"} <= set(entry)
    assert not any(k.startswith("_") for k in entry)


def test_featured_leaves_out_unlisted_taken_down_and_empty_courses(as_account, monkeypatch):
    from deeptutor.publishing import scope as scopes
    from deeptutor.tasks.courses import CourseService
    from deeptutor.tasks.service import TaskService

    kept = _listed_set(as_account, title="Kept")
    unlisted = _listed_set(as_account, title="Unlisted")
    taken = _listed_set(as_account, title="Taken down")
    with as_account("tina"):
        _listings().unlist("practice_set", unlisted)
    with as_account("adam"):
        _listings().take_down("practice_set", taken, "Copied content.")
    with as_account("tina"):
        task = TaskService().create("practice_set", "Do it")
        TaskService().update(task["id"], target_id=kept)
        course = CourseService().create("Course")
        CourseService().update(course["id"], task_ids=[task["id"]])
        with scopes.join_only():
            CourseService().publish(course["id"])
        _listings().save("course", course["id"], **META)
        if _listings().submit("course", course["id"])["status"] == "pending":
            with as_account("adam"):
                _listings().approve("course", course["id"])
        CourseService().update(course["id"], task_ids=[])  # emptied: out of the market
    for item_id in (kept, unlisted, taken):
        _rate(item_id, 5, 5, 5)
    _rate(course["id"], 5, 5, 5, kind="course")
    shown = _featured(as_account)
    assert [e["id"] for e in shown["popular"]] == [kept]
    assert [e["id"] for e in shown["top_rated"]] == [kept]


def test_featured_route(as_account, teaching_users, monkeypatch):
    import asyncio

    from deeptutor.api.routers import catalog

    item_id = _listed_set(as_account)
    with as_account("sam"):
        result = asyncio.run(catalog.featured())
    assert [e["id"] for e in result["popular"]] == [item_id]
    assert result["top_rated"] == []



def test_a_published_lesson_names_its_course_in_the_notice(as_account, monkeypatch):
    """Spec 2026-11-06 §1: the notice reads "<author> added a lesson to <course>"."""
    from deeptutor.tasks.courses import CourseService
    from deeptutor.tasks.service import TaskService

    sent = []
    monkeypatch.setattr(
        "deeptutor.notifications.service.notify", lambda users, kind, params, **kw: sent.append((kind, params))
    )
    with as_account("tina"):
        task = TaskService().create("note", "Read chapter 1")
        course = CourseService().create("Arithmetic course")
        CourseService().update(course["id"], task_ids=[task["id"]])
        TaskService().publish(task["id"])
    params = next(p for kind, p in sent if kind == "task_published")
    assert params["course"] == "Arithmetic course"
    assert params["teacher"] == "tina"  # the stored name of the author's param stays
    assert params["title"] == "Read chapter 1"
