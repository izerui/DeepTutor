"""Fixes from the review of the learning-platform work.

Report: .superpowers/platform-review-report.md (H = high, M = medium, L = low).
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


def _retire():
    from deeptutor.catalog import migration, retire_classes

    migration.run()
    assert retire_classes.run() is True


def _uid(teaching_users, name):
    return teaching_users[name]["id"]


def _listings():
    from deeptutor.catalog.listings import ListingService

    return ListingService()


def _market_set(as_account, owner="tina"):
    from deeptutor.practice_sets.service import PracticeSetService

    with as_account(owner):
        created = PracticeSetService().create("Week 1")
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


# ── H1 ─────────────────────────────────────────────────────


def test_a_course_does_not_hand_out_someone_elses_unlisted_book(as_account, teaching_users, tmp_path):
    from deeptutor.book import registry as book_registry
    from deeptutor.catalog.enrollment import join, members
    from deeptutor.multi_user.context import get_current_user
    from deeptutor.publishing import scope as scopes
    from deeptutor.tasks.courses import CourseService
    from deeptutor.tasks.service import TaskError, TaskService
    from deeptutor.tasks.store import db as task_db

    _retire()
    tom = _uid(teaching_users, "tom")
    book = book_registry.register_book(
        base_dir=tmp_path / "books", dir_name="book_bk_t", title="Optics", created_by=tom, visibility="public",
    )
    scopes._store_scope(scopes.BOOK, book.book_id, scopes.Scope(users=[_uid(teaching_users, "tina")]), actor_id=tom)
    with as_account("tina"):
        note = TaskService().create("note", "Read")
        course = CourseService().create("Course")
        CourseService().update(course["id"], task_ids=[note["id"]])
        CourseService().publish(course["id"])
    _list(as_account, "course", course["id"])
    with as_account("sara"):
        join(get_current_user(), "course", course["id"])
    with as_account("tina"):
        reading = TaskService().create("book", "Read optics")
    with task_db.write() as conn:  # the chapter check needs a real spine; stand in for it
        conn.execute("UPDATE tasks SET target_id = ? WHERE id = ?", (book.book_id, reading["id"]))
    with as_account("tina"), pytest.raises(TaskError, match="not in the market"):
        CourseService().update(course["id"], task_ids=[note["id"], reading["id"]])
    # Even if it got into the course some other way, its learners do not get the book.
    with task_db.write() as conn:
        conn.execute("INSERT INTO course_items VALUES (?, ?, 1)", (course["id"], reading["id"]))
    from deeptutor.catalog.enrollment import refresh_course

    refresh_course(course["id"])
    assert _uid(teaching_users, "sara") not in members("book", book.book_id)


# ── H2, H3 ─────────────────────────────────────────────────


def test_retiring_waits_for_the_market_migration(as_account):
    from deeptutor.catalog import retire_classes

    assert retire_classes.run() is False
    assert not retire_classes.retired()


def test_an_unreadable_item_stops_the_retirement(as_account, monkeypatch):
    from deeptutor.catalog import items, migration, retire_classes

    _market_set(as_account)  # sent to Tina's class
    migration.run()

    def unreadable(kind, item_id):
        raise OSError("disk")

    monkeypatch.setattr(items, "owner", unreadable)
    with pytest.raises(OSError):
        retire_classes.run()
    assert not retire_classes.retired()


# ── M1, M2, M3, M4 ─────────────────────────────────────────


def test_after_retirement_classes_give_no_reports_and_take_nobody(as_account):
    from deeptutor.classes.report import ReportService
    from deeptutor.classes.service import ClassForbidden, ClassService

    with as_account("adam"):
        [row] = ClassService().list_classes()
    _retire()
    with as_account("tina"):
        with pytest.raises(ClassForbidden):
            ReportService().class_report(row["id"])
        with pytest.raises(ClassForbidden):
            ClassService().create("New class")


def test_after_retirement_everyone_goes_through_the_market(as_account):
    from deeptutor.publishing.service import PublishService, ScopeError

    _retire()
    set_id = _market_set(as_account)
    with as_account("tina"), pytest.raises(ScopeError):
        PublishService().set_scope("practice_set", set_id, everyone=True, users=[], classes=[])


def test_a_taken_down_item_keeps_its_stored_scope(as_account, teaching_users):
    from deeptutor.publishing import scope as scopes
    from deeptutor.publishing.service import PublishService, ScopeError

    set_id = _market_set(as_account)  # stored: Tina's class
    _list(as_account, "practice_set", set_id)
    with as_account("adam"):
        _listings().take_down("practice_set", set_id, "Wrong.")
    with as_account("tina"), pytest.raises(ScopeError):
        PublishService().set_scope("practice_set", set_id, everyone=False, users=[], classes=[])
    assert scopes._stored_scope("practice_set", set_id).classes  # untouched


def test_an_administrators_taken_down_item_is_reviewed_again(as_account):
    set_id = _market_set(as_account, owner="adam")
    _list(as_account, "practice_set", set_id, owner="adam")
    with as_account("adam"):
        _listings().take_down("practice_set", set_id, "Second look.")
        assert _listings().submit("practice_set", set_id)["status"] == "pending"
        assert _listings().get("practice_set", set_id)["blocked"]


# ── L1, L6 ─────────────────────────────────────────────────


def test_reviews_and_author_pages_show_only_the_market(as_account, teaching_users):
    from deeptutor.catalog.listings import ListingNotFound
    from deeptutor.catalog.market import author_page
    from deeptutor.catalog.reviews import ReviewService
    from deeptutor.multi_user.context import get_current_user

    set_id = _market_set(as_account)
    with as_account("tina"):
        _listings().save("practice_set", set_id, **META)  # a draft listing
    with as_account("sam"):
        with pytest.raises(ListingNotFound):
            ReviewService().list_for("practice_set", set_id)
        with pytest.raises(ListingNotFound):
            author_page(get_current_user(), _uid(teaching_users, "sara"))  # a student, nothing listed


def test_deleting_a_draft_forgets_its_listing(as_account):
    from deeptutor.catalog.listings import status
    from deeptutor.practice_sets.service import PracticeSetService

    with as_account("tina"):
        created = PracticeSetService().create("Draft")
        _listings().save("practice_set", created["id"], **META)
        PracticeSetService().delete(created["id"])
    assert status("practice_set", created["id"]) is None


# ── report privacy ─────────────────────────────────────────


def test_a_report_shows_only_what_happened_on_the_item(as_account, teaching_users):
    from deeptutor.catalog.enrollment import join
    from deeptutor.catalog.reports import LearnerReportService
    from deeptutor.multi_user.context import get_current_user
    from deeptutor.tasks.courses import CourseService
    from deeptutor.tasks.service import TaskService

    _retire()
    with as_account("tina"):
        note = TaskService().create("note", "Read")
        course = CourseService().create("Course")
        CourseService().update(course["id"], task_ids=[note["id"]])
        CourseService().publish(course["id"])
    _list(as_account, "course", course["id"])
    with as_account("sara"):
        join(get_current_user(), "course", course["id"])
    with as_account("tina"):
        [row] = LearnerReportService().report("course", course["id"])["learners"]
    # No study time, question bank or own paths: only what happened on this item.
    assert set(row["overall"]) == {"last_activity", "period_answers", "period_tasks_done"}


# ── direction §11.4: no deadline in the market ─────────────


def test_practice_in_the_market_has_no_deadline(as_account):
    import time

    from deeptutor.catalog.listings import ListingError
    from deeptutor.practice_sets.service import PracticeError, PracticeSetService

    set_id = _market_set(as_account)
    with as_account("tina"):
        PracticeSetService().update(set_id, due_at=time.time() + 86400)
        _listings().save("practice_set", set_id, **META)
        with pytest.raises(ListingError, match="deadline"):
            _listings().submit("practice_set", set_id)
        PracticeSetService().update(set_id, due_at=None)
        _listings().submit("practice_set", set_id)
        with pytest.raises(PracticeError, match="deadline"):
            PracticeSetService().update(set_id, due_at=time.time() + 86400)


def test_administrators_name_entries_in_every_language():
    from deeptutor.catalog import taxonomy

    taxonomy.add_node("primary", "书法", "Calligraphy", {"de": "Kalligrafie", "fr": "Calligraphie"})
    entry = next(s for s in taxonomy.tree()["stages"][0]["subjects"] if s["names"]["en"] == "Calligraphy")
    assert entry["names"]["de"] == "Kalligrafie" and "pl" not in entry["names"]
    taxonomy.update_node("primary.math", name_en="Maths", names={"de": "Rechnen"})
    math = next(s for s in taxonomy.tree()["stages"][0]["subjects"] if s["id"] == "primary.math")
    assert math["names"]["en"] == "Maths" and math["names"]["de"] == "Rechnen" and "fr" not in math["names"]


# ── the migration check (run on a copy before going live) ──


def test_the_migration_check_finds_nothing_lost(as_account, teaching_users):
    from deeptutor.catalog import migration_check
    from deeptutor.classes.service import ClassService
    from deeptutor.practice_sets.service import PracticeSetService
    from deeptutor.publishing import scope as scopes

    _market_set(as_account)  # to Tina's class
    with as_account("tina"):
        everyone = PracticeSetService().create("Everyone")
        PracticeSetService().update(everyone["id"], questions=QUESTIONS)
        with scopes.chosen_scope(scopes.Scope(everyone=True)):
            PracticeSetService().publish(everyone["id"])
    with as_account("adam"):
        [row] = ClassService().list_classes()
        ClassService().set_student_policy(row["id"], {"allow_partners": True})
    result = migration_check.run()
    assert result["classes_retired"] and result["problems"] == 0
    sara = next(p for p in result["people"] if p["username"] == "sara")
    assert sara["ok"] and sara["not_started_everyone_items"] == {"practice_set": [everyone["id"]]}
    assert migration_check.main([]) == 2  # refuses without --on-a-copy


# ── found in the browser check with a stand-in model ───────


def test_a_set_sent_by_name_shows_after_retirement(as_account, teaching_users):
    from deeptutor.practice_sets.service import PracticeSetService
    from deeptutor.publishing import scope as scopes

    _retire()
    from deeptutor.catalog.enrollment import join
    from deeptutor.multi_user.context import get_current_user

    week = _market_set(as_account)
    _list(as_account, "practice_set", week)
    with as_account("sara"):
        join(get_current_user(), "practice_set", week)  # she learns from Tina: Tina may send her a set
    with as_account("tina"):
        remedial = PracticeSetService().create("Remedial: Week 1")
        PracticeSetService().update(remedial["id"], questions=QUESTIONS)
        with scopes.chosen_scope(scopes.Scope(users=[_uid(teaching_users, "sara")])):
            PracticeSetService().publish(remedial["id"])
    with as_account("sara"):
        assert sorted(s["id"] for s in PracticeSetService().assigned()) == sorted([week, remedial["id"]])
    with as_account("sam"):
        assert PracticeSetService().assigned() == []


def test_a_path_learner_who_never_opened_it_counts_in_the_report(
    as_account, teaching_users, teacher_draft, quiet_change_model,
):
    import asyncio

    from deeptutor.catalog.enrollment import join
    from deeptutor.catalog.reports import LearnerReportService
    from deeptutor.multi_user.context import get_current_user
    from deeptutor.teaching.models import PublishRequest
    from deeptutor.teaching.service import TeachingService

    _retire()
    teacher_draft()
    with as_account("tina"):
        service = TeachingService()
        template = service.register("topic_calculus", "")
        preview = asyncio.run(service.preview_publish(template.id))
        asyncio.run(service.publish(template.id, PublishRequest(draft_revision=preview["draft_revision"])))
    _list(as_account, "mastery_path", template.id)
    with as_account("sara"):
        join(get_current_user(), "mastery_path", template.id)  # never opens it
    with as_account("tina"):
        points = LearnerReportService().points("mastery_path", template.id, template.id)
        [row] = LearnerReportService().report("mastery_path", template.id)["learners"]
    assert len(points["points"]) == 3
    assert all(p["not_mastered"] == [_uid(teaching_users, "sara")] for p in points["points"])
    assert row["paths"][0]["kp_total"] == 3 and row["paths"][0]["kp_mastered"] == 0
