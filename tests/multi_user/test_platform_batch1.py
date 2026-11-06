"""Platform batch 1: listing, review, the market and joining.

Spec: docs/superpowers/specs/2026-11-03-platform-batch1-design.md
"""

from __future__ import annotations

import asyncio

import pytest

from deeptutor.notifications import service as notifications

QUESTIONS = [
    {"question": "2 + 2 = ?", "question_type": "choice", "options": {"A": "3", "B": "4"},
     "correct_answer": "B", "explanation": "", "points": 2},
]

META = dict(
    title="Arithmetic", summary="Add small numbers.", stages=["primary"], subjects=["primary.math"],
    difficulty="beginner", tags=["Addition", "addition", " Numbers "],
)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    async def ungraded(pending):
        return {}

    monkeypatch.setattr("deeptutor.practice_sets.service._judge", ungraded)


def _kinds(user_id):
    return [n["kind"] for n in notifications.list_for(user_id)]


def _market_set(as_account, owner="tina", title="Week 1"):
    """A practice set published for the market: nobody receives it until they join."""
    from deeptutor.practice_sets.service import PracticeSetService
    from deeptutor.publishing import scope as scopes

    with as_account(owner):
        created = PracticeSetService().create(title)
        PracticeSetService().update(created["id"], questions=QUESTIONS)
        with scopes.join_only():
            PracticeSetService().publish(created["id"])
    return created["id"]


def _listings():
    from deeptutor.catalog.listings import ListingService

    return ListingService()


def _list(as_account, kind, item_id, owner="tina", approve=True, **meta):
    with as_account(owner):
        _listings().save(kind, item_id, **{**META, **meta})
        result = _listings().submit(kind, item_id)
    if approve and result["status"] == "pending":
        with as_account("adam"):
            _listings().approve(kind, item_id)
    return result


def _assigned_sets(as_account, name):
    from deeptutor.practice_sets.service import PracticeSetService

    with as_account(name):
        return [s["id"] for s in PracticeSetService().assigned()]


def _search(as_account, name="sara", **filters):
    from deeptutor.catalog.market import Filters, search
    from deeptutor.multi_user.context import get_current_user

    with as_account(name):
        return search(get_current_user(), Filters(**filters))


def _join(as_account, name, kind, item_id):
    from deeptutor.catalog.enrollment import join
    from deeptutor.multi_user.context import get_current_user

    with as_account(name):
        return join(get_current_user(), kind, item_id)


def _leave(as_account, name, kind, item_id):
    from deeptutor.catalog.enrollment import leave
    from deeptutor.multi_user.context import get_current_user

    with as_account(name):
        return leave(get_current_user(), kind, item_id)


# ── taxonomy ───────────────────────────────────────────────


def test_taxonomy_checks_stages_subjects_and_difficulty():
    from deeptutor.catalog import taxonomy

    tree = taxonomy.tree()
    assert [s["id"] for s in tree["stages"]] == [
        "primary", "junior", "senior", "university", "postgrad_exam", "vocational", "interest",
    ]
    assert taxonomy.validate(["university"], ["university.engineering.computer"], "advanced")
    for stages, subjects, difficulty in (
        (["nowhere"], [], ""),
        (["primary"], ["university.engineering"], ""),  # the subject is not under the stage
        (["primary"], ["primary.math"], "expert"),
        (["primary", "junior", "senior", "university"], [], ""),
    ):
        with pytest.raises(taxonomy.TaxonomyError):
            taxonomy.validate(stages, subjects, difficulty)
    assert taxonomy.ancestors("university.engineering.computer") == [
        "university.engineering.computer", "university.engineering",
    ]


# ── publishing for the market ──────────────────────────────


def test_published_for_the_market_reaches_nobody_until_they_join(as_account):
    set_id = _market_set(as_account)
    assert _assigned_sets(as_account, "sara") == []  # not the old "the teacher's classes"


# ── listing states ─────────────────────────────────────────


def test_teacher_listing_is_reviewed_then_joined(as_account, teaching_users):
    set_id = _market_set(as_account)
    pending = _list(as_account, "practice_set", set_id, approve=False)
    assert pending["status"] == "pending"
    assert pending["tags"] == ["Addition", "Numbers"]  # trimmed, one per spelling
    assert "listing_submitted" in _kinds(teaching_users["adam"]["id"])
    assert _search(as_account)["total"] == 0  # not in the market while in review

    with as_account("adam"):
        queue = _listings().review_queue()
        assert [q["id"] for q in queue] == [set_id]
        review = _listings().review_item("practice_set", set_id)
        assert review["preview"]["questions"][0]["correct_answer"] == "B"
        _listings().approve("practice_set", set_id)
    assert "listing_approved" in _kinds(teaching_users["tina"]["id"])

    found = _search(as_account)
    assert [i["id"] for i in found["items"]] == [set_id]
    assert found["items"][0]["joined"] is None
    _join(as_account, "sara", "practice_set", set_id)
    assert _assigned_sets(as_account, "sara") == [set_id]
    assert _assigned_sets(as_account, "sam") == []
    assert _search(as_account)["items"][0]["joined"] == "self"
    assert _search(as_account)["items"][0]["learners"] == 1


def test_reject_needs_a_reason_and_the_author_resubmits(as_account, teaching_users):
    from deeptutor.catalog.listings import ListingConflict, ListingError

    set_id = _market_set(as_account)
    _list(as_account, "practice_set", set_id, approve=False)
    with as_account("adam"):
        with pytest.raises(ListingError):
            _listings().reject("practice_set", set_id, "  ")
        rejected = _listings().reject("practice_set", set_id, "Add an explanation.")
    assert rejected["status"] == "rejected" and rejected["reason"] == "Add an explanation."
    assert "listing_rejected" in _kinds(teaching_users["tina"]["id"])
    with as_account("tina"):
        assert _listings().get("practice_set", set_id)["reason"] == "Add an explanation."
        assert _listings().submit("practice_set", set_id)["status"] == "pending"
        with pytest.raises(ListingConflict):
            _listings().submit("practice_set", set_id)
        assert _listings().cancel("practice_set", set_id)["status"] == "draft"


def test_unlisting_keeps_members_and_relisting_needs_no_review(as_account):
    from deeptutor.catalog.enrollment import EnrollmentNotFound

    set_id = _market_set(as_account)
    _list(as_account, "practice_set", set_id)
    _join(as_account, "sara", "practice_set", set_id)
    with as_account("tina"):
        assert _listings().unlist("practice_set", set_id)["status"] == "unlisted"
    assert _search(as_account)["total"] == 0
    assert _assigned_sets(as_account, "sara") == [set_id]  # she keeps studying it
    with pytest.raises(EnrollmentNotFound):
        _join(as_account, "sam", "practice_set", set_id)
    with as_account("tina"):
        assert _listings().relist("practice_set", set_id)["status"] == "listed"


def test_take_down_cuts_members_off_until_approved_again(as_account, teaching_users):
    from deeptutor.catalog.listings import ListingError

    set_id = _market_set(as_account)
    _list(as_account, "practice_set", set_id)
    _join(as_account, "sara", "practice_set", set_id)
    with as_account("adam"):
        with pytest.raises(ListingError):
            _listings().take_down("practice_set", set_id, "")
        assert _listings().take_down("practice_set", set_id, "Copied content.")["status"] == "taken_down"
    assert "listing_taken_down" in _kinds(teaching_users["tina"]["id"])
    assert _assigned_sets(as_account, "sara") == []
    with as_account("tina"):
        assert _listings().submit("practice_set", set_id)["status"] == "pending"  # reviewed again
    assert _assigned_sets(as_account, "sara") == []
    with as_account("adam"):
        _listings().approve("practice_set", set_id)
    assert _assigned_sets(as_account, "sara") == [set_id]  # back, with her records


def test_administrator_lists_without_review(as_account):
    set_id = _market_set(as_account, owner="adam")
    assert _list(as_account, "practice_set", set_id, owner="adam", approve=False)["status"] == "listed"


def test_who_may_list_and_review(as_account):
    from deeptutor.catalog.listings import ListingError, ListingForbidden, ListingNotFound

    set_id = _market_set(as_account)
    # Spec 2026-11-05: any learner lists their own content, nobody another's.
    with as_account("sara"), pytest.raises(ListingNotFound):
        _listings().save("practice_set", set_id, **META)
    with as_account("tom"), pytest.raises(ListingNotFound):
        _listings().save("practice_set", set_id, **META)
    with as_account("tina"):
        _listings().save("practice_set", set_id, **{**META, "stages": [], "subjects": []})
        with pytest.raises(ListingError):
            _listings().submit("practice_set", set_id)  # a stage is required
        _listings().save("practice_set", set_id, **META)
        _listings().submit("practice_set", set_id)
    with as_account("tom"), pytest.raises(ListingForbidden):
        _listings().approve("practice_set", set_id)


def test_unpublished_content_cannot_be_listed(as_account):
    from deeptutor.catalog.listings import ListingError
    from deeptutor.practice_sets.service import PracticeSetService

    with as_account("tina"):
        created = PracticeSetService().create("Draft")
        _listings().save("practice_set", created["id"], **META)
        with pytest.raises(ListingError, match="Publish"):
            _listings().submit("practice_set", created["id"])


def test_archiving_takes_it_out_of_the_market(as_account):
    from deeptutor.practice_sets.service import PracticeSetService

    set_id = _market_set(as_account)
    _list(as_account, "practice_set", set_id)
    with as_account("tina"):
        PracticeSetService().archive(set_id)
        assert _listings().get("practice_set", set_id)["status"] == "unlisted"


def test_authors_do_not_join_their_own(as_account):
    from deeptutor.catalog.enrollment import EnrollmentError

    set_id = _market_set(as_account)
    _list(as_account, "practice_set", set_id)
    with pytest.raises(EnrollmentError):
        _join(as_account, "tina", "practice_set", set_id)  # her own
    _join(as_account, "tom", "practice_set", set_id)  # batch 2 §6: a teacher may


# ── the market ─────────────────────────────────────────────


def test_filters_counts_keyword_and_sorting(as_account):
    import time

    from deeptutor.catalog.store import db

    first = _market_set(as_account, title="Week 1")
    second = _market_set(as_account, title="Week 2")
    third = _market_set(as_account, title="Week 3")
    _list(as_account, "practice_set", first)
    _list(as_account, "practice_set", second, title="Derivatives", stages=["university"],
          subjects=["university.foundation.calculus"], difficulty="intermediate", tags=["Calculus"])
    _list(as_account, "practice_set", third, title="Unsorted")
    with db.write() as conn:  # an old item moved in by the migration, not classified yet
        conn.execute(
            "UPDATE listings SET stages_json = '[]', subjects_json = '[]', listed_at = ? WHERE item_id = ?",
            (time.time() - 100, third),
        )
    _join(as_account, "sara", "practice_set", second)

    everything = _search(as_account)
    assert everything["total"] == 3
    assert everything["facets"]["stages"] == {"primary": 1, "university": 1}
    assert everything["facets"]["subjects"]["university.foundation"] == 1  # the parent counts its children

    university = _search(as_account, stages=["university"])
    assert [i["id"] for i in university["items"]] == [second]
    # The stage's own counts leave the stage open; the others apply it.
    assert university["facets"]["stages"] == {"primary": 1, "university": 1}
    assert university["facets"]["difficulties"] == {"intermediate": 1}

    both = _search(as_account, stages=["university", "primary"], difficulties=["beginner"])
    assert [i["id"] for i in both["items"]] == [first]
    assert [i["id"] for i in _search(as_account, subjects=["university.foundation"])["items"]] == [second]
    assert [i["id"] for i in _search(as_account, tags=["calculus"])["items"]] == [second]
    assert [i["id"] for i in _search(as_account, q="deriv")["items"]] == [second]
    assert [i["id"] for i in _search(as_account, q="tina")["items"]] != []  # the author's name
    assert _search(as_account, sort="popular")["items"][0]["id"] == second


def test_detail_and_author_page(as_account, teaching_users):
    from deeptutor.catalog.listings import ListingNotFound
    from deeptutor.catalog.market import author_page, detail
    from deeptutor.multi_user.context import get_current_user

    set_id = _market_set(as_account)
    _list(as_account, "practice_set", set_id, approve=False)
    with as_account("sara"):
        with pytest.raises(ListingNotFound):
            detail(get_current_user(), "practice_set", set_id)  # still in review
    with as_account("adam"):
        _listings().approve("practice_set", set_id)
    _join(as_account, "sara", "practice_set", set_id)
    with as_account("sam"):
        shown = detail(get_current_user(), "practice_set", set_id)
        assert shown["can_join"] and shown["joined"] is None
        assert shown["preview"] == {"question_count": 1, "types": {"choice": 1}, "total_points": 2.0}
        assert "questions" not in shown["preview"]  # learners never see the questions here
        page = author_page(get_current_user(), teaching_users["tina"]["id"])
    assert page["name"] == "tina" and page["learners"] == 1
    assert [i["id"] for i in page["items"]] == [set_id]


# ── Mastery Paths ──────────────────────────────────────────


def _market_path(as_account, teacher_draft):
    from deeptutor.teaching.models import PublishRequest
    from deeptutor.teaching.service import TeachingService

    teacher_draft()
    with as_account("tina"):
        service = TeachingService()
        template = service.register("topic_calculus", "")
        preview = asyncio.run(service.preview_publish(template.id))
        asyncio.run(service.publish(
            template.id, PublishRequest(draft_revision=preview["draft_revision"]),
        ))
    return template.id


def _sara_paths(as_account):
    from deeptutor.api.routers import mastery_path

    with as_account("sara"):
        return [t["path_id"] for t in asyncio.run(mastery_path.list_topics())["topics"]]


def test_path_copy_comes_with_joining_and_stays_through_leaving(
    as_account, teacher_draft, quiet_change_model,
):
    from deeptutor.teaching.instances import instance_path_id

    template_id = _market_path(as_account, teacher_draft)
    assert _sara_paths(as_account) == []
    _list(as_account, "mastery_path", template_id)
    _join(as_account, "sara", "mastery_path", template_id)
    assert _sara_paths(as_account) == [instance_path_id(template_id)]
    _leave(as_account, "sara", "mastery_path", template_id)
    assert _sara_paths(as_account) == []
    _join(as_account, "sara", "mastery_path", template_id)
    assert _sara_paths(as_account) == [instance_path_id(template_id)]


# ── books ──────────────────────────────────────────────────


@pytest.fixture
def written(monkeypatch):
    """Books in these tests have one written chapter."""
    monkeypatch.setattr(
        "deeptutor.catalog.items._book_chapters", lambda record: [{"id": "c1", "title": "One", "pages": 1}]
    )


def _market_book(teaching_users, tmp_path):
    from deeptutor.book import registry as book_registry
    from deeptutor.publishing import scope as scopes

    record = book_registry.register_book(
        base_dir=tmp_path / "books", dir_name="book_bk_market", title="Vectors",
        created_by=teaching_users["tina"]["id"], visibility="public",
    )
    scopes._store_scope(scopes.BOOK, record.book_id, scopes.Scope(), actor_id=teaching_users["tina"]["id"])
    return record


def test_anyone_joins_a_book_and_then_reads_it(as_account, teaching_users, tmp_path, written, as_user):
    from deeptutor.book import registry as book_registry
    from deeptutor.multi_user.context import get_current_user

    record = _market_book(teaching_users, tmp_path)
    _list(as_account, "book", record.book_id)
    for name in ("sara", "tom"):
        with as_account(name):
            assert not book_registry.can_read_book(record, get_current_user())
        _join(as_account, name, "book", record.book_id)  # a teacher may join a book
        with as_account(name):
            assert book_registry.can_read_book(record, get_current_user())
    with as_account("adam"):
        _listings().take_down("book", record.book_id, "Wrong subject.")
    with as_account("sara"):
        assert not book_registry.can_read_book(record, get_current_user())


def test_making_a_book_private_takes_it_out(as_account, teaching_users, tmp_path, written):
    from deeptutor.book import registry as book_registry

    record = _market_book(teaching_users, tmp_path)
    _list(as_account, "book", record.book_id)
    book_registry.set_book_visibility(record.book_id, "private", force=True)
    with as_account("tina"):
        assert _listings().get("book", record.book_id)["status"] == "unlisted"


# ── courses ────────────────────────────────────────────────


def _market_course(as_account, set_id):
    from deeptutor.publishing import scope as scopes
    from deeptutor.tasks.courses import CourseService
    from deeptutor.tasks.service import TaskService

    with as_account("tina"):
        task = TaskService().create("practice_set", "Do week 1")
        TaskService().update(task["id"], target_id=set_id)
        course = CourseService().create("Arithmetic course")
        CourseService().update(course["id"], task_ids=[task["id"]])
        with scopes.join_only():
            CourseService().publish(course["id"])
    return course["id"], task["id"]


def _sara_courses(as_account):
    from deeptutor.tasks.courses import assigned_courses

    with as_account("sara"):
        return [c["id"] for c in assigned_courses()]


def test_joining_a_course_brings_its_tasks_and_content(as_account):
    set_id = _market_set(as_account)  # not listed: reached through the course only
    course_id, task_id = _market_course(as_account, set_id)
    _list(as_account, "course", course_id)
    assert _sara_courses(as_account) == []
    _join(as_account, "sara", "course", course_id)
    assert _sara_courses(as_account) == [course_id]
    assert _assigned_sets(as_account, "sara") == [set_id]
    _leave(as_account, "sara", "course", course_id)
    assert _sara_courses(as_account) == []
    assert _assigned_sets(as_account, "sara") == []


def test_a_listed_course_emptied_by_its_author_leaves_the_market(as_account):
    from deeptutor.catalog.enrollment import EnrollmentNotFound
    from deeptutor.tasks.courses import CourseService

    set_id = _market_set(as_account)
    course_id, _task_id = _market_course(as_account, set_id)
    _list(as_account, "course", course_id)
    assert course_id in {e["id"] for e in _search(as_account, "sam")["items"]}
    _join(as_account, "sara", "course", course_id)

    with as_account("tina"):
        CourseService().update(course_id, task_ids=[])  # nothing left to learn
    assert course_id not in {e["id"] for e in _search(as_account, "sam")["items"]}
    with pytest.raises(EnrollmentNotFound):
        _join(as_account, "sam", "course", course_id)


def test_a_course_adding_a_task_hands_it_to_members(as_account):
    from deeptutor.tasks.courses import CourseService
    from deeptutor.tasks.service import TaskService

    set_id = _market_set(as_account)
    course_id, task_id = _market_course(as_account, set_id)
    _list(as_account, "course", course_id)
    _join(as_account, "sara", "course", course_id)
    later = _market_set(as_account, title="Week 2")
    with as_account("tina"):
        task = TaskService().create("practice_set", "Do week 2")
        TaskService().update(task["id"], target_id=later)
        CourseService().update(course_id, task_ids=[task_id, task["id"]])
    assert sorted(_assigned_sets(as_account, "sara")) == sorted([set_id, later])


def test_course_reading_someone_elses_unlisted_book_cannot_be_listed(
    as_account, teaching_users, tmp_path, written,
):
    from deeptutor.book import registry as book_registry
    from deeptutor.catalog.listings import ListingError
    from deeptutor.tasks.courses import CourseService
    from deeptutor.tasks.service import TaskService

    record = book_registry.register_book(
        base_dir=tmp_path / "books", dir_name="book_bk_tom2", title="Tom's optics",
        created_by=teaching_users["tom"]["id"], visibility="public",
    )
    with as_account("tina"):
        task = TaskService().create("book", "Read")
        course = CourseService().create("Optics course")
    from deeptutor.tasks.store import db as task_db

    with task_db.write() as conn:  # chapters are checked against a real spine; stand in for it
        conn.execute("UPDATE tasks SET target_id = ? WHERE id = ?", (record.book_id, task["id"]))
        conn.execute("UPDATE courses SET status = 'published' WHERE id = ?", (course["id"],))
        conn.execute("INSERT INTO course_items VALUES (?, ?, 0)", (course["id"], task["id"]))
    with as_account("tina"):
        _listings().save("course", course["id"], **META)
        with pytest.raises(ListingError, match="not in the market"):
            _listings().submit("course", course["id"])


# ── the migration ──────────────────────────────────────────


def test_everyone_scoped_content_moves_into_the_market(as_account, teaching_users, teaching_books):
    from deeptutor.catalog import migration
    from deeptutor.catalog.enrollment import joined_by
    from deeptutor.practice_sets.service import PracticeSetService
    from deeptutor.publishing import scope as scopes

    with as_account("tina"):
        created = PracticeSetService().create("Everyone")
        PracticeSetService().update(created["id"], questions=QUESTIONS)
    # Content from before the market that reached everyone (it can no longer be chosen).
    scopes._store_scope(scopes.PRACTICE_SET, created["id"], scopes.Scope(everyone=True),
                        actor_id=teaching_users["tina"]["id"])
    with as_account("tina"):
        PracticeSetService().publish(created["id"])
    set_id = created["id"]
    with as_account("sara"):
        qid = PracticeSetService().take(set_id)["questions"][0]["question_id"]
        asyncio.run(PracticeSetService().submit(set_id, {qid: "B"}))
    # (A student's list holds only what they joined or were named in, so the
    # set is not listed for Sam even before the migration.)

    assert migration.run() == 2  # the set, and Tom's public book that never had a scope
    assert migration.run() == 0
    with as_account("tina"):
        listing = _listings().get("practice_set", set_id)
    assert listing["status"] == "listed" and listing["migrated"] and listing["needs_classification"]
    assert joined_by(teaching_users["sara"]["id"]) == {("practice_set", set_id): "migrated"}
    assert _assigned_sets(as_account, "sara") == [set_id]
    assert _assigned_sets(as_account, "sam") == []  # never started: not joined
    assert not scopes._stored_scope(scopes.PRACTICE_SET, set_id).everyone
    with as_account("tom"):
        assert _listings().get("book", teaching_books.public.book_id)["status"] == "listed"
    # In the market, but under no stage until classified.
    found = _search(as_account, name="sam")
    assert {i["id"] for i in found["items"]} == {set_id, teaching_books.public.book_id}
    assert found["facets"]["stages"] == {}


# ── accounts ───────────────────────────────────────────────


def test_deleted_learner_leaves_everything(as_account, teaching_users):
    from deeptutor.catalog.enrollment import forget_user, joined_by

    set_id = _market_set(as_account)
    _list(as_account, "practice_set", set_id)
    _join(as_account, "sara", "practice_set", set_id)
    forget_user(teaching_users["sara"]["id"])
    assert joined_by(teaching_users["sara"]["id"]) == {}


# ── the routes ─────────────────────────────────────────────


@pytest.fixture
def catalog_http(teaching_users, monkeypatch):
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    import deeptutor.api.routers.auth as auth_router
    from deeptutor.api.routers import catalog
    from deeptutor.services.auth import TokenPayload

    tokens = {
        name: TokenPayload(username=name, role=record["role"], user_id=record["id"])
        for name, record in teaching_users.items()
    }
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "decode_token", lambda token: tokens.get(token))
    app = FastAPI()
    app.include_router(catalog.router, prefix="/api/catalog", dependencies=[Depends(auth_router.require_auth)])
    client = TestClient(app)

    def call(method, name, url, **kwargs):
        return client.request(method, url, headers={"Authorization": f"Bearer {name}"}, **kwargs)

    return call


def test_routes_from_listing_to_joining(as_account, catalog_http):
    set_id = _market_set(as_account)
    base = f"/api/catalog/listings/practice_set/{set_id}"
    assert catalog_http("GET", "tina", base).json()["status"] == "none"
    assert catalog_http("PUT", "sara", base, json=META).status_code == 404  # not hers
    assert catalog_http("PUT", "tina", base, json=META).status_code == 200
    assert catalog_http("POST", "tina", f"{base}/submit").json()["status"] == "pending"
    assert catalog_http("GET", "tina", "/api/catalog/review").status_code == 403
    assert [r["id"] for r in catalog_http("GET", "adam", "/api/catalog/review").json()["listings"]] == [set_id]
    reject = catalog_http("POST", "adam", f"/api/catalog/review/practice_set/{set_id}/reject", json={"reason": ""})
    assert reject.status_code == 400
    assert catalog_http("POST", "adam", f"/api/catalog/review/practice_set/{set_id}/approve").status_code == 200

    found = catalog_http("GET", "sara", "/api/catalog/items", params={"stage": "primary,junior", "kind": "practice_set"})
    assert [i["id"] for i in found.json()["items"]] == [set_id]
    assert catalog_http("GET", "sara", "/api/catalog/items", params={"sort": "oldest"}).status_code == 422
    joined = catalog_http("POST", "sara", f"/api/catalog/items/practice_set/{set_id}/join")
    assert joined.json()["joined"] is True
    detail = catalog_http("GET", "sara", f"/api/catalog/items/practice_set/{set_id}").json()
    assert detail["joined"] == "self" and detail["study"] == {"kind": "practice_set", "id": set_id}
    assert catalog_http("GET", "sara", "/api/catalog/taxonomy").json()["difficulties"][0]["id"] == "beginner"
    mine = catalog_http("GET", "tina", "/api/catalog/listings/mine").json()["listings"]
    assert mine[0]["learners"] == 1
