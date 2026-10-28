"""Reference readers (teaching store) never run under the book read snapshot.

Global lock order is teaching -> KB -> Book. A reader that reads the teaching
store while this process holds SHARED on the book database inverts it.
"""

from __future__ import annotations

import sqlite3

import pytest

from deeptutor.book import registry


def _book_db_is_free() -> bool:
    """True when no connection holds a lock on the book database."""
    conn = sqlite3.connect(registry._db_path(), timeout=0, isolation_level=None)
    try:
        conn.execute("BEGIN EXCLUSIVE")
        conn.execute("ROLLBACK")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


@pytest.fixture
def referenced_private_book(tmp_path):
    book = registry.register_book(
        base_dir=tmp_path / "books", dir_name="book_bk_one", title="One", created_by="t1",
    )
    registry.replace_book_references("course", "c1", [book.ref], created_by="t1")
    return book


@pytest.mark.parametrize("entry", ["single", "batch"])
def test_reference_readers_run_after_the_book_snapshot_closes(
    referenced_private_book, make_user, entry,
):
    student = make_user("s1", role="student")
    observed: list[bool] = []

    def reader(_referrer, _user):
        observed.append(_book_db_is_free())
        return True

    registry.register_book_reference_reader("course", reader)
    if entry == "single":
        assert registry.can_read_book(referenced_private_book, student)
    else:
        [access] = registry.book_access_batch([referenced_private_book], student)
        assert access.readable and not access.writable
    assert observed == [True]


def test_batch_keeps_reader_denials_and_failures_closed(referenced_private_book, make_user):
    student = make_user("s1", role="student")

    def broken(_referrer, _user):
        raise RuntimeError("synthetic reader failure")

    registry.register_book_reference_reader("course", lambda _r, _u: False)
    assert not registry.book_access_batch([referenced_private_book], student)[0].readable
    registry.register_book_reference_reader("course", broken)
    assert not registry.book_access_batch([referenced_private_book], student)[0].readable
    assert not registry.can_read_book(referenced_private_book, student)
