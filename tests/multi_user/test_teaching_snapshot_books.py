"""Books follow the same sharing rules as knowledge bases (spec 7.8, D20)."""

from __future__ import annotations

import pytest

from deeptutor.book import registry as book_registry
from deeptutor.book.models import Book
from deeptutor.book.storage import get_book_storage
from deeptutor.core.visibility_intent import explicit_choice
from deeptutor.knowledge import registry as kb_registry
from deeptutor.learning.models import TopicSource, TopicSourceKind
from deeptutor.multi_user import book_access as local_books
from deeptutor.multi_user.context import get_current_user
from deeptutor.teaching import books as teaching_books_access
from deeptutor.teaching.models import TemplateBookRef, TemplateSources
from deeptutor.teaching.snapshot import plan_sources


def _book_source(index: int, book_id: str, label: str) -> TopicSource:
    return TopicSource(
        id=f"b{index}",
        kind=TopicSourceKind.BOOK,
        source_id=book_id,
        label=label,
        position=index,
    )


def test_a_public_book_passes(teaching_books, as_account):
    with as_account("tina"):
        plan = plan_sources([_book_source(0, "bk_pub", "Optics")], get_current_user())

    assert plan.issues == []
    assert plan.private_books == []
    assert plan.sources[0].source_id == "book:bk_pub"
    assert plan.books == {"bk_pub": "Optics"}


def test_an_own_private_book_offers_both_choices_with_its_kbs(teaching_books, as_account):
    with as_account("tina"):
        plan = plan_sources([_book_source(0, "bk_own", "Waves")], get_current_user())

    assert plan.issues == []
    assert [(c.key, c.default) for c in plan.private_books] == [("bk_own", "make_public")]
    assert [(c.key, c.via, c.via_name) for c in plan.private_kbs] == [
        (teaching_books.answers.kb_id, "bk_own", "Waves")
    ]


def test_a_republished_book_keeps_its_reference_only_choice(teaching_books, as_account):
    previous = TemplateSources(book_refs=[TemplateBookRef(book_id="bk_own", access="reference_only")])
    with as_account("tina"):
        plan = plan_sources(
            [_book_source(0, "bk_own", "Waves")], get_current_user(), previous=previous
        )

    assert plan.private_books[0].default == "reference_only"


def test_someone_elses_private_book_blocks(teaching_books, as_account):
    with as_account("tina"):
        plan = plan_sources([_book_source(0, "bk_tom", "Tom's notes")], get_current_user())

    assert [issue.code for issue in plan.issues] == ["not_shareable"]


def test_a_missing_book_blocks(teaching_books, as_account):
    with as_account("tina"):
        plan = plan_sources([_book_source(0, "book:bk_gone", "Gone")], get_current_user())

    assert [issue.code for issue in plan.issues] == ["missing_book"]


# ── identity, dependencies and explicit intent (root integration notes) ──


def _write_local(book_id: str, title: str) -> None:
    get_book_storage().save_book(Book(id=book_id, title=title, page_count=0))


def _book_intent(book_id: str) -> str | None:
    with book_registry._read() as conn:
        if conn is None or not explicit_choice(conn, book_id, schema="main"):
            return None
        return conn.execute(
            "SELECT value FROM registry_meta WHERE key = ?", ("visibility_intent_v1:" + book_id,)
        ).fetchone()[0]


def _kb_intent(kb_id: str) -> str | None:
    with kb_registry._read() as conn:
        if conn is None or not explicit_choice(conn, kb_id, schema="main"):
            return None
        return "set"


@pytest.fixture
def collision(teaching_users, as_account):
    """Tom's canonical public ``bk_one``; Tina imported an unrelated local ``bk_one``."""
    with as_account("tom"):
        _write_local("bk_one", "Canonical")
        assert local_books.resolve_book("bk_one").record is not None
        book_registry.set_book_visibility("bk_one", "public")
    with as_account("tina"):
        _write_local("bk_one", "Local import")


def test_a_bare_local_collision_is_not_normalized_into_the_registered_book(
    collision, as_account
):
    draft = _book_source(0, "bk_one", "Local import")
    with as_account("tina"):
        plan = plan_sources([draft], get_current_user())
        local = local_books.resolve_book("bk_one")

        assert [issue.code for issue in plan.issues] == ["not_shareable"]
        assert plan.books == {} and plan.sources == []
        # Personal local editing and reading keep working.
        assert local.source == "own" and local.can_edit and local.record is None
    assert draft.source_id == "bk_one"


def test_an_explicit_canonical_ref_keeps_the_registered_identity_despite_a_local_copy(
    collision, as_account, teaching_users
):
    with as_account("tina"):
        plan = plan_sources([_book_source(0, "book:bk_one", "Canonical")], get_current_user())

    assert plan.issues == []
    assert plan.books == {"bk_one": "Canonical"}
    assert plan.sources[0].source_id == "book:bk_one"
    assert book_registry.get_book("bk_one").created_by == teaching_users["tom"]["id"]


def test_a_bare_local_book_is_registered_to_its_owner_without_consent(
    teaching_users, as_account
):
    with as_account("tina"):
        _write_local("bk_mine", "Mine")
        plan = plan_sources([_book_source(0, "bk_mine", "Mine")], get_current_user())

    assert plan.issues == []
    assert [(c.key, c.default) for c in plan.private_books] == [("bk_mine", "make_public")]
    assert plan.sources[0].source_id == "book:bk_mine"
    record = book_registry.get_book("bk_mine")
    assert record.created_by == teaching_users["tina"]["id"]
    assert record.visibility == "private"
    assert _book_intent("bk_mine") is None


def test_planning_has_no_sharing_side_effects(teaching_books, as_account):
    with as_account("tina"):
        plan_sources([_book_source(0, "bk_own", "Waves")], get_current_user())

    assert _book_intent("bk_own") is None
    assert _kb_intent(teaching_books.answers.kb_id) is None
    assert book_registry.list_book_references("bk_own") == []
    assert kb_registry.list_references(teaching_books.answers.kb_id) == []
    assert book_registry.get_book("bk_own").visibility == "private"


def test_a_revoked_book_dependency_blocks_and_is_not_dropped(
    teaching_books, as_account, teaching_users, tmp_path
):
    shared = kb_registry.register_kb(
        base_dir=tmp_path / "kbs",
        name="shared",
        kind="uploaded",
        created_by=teaching_users["adam"]["id"],
        visibility="public",
    )
    book_registry.set_book_kb_links("bk_pub", {shared.ref: shared.kb_id})
    with as_account("adam"):
        kb_registry.set_visibility(shared.kb_id, "private", force=True)

    with as_account("tina"):
        plan = plan_sources([_book_source(0, "bk_pub", "Optics")], get_current_user())
        unavailable = teaching_books_access.unavailable_dependencies("bk_pub")

    assert [issue.code for issue in plan.issues] == ["book_kb_blocked"]
    assert [(d.kb_id, d.reason) for d in unavailable] == [(shared.kb_id, "revoked")]
    assert teaching_books_access.book_kb_ids("bk_pub") == [shared.kb_id]


def test_a_missing_book_dependency_blocks(teaching_books, as_account):
    book_registry.set_book_kb_links("bk_pub", {"kb:gone": ""})

    with as_account("tina"):
        plan = plan_sources([_book_source(0, "bk_pub", "Optics")], get_current_user())
        unavailable = teaching_books_access.unavailable_dependencies("bk_pub")

    assert [issue.code for issue in plan.issues] == ["book_kb_blocked"]
    assert [(d.ref, d.reason) for d in unavailable] == [("kb:gone", "missing")]


def test_a_private_books_missing_kb_blocks(teaching_books, as_account):
    book_registry.set_book_kb_links(
        "bk_own", {teaching_books.answers.ref: teaching_books.answers.kb_id, "kb:gone": ""}
    )
    with as_account("tina"):
        plan = plan_sources([_book_source(0, "bk_own", "Waves")], get_current_user())

    assert [issue.code for issue in plan.issues] == ["book_kb_blocked"]


def test_snapshot_book_refs_list_every_bound_kb(teaching_books, as_account):
    with as_account("tina"):
        plan = plan_sources([_book_source(0, "bk_own", "Waves")], get_current_user())

    (ref,) = plan.to_sources({}, {"bk_own": "reference_only"}).book_refs
    assert (ref.book_id, ref.access, ref.kb_ids) == (
        "bk_own",
        "reference_only",
        [teaching_books.answers.kb_id],
    )


def test_explicit_reference_only_book_records_private_intent(teaching_books, as_account, teaching_users):
    tina = teaching_users["tina"]["id"]
    with as_account("tina"):
        teaching_books_access.replace_template_references("tpl", ["bk_own"], created_by=tina)
        assert _book_intent("bk_own") is None

        teaching_books_access.replace_template_references(
            "tpl",
            ["bk_own", "bk_pub"],
            created_by=tina,
            explicit_reference_only=["bk_own", "bk_pub", "bk_tom"],
        )

    assert _book_intent("bk_own") == "private"
    assert _book_intent("bk_pub") is None
    assert book_registry.get_book("bk_pub").visibility == "public"
    assert _book_intent("bk_tom") is None
    assert {r.referrer_id for r in book_registry.list_book_references("bk_own")} == {"tpl"}


def test_a_book_registry_failure_is_an_issue_not_an_aborted_plan(
    teaching_books, as_account, monkeypatch
):
    def locked(_ref):
        raise book_registry.BookRegistryError("the book registry is locked")

    monkeypatch.setattr(teaching_books_access, "resolve", locked)
    with as_account("tina"):
        plan = plan_sources(
            [_book_source(0, "bk_own", "Waves"), _book_source(1, "bk_pub", "Optics")],
            get_current_user(),
        )

    assert [(issue.source_id, issue.code) for issue in plan.issues] == [
        ("b0", "not_shareable"),
        ("b1", "not_shareable"),
    ]
    assert "locked" in plan.issues[0].message
