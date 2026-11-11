"""The market's closed loop: progress, finished, the author's view, reply notices (part A);
next to study, trying before joining, favorites that changed (part B).

Spec: docs/superpowers/specs/2026-11-11-market-loop.md
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


def _list(as_account, kind, item_id, owner="tina"):
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


def _detail(as_account, name, kind, item_id):
    from deeptutor.catalog.market import detail
    from deeptutor.multi_user.context import get_current_user

    with as_account(name):
        return detail(get_current_user(), kind, item_id)


def _finished_unreviewed(as_account, name):
    from deeptutor.catalog.progress import finished_unreviewed
    from deeptutor.multi_user.context import get_current_user

    with as_account(name):
        return finished_unreviewed(get_current_user())


def _published_set(as_account, owner="tina", title="Week 1"):
    from deeptutor.practice_sets.service import PracticeSetService

    with as_account(owner):
        created = PracticeSetService().create(title)
        PracticeSetService().update(created["id"], questions=QUESTIONS)
        PracticeSetService().publish(created["id"])
    return created["id"]


def _answer(as_account, name, set_id, choice="B"):
    from deeptutor.practice_sets.service import PracticeSetService

    with as_account(name):
        qid = PracticeSetService().take(set_id)["questions"][0]["question_id"]
        asyncio.run(PracticeSetService().submit(set_id, {qid: choice}))


def _review(as_account, name, kind, item_id, stars=5, text=""):
    from deeptutor.catalog.reviews import ReviewService

    with as_account(name):
        return ReviewService().save(kind, item_id, stars, text)


# ── practice sets ──────────────────────────────────────────


def test_practice_set_progress_and_finished(as_account, teaching_users):
    set_id = _published_set(as_account)
    _list(as_account, "practice_set", set_id)
    assert _detail(as_account, "sara", "practice_set", set_id)["progress"] is None  # not joined
    _join(as_account, "sara", "practice_set", set_id)
    before = _detail(as_account, "sara", "practice_set", set_id)
    assert before["progress"]["finished"] is False and before["progress"]["submissions"] == 0
    assert before["progress"]["unit"] == "submissions" and before["reviewed"] is False and before["can_review"]
    _answer(as_account, "sara", set_id, "A")
    _answer(as_account, "sara", set_id, "B")
    after = _detail(as_account, "sara", "practice_set", set_id)["progress"]
    assert after["finished"] is True and after["submissions"] == 2 and after["percent"] == 100
    assert after["best_score"] == 2 and after["max_score"] == 2
    assert _finished_unreviewed(as_account, "sara") == [{"kind": "practice_set", "id": set_id}]
    _review(as_account, "sara", "practice_set", set_id)
    assert _detail(as_account, "sara", "practice_set", set_id)["reviewed"] is True
    assert _finished_unreviewed(as_account, "sara") == []


# ── courses ────────────────────────────────────────────────


@pytest.fixture
def course(as_account):
    """Tina's listed course: a practice set lesson and a plain instruction."""
    from types import SimpleNamespace

    from deeptutor.tasks.courses import CourseService
    from deeptutor.tasks.service import TaskService

    set_id = _published_set(as_account)
    with as_account("tina"):
        exercise = TaskService().create("practice_set", "Do week 1")
        TaskService().update(exercise["id"], target_id=set_id)
        note = TaskService().create("note", "Read chapter 1")
        created = CourseService().create("Arithmetic course")
        CourseService().update(created["id"], task_ids=[exercise["id"], note["id"]])
        CourseService().publish(created["id"])
    _list(as_account, "course", created["id"])
    return SimpleNamespace(id=created["id"], set_id=set_id, exercise=exercise["id"], note=note["id"])


def test_course_progress_leaves_out_plain_instructions(as_account, teaching_users, course):
    _join(as_account, "sara", "course", course.id)
    _join(as_account, "sam", "course", course.id)
    start = _detail(as_account, "sara", "course", course.id)["progress"]
    assert (start["done"], start["total"], start["unit"], start["finished"]) == (0, 1, "lessons", False)
    _answer(as_account, "sara", course.set_id)
    done = _detail(as_account, "sara", "course", course.id)["progress"]
    assert (done["done"], done["total"], done["finished"]) == (1, 1, True)  # the note does not count
    assert {"kind": "course", "id": course.id} in _finished_unreviewed(as_account, "sara")
    stats = _detail(as_account, "tina", "course", course.id)["stats"]
    assert stats["learners"] == 2 and stats["finished"] == 1 and stats["recent_learners"] == 2


# ── Mastery Paths ──────────────────────────────────────────


def _market_path(as_account, author_draft):
    from deeptutor.teaching.models import PublishRequest
    from deeptutor.teaching.service import TeachingService

    author_draft()
    with as_account("tina"):
        service = TeachingService()
        template = service.register("topic_calculus", "")
        preview = asyncio.run(service.preview_publish(template.id))
        asyncio.run(service.publish(template.id, PublishRequest(draft_revision=preview["draft_revision"])))
    _list(as_account, "mastery_path", template.id)
    return template.id


def test_path_progress_counts_points_and_finishes_when_all_are_mastered(
    as_account, teaching_users, author_draft, quiet_change_model, monkeypatch,
):
    from deeptutor.api.routers import mastery_path

    template_id = _market_path(as_account, author_draft)
    _join(as_account, "sara", "mastery_path", template_id)
    unopened = _detail(as_account, "sara", "mastery_path", template_id)["progress"]
    assert (unopened["done"], unopened["total"], unopened["unit"]) == (0, 3, "points")
    with as_account("sara"):
        asyncio.run(mastery_path.list_topics())  # her copy is made
    opened = _detail(as_account, "sara", "mastery_path", template_id)["progress"]
    assert (opened["done"], opened["total"], opened["finished"]) == (0, 3, False)
    assert _detail(as_account, "tina", "mastery_path", template_id)["stats"]["finished"] == 0

    def all_mastered(progress, **_kw):
        total = sum(len(m.knowledge_points) for m in progress.modules)
        return {"counts": {"mastered": total, "learning": 0, "new": 0, "total": total}}

    monkeypatch.setattr("deeptutor.learning.policy.map_summary", all_mastered)
    mastered = _detail(as_account, "sara", "mastery_path", template_id)["progress"]
    assert (mastered["done"], mastered["finished"], mastered["percent"]) == (3, True, 100)
    assert _detail(as_account, "tina", "mastery_path", template_id)["stats"]["finished"] == 1
    assert _finished_unreviewed(as_account, "sara") == [{"kind": "mastery_path", "id": template_id}]


# ── books ──────────────────────────────────────────────────


@pytest.fixture
def book(teaching_users, tmp_path, monkeypatch, as_account):
    from deeptutor.book import registry as book_registry
    from deeptutor.publishing import scope as scopes

    monkeypatch.setattr(
        "deeptutor.catalog.items._book_chapters", lambda record: [{"id": "c1", "title": "One", "pages": 2}]
    )
    monkeypatch.setattr("deeptutor.catalog.progress._book_page_ids", lambda book_id: {"p1", "p2"})
    record = book_registry.register_book(
        base_dir=tmp_path / "books", dir_name="book_bk_market", title="Vectors",
        created_by=teaching_users["tina"]["id"], visibility="public",
    )
    scopes._store_scope(scopes.BOOK, record.book_id, scopes.Scope(), actor_id=teaching_users["tina"]["id"])
    _list(as_account, "book", record.book_id)
    return record


def _read_pages(as_account, name, book_id, pages):
    from deeptutor.book.learning_overlay import BookLearningOverlay
    from deeptutor.book.models import Progress
    from deeptutor.multi_user.paths import get_current_path_service

    with as_account(name):
        BookLearningOverlay(get_current_path_service()).save_progress(
            Progress(book_id=book_id, visited_page_ids=list(pages))
        )


def test_book_progress_is_pages_read(as_account, teaching_users, book):
    _join(as_account, "sara", "book", book.book_id)
    assert _detail(as_account, "sara", "book", book.book_id)["progress"]["done"] == 0
    _read_pages(as_account, "sara", book.book_id, ["p1", "gone"])
    half = _detail(as_account, "sara", "book", book.book_id)["progress"]
    assert (half["done"], half["total"], half["percent"], half["unit"], half["finished"]) == (1, 2, 50, "pages", False)
    assert _detail(as_account, "tina", "book", book.book_id)["stats"]["finished"] == 0
    _read_pages(as_account, "sara", book.book_id, ["p1", "p2"])
    assert _detail(as_account, "sara", "book", book.book_id)["progress"]["finished"] is True
    assert _detail(as_account, "tina", "book", book.book_id)["stats"]["finished"] == 1


# ── the author's view (§5) ─────────────────────────────────


def test_stats_are_for_the_author_and_administrators_only(as_account, teaching_users):
    from deeptutor.catalog.reviews import ReviewService

    set_id = _published_set(as_account)
    _list(as_account, "practice_set", set_id)
    for name in ("sara", "sam"):
        _join(as_account, name, "practice_set", set_id)
    _answer(as_account, "sara", set_id)
    _review(as_account, "sara", "practice_set", set_id, 4)
    _review(as_account, "sam", "practice_set", set_id, 2)
    with as_account("tina"):
        ReviewService().reply("practice_set", set_id, _uid(teaching_users, "sara"), "Thanks!")
    author = _detail(as_account, "tina", "practice_set", set_id)
    assert author["author_view"] and author["open"]["id"] == set_id and author["progress"] is None
    assert author["stats"] == {
        "learners": 2, "recent_learners": 2, "finished": 1, "rating": 3.0, "rating_count": 2, "unanswered": 1,
    }
    assert _detail(as_account, "adam", "practice_set", set_id)["stats"] == author["stats"]
    for name in ("sara", "tom", "uma"):
        seen = _detail(as_account, name, "practice_set", set_id)
        assert "stats" not in seen and "open" not in seen and not seen["author_view"]


def test_knowledge_bases_have_no_progress_and_no_finished(as_account, teaching_users):
    from deeptutor.catalog import progress

    assert progress.finished_count("knowledge_base", "kb_x", _uid(teaching_users, "tina")) is None
    with as_account("sara"):
        from deeptutor.multi_user.context import get_current_user

        assert progress.mine(get_current_user(), "knowledge_base", "kb_x") is None


def test_the_open_target_of_a_path_is_the_authors_draft(
    as_account, teaching_users, author_draft, quiet_change_model,
):
    template_id = _market_path(as_account, author_draft)
    seen = _detail(as_account, "tina", "mastery_path", template_id)
    assert seen["open"]["path_id"] == "topic_calculus"


# ── reply notifications (§6) ───────────────────────────────


def test_the_reviewer_hears_when_the_author_replies(as_account, teaching_users):
    from deeptutor.catalog.reviews import ReviewService

    set_id = _published_set(as_account)
    _list(as_account, "practice_set", set_id)
    _join(as_account, "sara", "practice_set", set_id)
    _review(as_account, "sara", "practice_set", set_id, 3, "Too short")
    sara = _uid(teaching_users, "sara")

    def replies():
        return [n for n in notifications.list_for(sara) if n["kind"] == "review_replied"]

    with as_account("tina"):
        ReviewService().reply("practice_set", set_id, sara, "")  # empty: nothing to tell
    assert replies() == []
    with as_account("tina"):
        ReviewService().reply("practice_set", set_id, sara, "Added more.")
        ReviewService().reply("practice_set", set_id, sara, "Added more.")  # unchanged
    [note] = replies()
    assert note["params"] == {"content": "practice_set", "title": "Week 1"} and note["count"] == 1
    assert note["link"].startswith(f"/market/item?kind=practice_set&id={set_id}")
    with as_account("tina"):
        ReviewService().reply("practice_set", set_id, sara, "Added two more.")  # changed: raised again
    [again] = replies()
    assert again["count"] == 2 and not again["read"]


# ── next to study (§3) ─────────────────────────────────────


def _list_as(as_account, kind, item_id, owner, **meta):
    from deeptutor.catalog.listings import ListingService

    with as_account(owner):
        ListingService().save(kind, item_id, **{**META, **meta})
        ListingService().submit(kind, item_id)
    with as_account("adam"):
        if ListingService().review_queue():
            ListingService().approve(kind, item_id)


def _set_listed_at(kind, item_id, when):
    from deeptutor.catalog.store import db

    with db.write() as conn:
        conn.execute("UPDATE listings SET listed_at = ? WHERE kind = ? AND item_id = ?", (when, kind, item_id))


def _next(as_account, name, kind, item_id):
    from deeptutor.catalog.market import next_items
    from deeptutor.multi_user.context import get_current_user

    with as_account(name):
        return [e["title"] for e in next_items(get_current_user(), kind, item_id)["items"]]


def test_next_puts_the_author_first_then_one_level_harder_then_popular(as_account, teaching_users):
    def listed(owner, title, when, **meta):
        set_id = _published_set(as_account, owner, title)
        _list_as(as_account, "practice_set", set_id, owner, title=title, **meta)
        _set_listed_at("practice_set", set_id, when)
        return set_id

    here = listed("tina", "Here", 100)
    listed("tina", "Tina old", 101)
    listed("tina", "Tina new", 103)
    listed("tina", "Tina newer", 104, subjects=["primary.chinese"])  # same author, any subject
    listed("tom", "Harder", 105, difficulty="elementary")
    popular = listed("tom", "Popular", 106, difficulty="advanced")
    fairly = listed("tom", "Fairly popular", 107)
    listed("tom", "Quiet", 108)  # same subject, nobody: past the sixth
    joined = listed("tom", "Joined", 109, difficulty="elementary")
    listed("tom", "Other subject", 110, subjects=["primary.chinese"])
    for name in ("sam", "uma"):
        _join(as_account, name, "practice_set", popular)
    _join(as_account, "sam", "practice_set", fairly)
    _join(as_account, "sara", "practice_set", joined)

    assert _next(as_account, "sara", "practice_set", here) == [
        "Tina newer", "Tina new", "Tina old", "Harder", "Popular", "Fairly popular",
    ]
    # Nothing of one's own: Tom sees Tina's items, then the same subject without his.
    assert _next(as_account, "tom", "practice_set", here) == ["Tina newer", "Tina new", "Tina old"]
    # Nothing one joined; the item itself never.
    assert "Joined" not in _next(as_account, "sara", "practice_set", here)
    assert "Here" not in _next(as_account, "uma", "practice_set", here)


def test_next_of_an_item_one_cannot_see_is_not_found(as_account, teaching_users):
    from deeptutor.catalog.listings import ListingNotFound, ListingService

    set_id = _published_set(as_account)
    with pytest.raises(ListingNotFound):
        _next(as_account, "sara", "practice_set", set_id)  # no listing at all
    with as_account("tina"):
        ListingService().save("practice_set", set_id, **META)  # a draft: its author only
    with pytest.raises(ListingNotFound):
        _next(as_account, "sara", "practice_set", set_id)
    assert _next(as_account, "tina", "practice_set", set_id) == []


# ── trying before joining (§4) ─────────────────────────────


TRIAL_BOOK = "bk_trial"


@pytest.fixture
def trial_book(as_account, teaching_users):
    """Tina's listed public book: two chapters, a quiz on the first page."""
    from deeptutor.book import registry as book_registry
    from deeptutor.book.models import Block, BlockStatus, BlockType, Book, Chapter, Page, PageStatus, Spine
    from deeptutor.book.storage import get_book_storage
    from deeptutor.multi_user import book_access
    from deeptutor.publishing import scope as scopes

    with as_account("tina"):
        storage = get_book_storage()
        storage.save_book(Book(id=TRIAL_BOOK, title="Waves", page_count=3, chapter_count=2))
        storage.save_spine(Spine(book_id=TRIAL_BOOK, chapters=[
            Chapter(id="ch_1", title="Nodes", page_ids=["pg_1", "pg_2"], order=0),
            Chapter(id="ch_2", title="Beats", page_ids=["pg_3"], order=1),
        ]))
        for page_id, chapter in (("pg_1", "ch_1"), ("pg_2", "ch_1"), ("pg_3", "ch_2")):
            storage.save_page(Page(
                id=page_id, book_id=TRIAL_BOOK, chapter_id=chapter, title=f"Page {page_id}", status=PageStatus.READY,
                blocks=[
                    Block(id=f"txt_{page_id}", type=BlockType.TEXT, status=BlockStatus.READY,
                          payload={"body": f"Text of {page_id}"}, params={"prompt": "secret prompt"},
                          metadata={"model": "m"}),
                    Block(id=f"quiz_{page_id}", type=BlockType.QUIZ, status=BlockStatus.READY,
                          payload={"questions": [{"question_id": "q1", "question": "?", "question_type": "choice",
                                                  "options": {"A": "a", "B": "b"}, "correct_answer": "B"}]}),
                ],
            ))
        record = book_access.register_current_scope_book(TRIAL_BOOK)
        book_registry.set_book_visibility(TRIAL_BOOK, "public")
        scopes._store_scope(scopes.BOOK, TRIAL_BOOK, scopes.Scope(), actor_id=teaching_users["tina"]["id"])
    _list(as_account, "book", TRIAL_BOOK)
    return record


def _try(as_account, name, chapter=""):
    from deeptutor.catalog.trial import first_chapter
    from deeptutor.multi_user.context import get_current_user

    with as_account(name):
        return first_chapter(get_current_user(), TRIAL_BOOK, chapter)


def test_trial_reads_the_first_chapter_only(as_account, teaching_users, trial_book):
    from deeptutor.catalog.listings import ListingForbidden, ListingNotFound

    seen = _detail(as_account, "sara", "book", TRIAL_BOOK)
    assert seen["can_try"] is True
    assert [c["id"] for c in seen["preview"]["chapters"]] == ["ch_1", "ch_2"]
    trial = _try(as_account, "sara")
    assert trial["chapter"] == {"id": "ch_1", "title": "Nodes"}
    assert [p["id"] for p in trial["pages"]] == ["pg_1", "pg_2"]
    assert [(c["id"], c["trial"]) for c in trial["chapters"]] == [("ch_1", True), ("ch_2", False)]
    block = trial["pages"][0]["blocks"][0]
    assert set(block) == {"id", "type", "status", "title", "payload"}  # no prompts, no run details
    assert "secret prompt" not in str(trial) and "Text of pg_3" not in str(trial)
    assert _try(as_account, "sara", "ch_1")["chapter"]["id"] == "ch_1"
    with pytest.raises(ListingForbidden) as later:
        _try(as_account, "sara", "ch_2")
    assert str(later.value) == "Join to read this chapter."
    with pytest.raises(ListingNotFound):
        _try(as_account, "sara", "ch_none")
    # Who joined, and its author, read the book itself.
    _join(as_account, "sam", "book", TRIAL_BOOK)
    assert _detail(as_account, "sam", "book", TRIAL_BOOK)["can_try"] is False
    assert _detail(as_account, "tina", "book", TRIAL_BOOK)["can_try"] is False


def test_the_trial_reader_still_cannot_use_the_book_reader(as_account, teaching_users, trial_book):
    from fastapi import HTTPException

    from deeptutor.api.routers import book as book_router
    from deeptutor.book import registry as book_registry
    from deeptutor.multi_user.context import get_current_user

    _try(as_account, "sara")  # trying first changes nothing
    calls = [
        lambda: book_router.get_book(TRIAL_BOOK),
        lambda: book_router.get_page(TRIAL_BOOK, "pg_1"),
        lambda: book_router.get_spine(TRIAL_BOOK),
        lambda: book_router.mark_visited(book_router.ProgressRequest(book_id=TRIAL_BOOK, page_id="pg_1")),
        lambda: book_router.toggle_bookmark(book_router.ProgressRequest(book_id=TRIAL_BOOK, page_id="pg_1")),
        lambda: book_router.quiz_attempt(book_router.QuizAttemptRequest(
            book_id=TRIAL_BOOK, page_id="pg_1", block_id="quiz_pg_1", question_id="q1", user_answer="B")),
        lambda: book_router.set_page_chat_session(book_router.PageChatSessionRequest(
            book_id=TRIAL_BOOK, page_id="pg_1", session_id="s1")),
        lambda: book_router.deep_dive(book_router.DeepDiveRequest(
            book_id=TRIAL_BOOK, parent_page_id="pg_1", topic="more")),
    ]
    with as_account("sara"):
        assert not book_registry.can_read_book(trial_book, get_current_user())
        for call in calls:
            with pytest.raises(HTTPException) as denied:
                asyncio.run(call())
            assert denied.value.status_code == 404
    assert _detail(as_account, "sara", "book", TRIAL_BOOK)["progress"] is None


def test_no_trial_once_the_book_leaves_the_market(as_account, teaching_users, trial_book):
    from deeptutor.catalog.listings import ListingNotFound, ListingService

    with as_account("tina"):
        ListingService().unlist("book", TRIAL_BOOK)
    with pytest.raises(ListingNotFound):
        _try(as_account, "sara")
    with as_account("tina"):
        ListingService().relist("book", TRIAL_BOOK)
    assert _try(as_account, "sara")["chapter"]["id"] == "ch_1"
    with as_account("adam"):
        ListingService().take_down("book", TRIAL_BOOK, "Wrong")
    with pytest.raises(ListingNotFound):
        _try(as_account, "sara")


def test_a_never_listed_book_has_no_trial(as_account, teaching_users, trial_book):
    from deeptutor.catalog.listings import ListingNotFound
    from deeptutor.catalog.trial import first_chapter
    from deeptutor.multi_user.context import get_current_user

    with as_account("sara"), pytest.raises(ListingNotFound):
        first_chapter(get_current_user(), "bk_nowhere")


SET_QUESTIONS = [
    {"question": f"Question {n}", "question_type": "choice", "options": {"A": f"a{n}", "B": f"b{n}"},
     "correct_answer": "B", "explanation": f"Because {n}", "points": 3, "rubric": f"Rubric {n}"}
    for n in range(1, 5)
]


def test_a_listed_practice_set_shows_three_questions_without_answers(as_account, teaching_users):
    import json

    from deeptutor.catalog.listings import ListingService
    from deeptutor.practice_sets.service import PracticeSetService

    with as_account("tina"):
        set_id = PracticeSetService().create("Week 2")["id"]
        PracticeSetService().update(set_id, questions=SET_QUESTIONS)
        PracticeSetService().publish(set_id)
    _list(as_account, "practice_set", set_id)
    seen = _detail(as_account, "uma", "practice_set", set_id)
    samples = seen["preview"]["sample_questions"]
    assert samples == [
        {"question": f"Question {n}", "question_type": "choice", "options": {"A": f"a{n}", "B": f"b{n}"}}
        for n in range(1, 4)
    ]
    text = json.dumps(seen)
    assert "correct_answer" not in text and "Because" not in text and "Rubric" not in text
    assert "Question 4" not in text
    # Out of the market: no questions, also for who joined.
    _join(as_account, "sara", "practice_set", set_id)
    with as_account("tina"):
        ListingService().unlist("practice_set", set_id)
    assert "sample_questions" not in _detail(as_account, "sara", "practice_set", set_id)["preview"]


# ── favorites that changed (§7) ────────────────────────────


def _favorite(as_account, name, kind, item_id, on=True):
    from deeptutor.catalog.reviews import ReviewService

    with as_account(name):
        ReviewService().favorite(kind, item_id, on)


def _my_favorites(as_account, name):
    from deeptutor.catalog.market import Filters, search
    from deeptutor.multi_user.context import get_current_user

    with as_account(name):
        return {e["title"]: e["updated"] for e in search(get_current_user(), Filters(favorites=True))["items"]}


def _home_updates(as_account, name):
    from deeptutor.catalog.market import updated_favorites
    from deeptutor.multi_user.context import get_current_user

    with as_account(name):
        return [e["title"] for e in updated_favorites(get_current_user())]


def _add_question(as_account, set_id, count):
    from deeptutor.practice_sets.service import PracticeSetService

    with as_account("tina"):
        PracticeSetService().update(set_id, questions=SET_QUESTIONS[:count])
        PracticeSetService().publish(set_id)


def test_a_favorite_is_updated_when_its_content_changes_until_opened(as_account, teaching_users):
    from deeptutor.catalog.listings import ListingService

    set_id = _published_set(as_account)
    _list(as_account, "practice_set", set_id)
    _favorite(as_account, "uma", "practice_set", set_id)
    _favorite(as_account, "sara", "practice_set", set_id)
    _join(as_account, "sara", "practice_set", set_id)
    assert _my_favorites(as_account, "uma") == {"Arithmetic": False}
    assert _home_updates(as_account, "uma") == []

    with as_account("tina"):  # a new description is not an update
        ListingService().save("practice_set", set_id, **{**META, "summary": "Add more."})
    assert _my_favorites(as_account, "uma") == {"Arithmetic": False}

    _add_question(as_account, set_id, 2)
    assert _my_favorites(as_account, "uma") == {"Arithmetic": True}
    assert _home_updates(as_account, "uma") == ["Arithmetic"]
    # Who joined it hears of changes otherwise.
    assert _my_favorites(as_account, "sara") == {"Arithmetic": False}
    assert _home_updates(as_account, "sara") == []

    _detail(as_account, "uma", "practice_set", set_id)  # opening it: seen
    assert _my_favorites(as_account, "uma") == {"Arithmetic": False}
    assert _home_updates(as_account, "uma") == []


def test_an_old_favorite_without_a_fingerprint_is_backfilled(as_account, teaching_users):
    from deeptutor.catalog.store import db

    set_id = _published_set(as_account)
    _list(as_account, "practice_set", set_id)
    _favorite(as_account, "uma", "practice_set", set_id)
    with db.write() as conn:
        conn.execute("UPDATE favorites SET fingerprint = ''")
    assert _my_favorites(as_account, "uma") == {"Arithmetic": False}
    with db.read() as conn:
        assert conn.execute("SELECT fingerprint FROM favorites").fetchone()[0] == "questions:1"
    _add_question(as_account, set_id, 3)
    assert _home_updates(as_account, "uma") == ["Arithmetic"]


def test_fingerprints_count_the_content_of_each_kind(as_account, teaching_users, course, trial_book):
    from deeptutor.catalog.items import fingerprint

    assert fingerprint("course", course.id) == "lessons:2"
    assert fingerprint("practice_set", course.set_id) == "questions:1"
    assert fingerprint("book", TRIAL_BOOK) == "chapters:2,pages:3"
    assert fingerprint("book", "bk_gone") == ""
