"""Reading a book's first chapter before joining it (spec 2026-11-11 §4).

Read only and kept apart from the book reader: the book's own read rules do
not change, so every reader route — pages, progress, bookmarks, quizzes, the
book's AI — still turns a trial reader away. This serves the first chapter's
pages of a listed, public book, and nothing else.
"""

from __future__ import annotations

from typing import Any

from deeptutor.catalog.listings import ListingForbidden, ListingNotFound
from deeptutor.catalog.store import db
from deeptutor.multi_user.models import CurrentUser
from deeptutor.publishing import scope as scopes

JOIN_TO_READ = "Join to read this chapter."

#: What a reader needs of a block to show it; generator inputs, sources and run details stay behind.
_BLOCK_FIELDS = ("id", "type", "status", "title", "payload")
_PAGE_FIELDS = ("id", "chapter_id", "title", "order", "status")


def _listed_book(item_id: str):
    """The book's registry record when it is in the market now; ``None`` otherwise."""
    from deeptutor.book import registry as book_registry

    with db.read() as conn:
        row = conn.execute(
            "SELECT * FROM listings WHERE kind = ? AND item_id = ?", (scopes.BOOK, item_id)
        ).fetchone() if conn else None
    if row is None or row["status"] != "listed":
        return None
    if "blocked" in row.keys() and row["blocked"]:
        return None  # taken down
    record = book_registry.get_book(item_id)
    if record is None or record.visibility != "public":
        return None  # made private (closed) by its author
    return record


def _page(page) -> dict[str, Any]:
    data = page.model_dump(mode="json")
    out = {key: data.get(key) for key in _PAGE_FIELDS}
    out["blocks"] = [{key: block.get(key) for key in _BLOCK_FIELDS} for block in data.get("blocks") or []]
    return out


def first_chapter(viewer: CurrentUser, item_id: str, chapter_id: str = "") -> dict[str, Any]:
    """The first chapter of a listed book, its pages read only; other chapters are for who joined.

    Nothing is recorded: no reading progress, no bookmark, no answer.
    """
    del viewer  # anyone who sees the introduction of a listed book may try it
    record = _listed_book(item_id)
    if record is None:
        raise ListingNotFound("Not found.")
    from deeptutor.multi_user.book_access import storage_for

    storage = storage_for(record)
    spine = storage.load_spine(item_id)
    chapters = sorted(spine.chapters, key=lambda c: c.order) if spine is not None else []
    readable = [chapter for chapter in chapters if chapter.page_ids]
    if not readable:
        raise ListingNotFound("Not found.")
    first = readable[0]
    if chapter_id and chapter_id != first.id:
        if any(chapter.id == chapter_id for chapter in chapters):
            raise ListingForbidden(JOIN_TO_READ)
        raise ListingNotFound("Not found.")
    pages = []
    for page_id in first.page_ids:
        page = storage.load_page(item_id, page_id)
        if page is not None:
            pages.append(_page(page))
    book = storage.load_book(item_id)
    return {
        "book": {"id": item_id, "title": record.title, "language": book.language if book else ""},
        "chapter": {"id": first.id, "title": first.title},
        "chapters": [{"id": chapter.id, "title": chapter.title, "trial": chapter.id == first.id} for chapter in chapters],
        "pages": pages,
    }


def trial_page_storage(item_id: str, page_id: str):
    """The book's store when ``page_id`` is in the trial chapter of a listed book.

    Lets a visitor play the media of the blocks :func:`first_chapter` shows
    them (spec 2026-11-20 §6); ``None`` for every other page or book.
    """
    record = _listed_book(item_id)
    if record is None:
        return None
    from deeptutor.multi_user.book_access import storage_for

    storage = storage_for(record)
    spine = storage.load_spine(item_id)
    chapters = sorted(spine.chapters, key=lambda c: c.order) if spine is not None else []
    readable = [chapter for chapter in chapters if chapter.page_ids]
    if not readable or page_id not in readable[0].page_ids:
        return None
    return storage


__all__ = ["JOIN_TO_READ", "first_chapter", "trial_page_storage"]
