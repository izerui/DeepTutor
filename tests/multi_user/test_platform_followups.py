"""Platform follow-ups: reviews and favorites, grades, remedial into a course,
administrators' own practice sets, grading a hand-in that changed, task reads.

Spec: docs/superpowers/specs/2026-11-04-platform-followups-design.md
"""

from __future__ import annotations

import asyncio

import pytest

from deeptutor.notifications import service as notifications

QUESTIONS = [
    {"question": "Explain addition.", "question_type": "short_answer", "options": {},
     "correct_answer": "Putting together.", "explanation": "", "points": 2},
]
META = dict(title="Arithmetic", summary="Add.", stages=["primary"], subjects=["primary.math"],
            difficulty="beginner", tags=[])


@pytest.fixture(autouse=True)
def _retired_and_offline(monkeypatch, teaching_users):
    from deeptutor.catalog import retire_classes

    async def ungraded(pending):
        return {}

    async def no_suggestion(_task_id, _student_id):
        return None

    monkeypatch.setattr("deeptutor.practice_sets.service._judge", ungraded)
    monkeypatch.setattr("deeptutor.tasks.service.suggest_grade", no_suggestion)
    retire_classes.run()


def _uid(teaching_users, name):
    return teaching_users[name]["id"]


def _listed_set(as_account, owner="tina", **meta):
    from deeptutor.catalog.listings import ListingService
    from deeptutor.practice_sets.service import PracticeSetService

    with as_account(owner):
        created = PracticeSetService().create("Week 1")
        PracticeSetService().update(created["id"], questions=QUESTIONS)
        PracticeSetService().publish(created["id"])
        ListingService().save("practice_set", created["id"], **{**META, **meta})
        ListingService().submit("practice_set", created["id"])
    with as_account("adam"):
        if ListingService().review_queue():
            ListingService().approve("practice_set", created["id"])
    return created["id"]


def _join(as_account, name, kind, item_id):
    from deeptutor.catalog.enrollment import join
    from deeptutor.multi_user.context import get_current_user

    with as_account(name):
        join(get_current_user(), kind, item_id)


def _reviews():
    from deeptutor.catalog.reviews import ReviewService

    return ReviewService()


def _search(as_account, name="sara", **filters):
    from deeptutor.catalog.market import Filters, search
    from deeptutor.multi_user.context import get_current_user

    with as_account(name):
        return search(get_current_user(), Filters(**filters))


# ── reviews and favorites (§1) ─────────────────────────────


def test_only_who_joined_reviews_once_and_the_author_answers(as_account, teaching_users):
    from deeptutor.catalog.listings import ListingError, ListingForbidden

    set_id = _listed_set(as_account)
    with as_account("sara"), pytest.raises(ListingForbidden):
        _reviews().save("practice_set", set_id, 5, "Great")  # not joined yet
    _join(as_account, "sara", "practice_set", set_id)
    _join(as_account, "sam", "practice_set", set_id)
    with as_account("sara"):
        with pytest.raises(ListingError):
            _reviews().save("practice_set", set_id, 6)
        _reviews().save("practice_set", set_id, 5, "Great")
        listed = _reviews().save("practice_set", set_id, 4, "Good")  # changed, still one
    assert listed["count"] == 1 and listed["mine"]["stars"] == 4
    with as_account("sam"):
        _reviews().save("practice_set", set_id, 2)
    with as_account("tina"):
        with pytest.raises(ListingForbidden):
            _reviews().save("practice_set", set_id, 5)  # her own
        answered = _reviews().reply("practice_set", set_id, _uid(teaching_users, "sam"), "Thanks, fixed.")
    assert answered["average"] == 3.0
    assert next(r for r in answered["reviews"] if r["author"] == "sam")["reply"] == "Thanks, fixed."
    assert [n["kind"] for n in notifications.list_for(_uid(teaching_users, "tina"))].count("listing_reviewed") == 1
    with as_account("sara"), pytest.raises(ListingForbidden):
        _reviews().delete("practice_set", set_id, _uid(teaching_users, "sam"))
    with as_account("adam"):
        assert _reviews().delete("practice_set", set_id, _uid(teaching_users, "sam"))["count"] == 1


def test_rating_sort_and_favorites(as_account, teaching_users):
    first = _listed_set(as_account)
    second = _listed_set(as_account)
    third = _listed_set(as_account)
    for item, stars in ((first, 3), (second, 5)):
        _join(as_account, "sara", "practice_set", item)
        with as_account("sara"):
            _reviews().save("practice_set", item, stars)
    ranked = [i["id"] for i in _search(as_account, sort="rating")["items"]]
    assert ranked == [second, first, third]  # unrated last
    entry = next(i for i in _search(as_account)["items"] if i["id"] == second)
    assert entry["rating"] == 5.0 and entry["rating_count"] == 1
    with as_account("sam"):
        _reviews().favorite("practice_set", third, True)
    found = _search(as_account, name="sam", favorites=True)
    assert [i["id"] for i in found["items"]] == [third] and found["items"][0]["favorited"]
    with as_account("sam"):
        _reviews().favorite("practice_set", third, False)
    assert _search(as_account, name="sam", favorites=True)["total"] == 0
    from deeptutor.catalog.enrollment import joined_by

    assert ("practice_set", third) not in joined_by(_uid(teaching_users, "sam"))  # a favorite is not joining


def test_a_deleted_account_takes_its_reviews(as_account, teaching_users):
    from deeptutor.catalog.reviews import forget_user

    set_id = _listed_set(as_account)
    _join(as_account, "sara", "practice_set", set_id)
    with as_account("sara"):
        _reviews().save("practice_set", set_id, 5)
        _reviews().favorite("practice_set", set_id, True)
    forget_user(_uid(teaching_users, "sara"))
    with as_account("sam"):
        assert _reviews().list_for("practice_set", set_id)["count"] == 0


# ── grades (§2) ────────────────────────────────────────────


def test_grades_belong_to_stages_and_filter(as_account):
    from deeptutor.catalog.listings import ListingError, ListingService

    with_grade = _listed_set(as_account, grades=["grade.3"])
    _listed_set(as_account, stages=["junior"], subjects=["junior.math"], grades=["grade.8"])
    with as_account("tina"):
        with pytest.raises(ListingError):
            ListingService().save("practice_set", with_grade, **{**META, "grades": ["grade.8"]})  # not primary
    found = _search(as_account, grades=["grade.3"])
    assert [i["id"] for i in found["items"]] == [with_grade]
    assert found["facets"]["grades"] == {"grade.3": 1, "grade.8": 1}  # its own condition stays open
    assert found["items"][0]["grades"] == ["grade.3"]


# ── remedial into the course (§3) ──────────────────────────


def test_a_published_set_joins_the_course_and_reaches_its_learners(as_account):
    from deeptutor.catalog.listings import ListingService
    from deeptutor.practice_sets.service import PracticeSetService
    from deeptutor.tasks.courses import CourseService
    from deeptutor.tasks.service import TaskError, TaskService

    with as_account("tina"):
        task = TaskService().create("note", "Read")
        course = CourseService().create("Course")
        CourseService().update(course["id"], task_ids=[task["id"]])
        CourseService().publish(course["id"])
        ListingService().save("course", course["id"], **META)
        ListingService().submit("course", course["id"])
    with as_account("adam"):
        ListingService().approve("course", course["id"])
    _join(as_account, "sara", "course", course["id"])
    with as_account("tina"):
        remedial = PracticeSetService().create("Remedial: Week 1")
        PracticeSetService().update(remedial["id"], questions=QUESTIONS)
        PracticeSetService().publish(remedial["id"])
        updated = CourseService().add_practice_set(course["id"], remedial["id"])
        with pytest.raises(TaskError):
            CourseService().add_practice_set(course["id"], "ps_missing")
    assert len(updated["tasks"]) == 2
    with as_account("sara"):
        assert remedial["id"] in [s["id"] for s in PracticeSetService().assigned()]


# ── administrators' own practice sets (§4) ─────────────────


def _answered_set(as_account, owner):
    from deeptutor.practice_sets.service import PracticeSetService

    set_id = _listed_set(as_account, owner=owner)
    _join(as_account, "sara", "practice_set", set_id)
    with as_account("sara"):
        qid = PracticeSetService().take(set_id)["questions"][0]["question_id"]
        asyncio.run(PracticeSetService().submit(set_id, {qid: "Putting things together."}))
    from deeptutor.practice_sets import store

    with store.read() as conn:
        submission_id = conn.execute("SELECT id FROM practice_submissions WHERE set_id = ?", (set_id,)).fetchone()[0]
    return set_id, submission_id


def test_an_administrator_reads_answers_of_their_own_set_only(as_account, teaching_users):
    from deeptutor.practice_sets import store
    from deeptutor.practice_sets.review import PracticeReview
    from deeptutor.practice_sets.service import PracticeForbidden

    own, submission = _answered_set(as_account, "adam")
    with as_account("adam"):
        attempt = PracticeReview().attempt(own, submission)
    assert attempt["questions"][0]["answer"] == "Putting things together."
    # A set handed over to the administrator keeps its writer: no answers.
    handed, handed_submission = _answered_set(as_account, "tina")
    with store.write() as conn:
        conn.execute("UPDATE practice_sets SET owner_user_id = ? WHERE id = ?", (_uid(teaching_users, "adam"), handed))
    with as_account("adam"), pytest.raises(PracticeForbidden):
        PracticeReview().attempt(handed, handed_submission)


# ── grading what the teacher read (§5) ─────────────────────


def test_grading_a_hand_in_that_changed_is_refused(as_account, teaching_users):
    from deeptutor.tasks.courses import CourseService
    from deeptutor.tasks.service import StudentTasks, TaskConflict, TaskService

    with as_account("tina"):
        homework = TaskService().create("homework", "Essay")
        course = CourseService().create("Course")
        CourseService().update(course["id"], task_ids=[homework["id"]])
        CourseService().publish(course["id"])
    import time

    from deeptutor.catalog.enrollment import refresh_course
    from deeptutor.catalog.store import db

    sara = _uid(teaching_users, "sara")
    with db.write() as conn:  # joined without a listing: the course is not in the market
        conn.execute(
            "INSERT INTO enrollments (kind, item_id, user_id, via, joined_at) VALUES ('course', ?, ?, 'self', ?)",
            (course["id"], sara, time.time()),
        )
    refresh_course(course["id"])
    with as_account("sara"):
        asyncio.run(StudentTasks().submit(homework["id"], "First.", []))
        asyncio.run(StudentTasks().submit(homework["id"], "Second.", []))
    with as_account("tina"):
        with pytest.raises(TaskConflict):
            TaskService().grade(homework["id"], sara, 80, "ok", attempt=1)
        with pytest.raises(TaskConflict):
            TaskService().send_back(homework["id"], sara, "redo", attempt=1)
        TaskService().grade(homework["id"], sara, 80, "ok", attempt=2)
