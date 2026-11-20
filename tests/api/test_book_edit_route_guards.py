"""New chapter/edit routes accept ``book:`` refs and audit like other edits (M2, M3)."""

from types import SimpleNamespace

from deeptutor.api.routers import book as book_router
from tests.api.test_book_chapter_crud import _client, _seed


def test_chapter_and_edit_routes_strip_the_book_ref(tmp_path, monkeypatch):
    client, storage = _client(tmp_path, monkeypatch)
    _seed(storage)
    # Registers the personal book, so the explicit ref resolves to it.
    assert client.get("/api/books/bk").status_code == 200

    added = client.post(
        "/api/books/book:bk/chapters", json={"title": "Appendix", "expected_revision": 1}
    )
    assert added.status_code == 200, added.text
    chapter_id = added.json()["chapter"]["id"]
    revision = added.json()["book_revision"]
    renamed = client.patch(
        f"/api/books/book:bk/chapters/{chapter_id}",
        json={"title": "Appendix A", "expected_revision": revision},
    )
    assert renamed.status_code == 200, renamed.text
    revision = renamed.json()["book_revision"]
    moved = client.post(
        f"/api/books/book:bk/chapters/{chapter_id}/move",
        json={"new_position": 0, "expected_revision": revision},
    )
    assert moved.status_code == 200, moved.text
    revision = moved.json()["book_revision"]
    deleted = client.delete(
        f"/api/books/book:bk/chapters/{chapter_id}?expected_revision={revision}"
    )
    assert deleted.status_code == 200, deleted.text

    revision = storage.load_book("bk").revision
    proposal = client.post(
        "/api/books/book:bk/edits/preview",
        json={"action": "rename_page", "page_id": "pg_0", "title": "Renamed"},
    )
    assert proposal.status_code == 200, proposal.text
    edit_id = proposal.json()["id"]
    assert client.get("/api/books/book:bk/edits").json()["edits"][0]["id"] == edit_id
    applied = client.post(
        f"/api/books/book:bk/edits/{edit_id}/apply", json={"expected_revision": revision}
    )
    assert applied.status_code == 200, applied.text
    undone = client.post(
        f"/api/books/book:bk/edits/{edit_id}/undo", json={"expected_revision": revision + 1}
    )
    assert undone.status_code == 200, undone.text
    assert storage.load_page("bk", "pg_0").title == "Page 0"


def _audit(monkeypatch):
    calls = []
    monkeypatch.setattr(
        book_router, "log_admin_action", lambda action, **k: calls.append(("admin", action))
    )
    monkeypatch.setattr(
        book_router, "log_usage", lambda kind, ref, action, summary: calls.append(("usage", action))
    )
    return calls


def _resolved(source, group):
    return SimpleNamespace(
        is_shared=source == "shared", capabilities=lambda: {"group": group}
    )


def test_edit_audit_follows_the_content_mutation_rule(monkeypatch):
    calls = _audit(monkeypatch)
    learner = SimpleNamespace(is_admin=False)
    admin = SimpleNamespace(is_admin=True)

    monkeypatch.setattr(book_router, "get_current_user", lambda: learner)
    book_router._log_book_edit(_resolved("own", "mine"), "bk", "book.edit.apply", "e1")
    assert calls == []  # an author's own work is nobody's audit trail

    book_router._log_book_edit(_resolved("shared", "public"), "bk", "book.edit.apply", "e1")
    assert calls == [("usage", "shared_edit")]

    calls.clear()
    monkeypatch.setattr(book_router, "get_current_user", lambda: admin)
    book_router._log_book_edit(_resolved("own", "mine"), "bk", "book.edit.undo", "e1")
    assert calls == []
    book_router._log_book_edit(_resolved("registry", "public"), "bk", "book.edit.undo", "e1")
    assert calls == [("admin", "book.edit.undo")]
