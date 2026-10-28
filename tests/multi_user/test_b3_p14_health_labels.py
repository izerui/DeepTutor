"""B3-P14 item 1: health response carries KB display labels.

The ``/api/books/{book_id}/health`` response must include a ``kb_labels``
dict that maps each KB ref to its human-readable name so the banner can show
names instead of raw ``kb:<id>`` refs.
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


def _tinas_book(as_account, kb_ref: str) -> None:
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

    with as_account("tina"):
        store = get_book_storage()
        store.save_book(Book(id="bk_labels", title="Labels", knowledge_bases=[kb_ref]))
        store.save_spine(Spine(book_id="bk_labels"))
        store.save_page(
            Page(
                id="pg_labels",
                book_id="bk_labels",
                chapter_id="ch_one",
                title="Labels",
                status=PageStatus.READY,
                blocks=[
                    Block(
                        id="blk_labels",
                        type=BlockType.TEXT,
                        status=BlockStatus.READY,
                        source_anchors=[
                            SourceAnchor(kind="kb", kb_name=kb_ref, ref="doc.md", snippet="x")
                        ],
                    )
                ],
            )
        )


def test_health_response_includes_kb_labels(toms_public_kb, as_account, teaching_http):
    """The health response must carry a ``kb_labels`` map with display names."""
    _tinas_book(as_account, toms_public_kb.ref)
    client = teaching_http.client

    response = client.get("/api/books/bk_labels/health", headers=_auth("tina"))
    assert response.status_code == 200, response.text
    data = response.json()
    assert "kb_labels" in data, "health response must include kb_labels"
    labels = data["kb_labels"]
    # The KB ref (kb:<id>) should map to the KB's display name.
    assert labels.get(toms_public_kb.ref) == "optics", (
        f"Expected label 'optics' for {toms_public_kb.ref}, got {labels}"
    )


def test_health_labels_include_withdrawn_kb(toms_public_kb, as_account, teaching_http):
    """Even after withdrawal, the label must still resolve (name is in registry)."""
    _tinas_book(as_account, toms_public_kb.ref)
    client = teaching_http.client

    # Establish a baseline so we get unavailable_kbs after withdrawal.
    baseline = client.get("/api/books/bk_labels/health", headers=_auth("tina"))
    assert baseline.status_code == 200

    # Withdraw the KB.
    with as_account("tom"):
        kb_registry.set_visibility(toms_public_kb.kb_id, "private")

    response = client.get("/api/books/bk_labels/health", headers=_auth("tina"))
    assert response.status_code == 200, response.text
    data = response.json()
    drift = data["kb_drift"]
    assert toms_public_kb.ref in drift["unavailable_kbs"]
    labels = data["kb_labels"]
    # The withdrawn KB should still have a label.
    assert labels.get(toms_public_kb.ref) == "optics"
