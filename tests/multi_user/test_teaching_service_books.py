"""Publishing a path that cites books (spec 7.8), against plans B and D for real."""

from __future__ import annotations

import pytest

from deeptutor.book import registry as book_registry
from deeptutor.knowledge import registry as kb_registry
from deeptutor.learning.models import TopicSource, TopicSourceKind
from deeptutor.teaching.service import PublishBlocked
from deeptutor.teaching.store import TeachingStore


def _book(book_id: str, label: str) -> TopicSource:
    return TopicSource(
        id=f"src_{book_id}",
        kind=TopicSourceKind.BOOK,
        source_id=book_id,
        label=label,
        position=0,
    )


def test_an_own_private_book_shared_by_reference_stays_private(
    teaching_books, teacher_draft, publish_template
):
    teacher_draft(sources=(_book("bk_own", "Waves"),))

    result = publish_template(
        book_choices={"bk_own": "reference_only"},
        kb_choices={teaching_books.answers.kb_id: "reference_only"},
    )

    template_id = result["template"]["id"]
    assert result["warnings"] == []
    assert book_registry.get_book("bk_own").visibility == "private"
    assert [
        (ref.referrer_kind, ref.referrer_id) for ref in book_registry.list_book_references("bk_own")
    ] == [("mastery_template", template_id)]
    # The book's own KB is now readable through the book.
    assert ("book", "bk_own") in {
        (ref.referrer_kind, ref.referrer_id)
        for ref in kb_registry.list_references(teaching_books.answers.kb_id)
    }
    assert kb_registry.get_kb(teaching_books.answers.kb_id).visibility == "private"
    (book_ref,) = TeachingStore().get_revision(template_id, 1).sources.book_refs
    assert (book_ref.book_id, book_ref.access) == ("bk_own", "reference_only")
    assert book_ref.kb_ids == [teaching_books.answers.kb_id]


def test_an_own_private_book_can_be_made_public_with_its_kbs(
    teaching_books, teacher_draft, publish_template
):
    teacher_draft(sources=(_book("bk_own", "Waves"),))

    publish_template()  # defaults: make the book and its KB public (D13)

    assert book_registry.get_book("bk_own").visibility == "public"
    assert kb_registry.get_kb(teaching_books.answers.kb_id).visibility == "public"


def test_a_public_book_passes_untouched(teaching_books, teacher_draft, publish_template):
    teacher_draft(sources=(_book("bk_pub", "Optics"),))

    result = publish_template()

    (book_ref,) = TeachingStore().get_revision(result["template"]["id"], 1).sources.book_refs
    assert (book_ref.book_id, book_ref.access) == ("bk_pub", "public")
    assert book_registry.get_book("bk_pub").created_by == teaching_books.public.created_by


@pytest.mark.parametrize(
    ("choice", "patched", "failed_step"),
    [
        ("reference_only", "share_book_kbs", "share:book:bk_own"),
        ("make_public", "set_book_visibility", "make_public:book:bk_own"),
    ],
)
def test_a_failed_book_step_aborts_before_the_revision(
    teaching_books, teacher_draft, publish_template, monkeypatch, choice, patched, failed_step
):
    teacher_draft(sources=(_book("bk_own", "Waves"),))

    def broken(*_args, **_kwargs):
        raise book_registry.BookVisibilityError("book registry is busy")

    monkeypatch.setattr(book_registry, patched, broken)

    with pytest.raises(PublishBlocked) as blocked:
        publish_template(students=("sara",), book_choices={"bk_own": choice})

    assert blocked.value.detail["failed_step"] == failed_step
    assert blocked.value.detail["completed_steps"] == ["documents"]
    (template,) = TeachingStore().list_templates()
    assert template.current_revision == 0
    assert TeachingStore().list_assignments(template.id) == []
    assert book_registry.get_book("bk_own").visibility == "private"
    assert book_registry.list_book_references("bk_own") == []
    assert kb_registry.get_kb(teaching_books.answers.kb_id).visibility == "private"


def test_book_steps_report_what_completed_before_them(
    teaching_books, teacher_draft, publish_template, teaching_users, monkeypatch, tmp_path
):
    from deeptutor.knowledge.manager import KnowledgeBaseManager

    tina = teaching_users["tina"]["id"]
    notes = kb_registry.register_kb(
        base_dir=tmp_path / "kbs", name="notes", kind="uploaded", created_by=tina
    )
    raw = tmp_path / "kbs" / "notes" / "raw"
    raw.mkdir(parents=True)
    (raw / "doc.md").write_text("notes", encoding="utf-8")
    monkeypatch.setattr(
        KnowledgeBaseManager, "get_kb_status", lambda self, name: {"status": "ready"}
    )
    teacher_draft(
        sources=(
            TopicSource(
                id="s_notes",
                kind=TopicSourceKind.KNOWLEDGE_BASE,
                source_id=notes.ref,
                label="Notes",
                position=0,
            ),
            _book("bk_own", "Waves"),
        )
    )

    def broken(*_args, **_kwargs):
        raise book_registry.BookVisibilityError("book registry is busy")

    monkeypatch.setattr(book_registry, "share_book_kbs", broken)

    with pytest.raises(PublishBlocked) as blocked:
        publish_template(book_choices={"bk_own": "reference_only"})

    assert blocked.value.detail["failed_step"] == "share:book:bk_own"
    assert blocked.value.detail["completed_steps"] == [
        "documents",
        f"make_public:kb:{notes.kb_id}",
    ]
    (template,) = TeachingStore().list_templates()
    assert template.current_revision == 0


def test_several_sources_citing_one_book_offer_one_choice(
    as_account, teaching_books, teacher_draft, quiet_change_model
):
    import asyncio

    from deeptutor.teaching.service import TeachingService

    second = _book("bk_own", "Waves, chapter 2").model_copy(update={"id": "src_again", "position": 1})
    teacher_draft(sources=(_book("bk_own", "Waves"), second))

    with as_account("tina"):
        service = TeachingService()
        template = service.register("topic_calculus", "")
        preview = asyncio.run(service.preview_publish(template.id))

    assert [item["book_id"] for item in preview["private_books"]] == ["bk_own"]
    assert [item["kb_id"] for item in preview["private_kbs"]] == [teaching_books.answers.kb_id]
