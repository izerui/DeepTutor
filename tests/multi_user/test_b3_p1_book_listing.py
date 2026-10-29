"""B3-2: the library listing reads each public book once and batches registry reads."""

from __future__ import annotations

from collections import Counter

import pytest

from deeptutor.api.routers import book as router
from deeptutor.book import registry, sharing
from deeptutor.book.models import Book
from deeptutor.book.storage import BookStorage, get_book_storage
from deeptutor.multi_user import book_access

N = 10


def _publish_foreign_books(as_user) -> list[str]:
    ids = [f"bk_pub{index:02d}" for index in range(N)]
    with as_user("author", role="teacher"):
        store = get_book_storage()
        for index, book_id in enumerate(ids):
            store.save_book(Book(id=book_id, title=f"Public {index:02d}"))
            book_access.resolve_book(book_id)
            registry.set_book_visibility(book_id, "public")
    return ids


@pytest.mark.asyncio
async def test_listing_loads_each_public_book_once_and_bounds_registry_opens(as_user, monkeypatch):
    ids = _publish_foreign_books(as_user)
    with as_user("viewer"):
        baseline = await router.list_books()
    opens: list[bool] = []
    loads: Counter[str] = Counter()
    real_open, real_load = registry._open, BookStorage.load_book

    def counting_open(path, *, readonly=False):
        opens.append(readonly)
        return real_open(path, readonly=readonly)

    def counting_load(self, book_id):
        loads[book_id] += 1
        return real_load(self, book_id)

    monkeypatch.setattr(registry, "_open", counting_open)
    monkeypatch.setattr(BookStorage, "load_book", counting_load)
    with as_user("viewer"):
        body = await router.list_books()
    print(f"registry opens={len(opens)} load_book={dict(sorted(loads.items()))}")
    assert {loads[book_id] for book_id in ids} == {1}
    assert len(opens) <= N + 5
    # Same payload and order as before counting started.
    assert body == baseline
    assert {b["id"] for b in body["books"]} == set(ids)


def _oracle(book_id: str) -> dict:
    """The one-by-one resolution path (unbatched rules) for the same book."""
    resolved = book_access.resolve_book(f"book:{book_id}")
    return router._book_payload(resolved.engine.load_book(book_id), resolved)


def _without_listing_extras(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in {"reading", "generation"}}


@pytest.mark.asyncio
async def test_listing_payload_keeps_capabilities_and_access_decisions(as_user):
    ids = _publish_foreign_books(as_user)
    with as_user("author", role="teacher"):
        registry.set_book_visibility(ids[0], "private")
    with as_user("leaver", role="teacher"):
        get_book_storage().save_book(Book(id="bk_handed", title="Handed over"))
        book_access.resolve_book("bk_handed")
    sharing.transfer_ownership("leaver", "viewer")
    with as_user("viewer"):
        body = await router.list_books()
        listed = {b["id"]: b for b in body["books"]}
        assert set(listed) == {*ids[1:], "bk_handed"}
        for book_id in listed:
            assert _without_listing_extras(listed[book_id]) == _oracle(book_id)
    for book_id in ids[1:]:
        item = listed[book_id]
        assert item["source"] == "shared" and item["group"] == "public"
        assert item["permission"] == "read" and not item["can_edit"]
        assert not item["can_set_visibility"] and not item["can_make_public"]
    assert listed["bk_handed"]["group"] == "mine" and listed["bk_handed"]["can_edit"] is True
    with as_user("admin", role="admin"):
        admin_listed = {b["id"]: b for b in (await router.list_books())["books"]}
        for book_id in admin_listed:
            assert _without_listing_extras(admin_listed[book_id]) == _oracle(book_id)
    # spec 2026-10-29 D10 (P4): an admin withdraws or deletes others' public books, never edits them.
    for book_id in ids[1:]:
        assert admin_listed[book_id]["can_edit"] is False
        assert admin_listed[book_id]["can_make_public"] is False
        assert admin_listed[book_id]["can_set_visibility"] is True
        assert admin_listed[book_id]["can_delete"] is True
    with as_user("author", role="teacher"):
        own = {b["id"]: b for b in (await router.list_books())["books"]}
    assert own[ids[0]]["source"] == "own" and own[ids[0]]["can_set_visibility"] is True
