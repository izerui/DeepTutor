"""B3-P10 (B2-19 follow-up): a withdrawn bound KB is "unavailable", not "updated".

Tina's book is bound to Tom's public KB and has a fingerprint baseline. When
Tom withdraws the KB (makes it private), Tina can no longer read it: the
health report must list it under ``unavailable_kbs``, keep it out of
``changed_kbs`` and leave the stale pages untouched.
"""

from __future__ import annotations

from collections.abc import Callable  # noqa: F401  (annotation)

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
    real = kb_health.plan_drift

    def recording(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real(*args, **kwargs)

    monkeypatch.setattr(kb_health, "plan_drift", recording)
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


# ── Fix round 1 ─────────────────────────────────────────────────────────────


def _kb_doc(record):
    from pathlib import Path

    return Path(record.base_dir) / record.dir_name / "raw" / "doc.md"


@pytest.mark.parametrize("public_book", [False, True])
def test_republished_kb_is_available_again_and_its_changes_count(
    toms_public_kb, as_account, teaching_http, public_book,
):
    """A revocation record alone is not unavailability once the KB is readable again."""
    from deeptutor.book.storage import get_book_storage

    _tinas_book(as_account, toms_public_kb.ref, public=public_book)
    client = teaching_http.client
    assert client.get("/api/books/bk_lens/health", headers=_auth("tina")).status_code == 200
    with as_account("tom"):
        kb_registry.set_visibility(toms_public_kb.kb_id, "private")
    withdrawn = client.get("/api/books/bk_lens/health", headers=_auth("tina")).json()
    assert withdrawn["kb_drift"]["unavailable_kbs"] == [toms_public_kb.ref]

    with as_account("tom"):
        kb_registry.set_visibility(toms_public_kb.kb_id, "public")
    if public_book:
        # Nothing clears the book's revocation when the KB owner re-publishes.
        assert kb_registry.list_unavailable_references("book", "bk_lens")
        reader = client.get("/api/books/bk_lens/health", headers=_auth("sara")).json()
        assert reader["kb_drift"]["unavailable_kbs"] == []
    _kb_doc(toms_public_kb).write_text("new lenses", encoding="utf-8")

    drift = client.get("/api/books/bk_lens/health", headers=_auth("tina")).json()["kb_drift"]
    assert drift["unavailable_kbs"] == []
    assert drift["changed_kbs"] == [toms_public_kb.ref]
    assert drift["stale_page_ids"] == ["pg_lens"]
    with as_account("tina"):
        assert get_book_storage().load_book("bk_lens").stale_page_ids == ["pg_lens"]


def test_mark_seen_keeps_the_baseline_of_an_unavailable_kb(
    toms_public_kb, as_account, teaching_http, make_kb,
):
    from deeptutor.book.models import Book
    from deeptutor.book.storage import get_book_storage
    from deeptutor.multi_user.knowledge_access import (
        current_kb_manager,
        list_visible_knowledge_bases,
    )

    with as_account("tom"):
        manager = current_kb_manager()
        make_kb(manager, "waves", text="ripples")
        list_visible_knowledge_bases()
        waves = kb_registry.find_by_location(manager.base_dir, "waves")
        kb_registry.set_visibility(waves.kb_id, "public")
        waves = kb_registry.get_kb(waves.kb_id)
    _tinas_book(as_account, toms_public_kb.ref, public=False)
    with as_account("tina"):
        store = get_book_storage()
        book = store.load_book("bk_lens")
        store.save_book(Book(**{**book.model_dump(), "knowledge_bases": [toms_public_kb.ref, waves.ref]}))
    client = teaching_http.client
    assert client.get("/api/books/bk_lens/health", headers=_auth("tina")).status_code == 200
    with as_account("tina"):
        assert set(get_book_storage().load_book("bk_lens").kb_fingerprints) == {
            toms_public_kb.ref, waves.ref,
        }

    with as_account("tom"):
        kb_registry.set_visibility(toms_public_kb.kb_id, "private")
    _kb_doc(waves).write_text("new ripples", encoding="utf-8")
    drift = client.get("/api/books/bk_lens/health", headers=_auth("tina")).json()["kb_drift"]
    assert drift["changed_kbs"] == [waves.ref]
    assert drift["unavailable_kbs"] == [toms_public_kb.ref]
    seen = client.post("/api/books/bk_lens/refresh-fingerprints?force=true", headers=_auth("tina"))
    assert seen.status_code == 200, seen.text
    with as_account("tina"):
        assert get_book_storage().load_book("bk_lens").kb_fingerprints[toms_public_kb.ref]

    with as_account("tom"):
        kb_registry.set_visibility(toms_public_kb.kb_id, "public")
    _kb_doc(toms_public_kb).write_text("new lenses", encoding="utf-8")
    drift = client.get("/api/books/bk_lens/health", headers=_auth("tina")).json()["kb_drift"]
    assert drift["changed_kbs"] == [toms_public_kb.ref]
    assert drift["unavailable_kbs"] == []


def test_owner_deleted_kb_is_unavailable_on_the_production_path(as_account, teaching_http, make_kb):
    """Behavior lock (risk c): without an injected manager a deleted KB is "no longer
    available" — not "updated" with stale pages — for its own owner too."""
    from deeptutor.multi_user.knowledge_access import (
        current_kb_manager,
        list_visible_knowledge_bases,
    )

    with as_account("tina"):
        manager = current_kb_manager()
        make_kb(manager, "notes", text="mine")
        list_visible_knowledge_bases()
        record = kb_registry.find_by_location(manager.base_dir, "notes")
    _tinas_book(as_account, record.ref, public=False)
    client = teaching_http.client
    assert client.get("/api/books/bk_lens/health", headers=_auth("tina")).status_code == 200
    with as_account("tina"):
        assert current_kb_manager().delete_knowledge_base("notes", confirm=True)
        kb_registry.unregister_kb(record.ref)

    drift = client.get("/api/books/bk_lens/health", headers=_auth("tina")).json()["kb_drift"]
    assert drift["unavailable_kbs"] == [record.ref]
    assert drift["changed_kbs"] == [] and drift["stale_page_ids"] == []


def test_single_user_deleted_kb_is_unavailable_without_injected_manager(tmp_path, make_kb):
    """Single-user (no account context) production path: same classification."""
    from deeptutor.book.kb_health import mark_drift_on_book
    from deeptutor.book.models import Book
    from deeptutor.book.storage import BookStorage
    from deeptutor.multi_user.knowledge_access import current_kb_manager
    from deeptutor.services.path_service import PathService

    manager = current_kb_manager()
    make_kb(manager, "solo", text="alone")
    storage = BookStorage(path_service=PathService(workspace_root=tmp_path / "books"))
    storage.save_book(Book(id="bk_solo", knowledge_bases=["solo"]))
    first = mark_drift_on_book("bk_solo", storage=storage)
    assert first is not None and first.unavailable_kbs == []
    assert storage.load_book("bk_solo").kb_fingerprints["solo"]

    assert manager.delete_knowledge_base("solo", confirm=True)
    report = mark_drift_on_book("bk_solo", storage=storage)
    assert report is not None
    assert report.unavailable_kbs == ["solo"]
    assert report.changed_kbs == [] and not report.has_drift


# ── Fix round 2 ─────────────────────────────────────────────────────────────


def test_polling_with_every_kb_unavailable_does_not_rewrite_the_book(
    toms_public_kb, as_account, teaching_http, monkeypatch,
):
    """No readable KB and no baseline: the self-heal has nothing new to store, so
    repeated polls must not save the manifest (it would race real book edits)."""
    from deeptutor.book.storage import BookStorage

    with as_account("tom"):
        kb_registry.set_visibility(toms_public_kb.kb_id, "private")
    _tinas_book(as_account, toms_public_kb.ref, public=False)
    saves: list[str] = []
    real_save = BookStorage.save_book

    def counting(self, book):
        saves.append(book.id)
        return real_save(self, book)

    monkeypatch.setattr(BookStorage, "save_book", counting)
    for _ in range(3):
        response = teaching_http.client.get("/api/books/bk_lens/health", headers=_auth("tina"))
        assert response.status_code == 200, response.text
        assert response.json()["kb_drift"]["unavailable_kbs"] == [toms_public_kb.ref]
    assert saves == []


# ── Final review I1: the worker thread only computes; the event loop applies ──


def _during_hashing(monkeypatch, action: "Callable[[], None]") -> list[bool]:
    """Run ``action`` (an event-loop book writer stand-in) once, mid-hash."""
    from deeptutor.book import kb_health

    real = kb_health.fingerprint_kb_documents_batch
    fired: list[bool] = []

    def hashing(*args, **kwargs):
        result = real(*args, **kwargs)
        if not fired:
            fired.append(True)
            action()
        return result

    monkeypatch.setattr(kb_health, "fingerprint_kb_documents_batch", hashing)
    return fired


def test_status_written_during_hashing_is_not_overwritten(
    toms_public_kb, as_account, teaching_http, monkeypatch,
):
    from deeptutor.book.models import BookStatus
    from deeptutor.book.storage import get_book_storage

    _tinas_book(as_account, toms_public_kb.ref, public=False)
    with as_account("tina"):
        store = get_book_storage()
        book = store.load_book("bk_lens")
        book.status = BookStatus.COMPILING
        store.save_book(book)

    def finalize() -> None:
        current = store.load_book("bk_lens")
        current.status = BookStatus.READY
        store.save_book(current)

    fired = _during_hashing(monkeypatch, finalize)
    response = teaching_http.client.get("/api/books/bk_lens/health", headers=_auth("tina"))
    assert response.status_code == 200, response.text
    assert fired
    with as_account("tina"):
        saved = get_book_storage().load_book("bk_lens")
    assert saved.status == BookStatus.READY
    assert saved.kb_fingerprints[toms_public_kb.ref]  # the baseline was still merged in


@pytest.mark.parametrize("drift", [False, True])
def test_book_deleted_during_hashing_is_not_recreated(
    toms_public_kb, as_account, teaching_http, monkeypatch, tmp_path, drift,
):
    import shutil

    from deeptutor.book.storage import get_book_storage

    _tinas_book(as_account, toms_public_kb.ref, public=False)
    client = teaching_http.client
    if drift:
        assert client.get("/api/books/bk_lens/health", headers=_auth("tina")).status_code == 200
        _kb_doc(toms_public_kb).write_text("new lenses", encoding="utf-8")
    with as_account("tina"):
        store = get_book_storage()
        root = store.book_root("bk_lens")

    def delete() -> None:
        shutil.move(str(root), str(tmp_path / "staged-delete"))

    fired = _during_hashing(monkeypatch, delete)
    response = client.get("/api/books/bk_lens/health", headers=_auth("tina"))
    assert response.status_code == 200, response.text
    assert fired
    assert not root.exists()
    with as_account("tina"):
        assert "bk_lens" not in get_book_storage().list_book_ids()
