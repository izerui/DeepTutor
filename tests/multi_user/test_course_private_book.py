"""Audit fix A: a course lesson's book must be one the course's learners can open.

A private book — its author's own included — keeps the course from being
published, put forward for the market, put back, or joined.
"""

from __future__ import annotations

import pytest

META = dict(
    title="Optics course", summary="Light and lenses.", stages=["primary"], subjects=["primary.math"],
    difficulty="beginner", tags=["Optics"],
)
PRIVATE_MESSAGE = "Make the book “Tina's optics” public first: learners of this course cannot open it."


@pytest.fixture
def written(monkeypatch):
    """Books in these tests have one written chapter."""
    monkeypatch.setattr(
        "deeptutor.catalog.items._book_chapters", lambda record: [{"id": "c1", "title": "One", "pages": 1}]
    )


def _listings():
    from deeptutor.catalog.listings import ListingService

    return ListingService()


def _book(teaching_users, tmp_path, visibility):
    from deeptutor.book import registry as book_registry

    return book_registry.register_book(
        base_dir=tmp_path / "books", dir_name="book_bk_tina_optics", title="Tina's optics",
        created_by=teaching_users["tina"]["id"], visibility=visibility,
    )


def _course_reading(as_account, book_id, *, published: bool):
    """Tina's course with one book lesson; chapters are stood in for (no real spine)."""
    from deeptutor.tasks.courses import CourseService
    from deeptutor.tasks.service import TaskService
    from deeptutor.tasks.store import db as task_db

    with as_account("tina"):
        task = TaskService().create("book", "Read")
        course = CourseService().create("Optics course")
    with task_db.write() as conn:
        conn.execute("UPDATE tasks SET target_id = ? WHERE id = ?", (book_id, task["id"]))
        conn.execute("INSERT INTO course_items VALUES (?, ?, 0)", (course["id"], task["id"]))
        if published:
            conn.execute("UPDATE tasks SET status = 'published' WHERE id = ?", (task["id"],))
            conn.execute("UPDATE courses SET status = 'published' WHERE id = ?", (course["id"],))
    return course["id"], task["id"]


def test_a_course_reading_its_authors_private_book_is_not_ready(as_account, teaching_users, tmp_path, written):
    from deeptutor.catalog import items

    record = _book(teaching_users, tmp_path, "private")
    course_id, _ = _course_reading(as_account, record.book_id, published=True)
    assert items.info("course", course_id).not_ready == PRIVATE_MESSAGE


def test_publishing_a_course_reading_a_private_book_is_refused(as_account, teaching_users, tmp_path, written):
    from deeptutor.tasks.courses import CourseService
    from deeptutor.tasks.service import TaskError

    record = _book(teaching_users, tmp_path, "private")
    course_id, _ = _course_reading(as_account, record.book_id, published=False)
    with as_account("tina"):
        with pytest.raises(TaskError) as refused:
            CourseService().publish(course_id)
        assert str(refused.value) == PRIVATE_MESSAGE
        assert CourseService().get(course_id)["status"] == "draft"


def test_a_book_lesson_cannot_be_published_on_a_private_book(as_account, teaching_users, tmp_path):
    from deeptutor.tasks.service import TaskError, TaskService

    record = _book(teaching_users, tmp_path, "private")
    with as_account("tina"):
        with pytest.raises(TaskError) as refused:
            TaskService()._check_target("book", record.book_id, {"chapter_ids": ["c1"]})
    assert str(refused.value) == PRIVATE_MESSAGE


def test_submitting_a_course_reading_a_private_book_is_refused(as_account, teaching_users, tmp_path, written):
    from deeptutor.catalog.listings import ListingError

    record = _book(teaching_users, tmp_path, "private")
    course_id, _ = _course_reading(as_account, record.book_id, published=True)
    with as_account("tina"):
        _listings().save("course", course_id, **META)
        with pytest.raises(ListingError) as refused:
            _listings().submit("course", course_id)
    assert str(refused.value) == PRIVATE_MESSAGE


def test_a_public_book_lets_the_course_be_listed_and_joined(as_account, teaching_users, tmp_path, written):
    from deeptutor.catalog import enrollment, items

    record = _book(teaching_users, tmp_path, "public")
    course_id, _ = _course_reading(as_account, record.book_id, published=True)
    assert items.info("course", course_id).not_ready == ""
    with as_account("tina"):
        _listings().save("course", course_id, **META)
        result = _listings().submit("course", course_id)
    if result["status"] == "pending":
        with as_account("adam"):
            _listings().approve("course", course_id)
    with as_account("sara"):
        from deeptutor.multi_user.context import get_current_user

        assert enrollment.join(get_current_user(), "course", course_id)["joined"]


def test_a_listed_course_whose_book_turns_private_cannot_be_joined_or_put_back(
    as_account, teaching_users, tmp_path, written,
):
    from deeptutor.book import registry as book_registry
    from deeptutor.catalog import enrollment
    from deeptutor.catalog.enrollment import EnrollmentNotFound
    from deeptutor.catalog.listings import ListingError
    from deeptutor.multi_user.context import get_current_user

    record = _book(teaching_users, tmp_path, "public")
    course_id, _ = _course_reading(as_account, record.book_id, published=True)
    with as_account("tina"):
        _listings().save("course", course_id, **META)
        result = _listings().submit("course", course_id)
    if result["status"] == "pending":
        with as_account("adam"):
            _listings().approve("course", course_id)
    with as_account("tina"):
        _listings().unlist("course", course_id)

    book_registry.set_book_visibility(record.book_id, "private", force=True)
    with as_account("tina"):
        with pytest.raises(ListingError) as refused:
            _listings().relist("course", course_id)
    assert str(refused.value) == PRIVATE_MESSAGE
    with as_account("sara"):
        with pytest.raises(EnrollmentNotFound):
            enrollment.join(get_current_user(), "course", course_id)
