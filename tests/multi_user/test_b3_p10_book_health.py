"""B3-P10 (B2-19 follow-up): a withdrawn bound KB is "unavailable", not "updated".

Tina's book is bound to Tom's public KB and has a fingerprint baseline. When
Tom withdraws the KB (makes it private), Tina can no longer read it: the
health report must list it under ``unavailable_kbs``, keep it out of
``changed_kbs`` and leave the stale pages untouched.
"""

from __future__ import annotations

import pytest

from deeptutor.knowledge import registry as kb_registry


def _auth(name: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {name}"}


@pytest.fixture
def toms_public_kb(as_account, make_kb):
    from deeptutor.multi_user.knowledge_access import (
        current_kb_manager,
        list_visible_knowledge_bases,
    )

    with as_account("tom"):
        manager = current_kb_manager()
        make_kb(manager, "optics", text="lenses")
        list_visible_knowledge_bases()
        record = kb_registry.find_by_location(manager.base_dir, "optics")
        assert record is not None
        kb_registry.set_visibility(record.kb_id, "public")
    return kb_registry.get_kb(record.kb_id)


def _tinas_book(as_account, kb_ref: str, *, public: bool) -> None:
    from deeptutor.book.models import (
        Block,
        BlockStatus,
        BlockType,
        Book,
        Page,
        PageStatus,
        SourceAnchor,
        Spine,
    )
    from deeptutor.book.storage import get_book_storage
    from deeptutor.multi_user import book_access

    with as_account("tina"):
        store = get_book_storage()
        store.save_book(Book(id="bk_lens", title="Lenses", knowledge_bases=[kb_ref]))
        store.save_spine(Spine(book_id="bk_lens"))
        store.save_page(
            Page(
                id="pg_lens",
                book_id="bk_lens",
                chapter_id="ch_one",
                title="Lenses",
                status=PageStatus.READY,
                blocks=[
                    Block(
                        id="blk_lens",
                        type=BlockType.TEXT,
                        status=BlockStatus.READY,
                        source_anchors=[
                            SourceAnchor(kind="kb", kb_name=kb_ref, ref="doc.md", snippet="x")
                        ],
                    )
                ],
            )
        )
        resolved = book_access.resolve_book("bk_lens")
        assert resolved is not None and resolved.record is not None
        if public:
            from deeptutor.book import sharing

            sharing.set_visibility(resolved.record.book_id, "public", force=False, kb_choices={})


@pytest.mark.parametrize("public_book", [False, True])
def test_withdrawn_foreign_kb_is_unavailable_not_changed(
    toms_public_kb, as_account, teaching_http, public_book,
):
    from deeptutor.book.storage import get_book_storage

    _tinas_book(as_account, toms_public_kb.ref, public=public_book)
    client = teaching_http.client

    baseline = client.get("/api/books/bk_lens/health", headers=_auth("tina"))
    assert baseline.status_code == 200, baseline.text
    assert baseline.json()["kb_drift"]["has_drift"] is False
    with as_account("tina"):
        book = get_book_storage().load_book("bk_lens")
        assert book is not None and book.kb_fingerprints[toms_public_kb.ref]

    with as_account("tom"):
        kb_registry.set_visibility(toms_public_kb.kb_id, "private")
    if public_book:
        [revoked] = kb_registry.list_unavailable_references("book", "bk_lens")
        assert revoked.kb_id == toms_public_kb.kb_id

    response = client.get("/api/books/bk_lens/health", headers=_auth("tina"))
    assert response.status_code == 200, response.text
    drift = response.json()["kb_drift"]
    assert drift["changed_kbs"] == []
    assert drift["unavailable_kbs"] == [toms_public_kb.ref]
    assert drift["stale_page_ids"] == []
    assert drift["has_drift"] is False
    with as_account("tina"):
        book = get_book_storage().load_book("bk_lens")
        assert book is not None and book.stale_page_ids == []


def test_health_drift_runs_off_the_event_loop(
    toms_public_kb, as_account, teaching_http, monkeypatch,
):
    """The reader polls /health; hashing every raw file must not block the loop."""
    import asyncio

    from deeptutor.book import kb_health

    _tinas_book(as_account, toms_public_kb.ref, public=False)
    on_loop: list[bool] = []
    real = kb_health.mark_drift_on_book

    def recording(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real(*args, **kwargs)

    monkeypatch.setattr(kb_health, "mark_drift_on_book", recording)
    response = teaching_http.client.get("/api/books/bk_lens/health", headers=_auth("tina"))
    assert response.status_code == 200, response.text
    assert on_loop == [False]


def test_reader_sees_revoked_binding_without_drift(toms_public_kb, as_account, teaching_http):
    """A read-only reader gets the registry's revocation, never a recomputed drift."""
    _tinas_book(as_account, toms_public_kb.ref, public=True)
    client = teaching_http.client
    before = client.get("/api/books/bk_lens/health", headers=_auth("sara"))
    assert before.status_code == 200, before.text
    assert before.json()["kb_drift"]["unavailable_kbs"] == []

    with as_account("tom"):
        kb_registry.set_visibility(toms_public_kb.kb_id, "private")

    response = client.get("/api/books/bk_lens/health", headers=_auth("sara"))
    assert response.status_code == 200, response.text
    drift = response.json()["kb_drift"]
    assert drift["cached"] is True
    assert drift["unavailable_kbs"] == [toms_public_kb.ref]
    assert drift["changed_kbs"] == [] and drift["has_drift"] is False
