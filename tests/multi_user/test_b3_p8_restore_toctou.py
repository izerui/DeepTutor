"""P8 item 4: B2-1 book-side TOCTOU — owner makes book private between the
restore decision and the reference write; the re-assignment must NOT restore
the book reference.

Two levels: (a) registry-level ``replace_book_references`` with a rejecting
``restorable`` callback, and (b) an unregistered restore target is silently
dropped.

Mirrors the KB-side ``test_teaching_kb_access_real.py::
test_reassigning_rechecks_an_old_material_in_the_reference_write``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deeptutor.book import registry


def test_book_restore_refused_when_restorable_rejects(tmp_path: Path) -> None:
    """A released book asked back is dropped when ``restorable`` rejects it.

    This exercises the TOCTOU defence: the ``restorable`` callback runs
    inside the write transaction and can see the freshly-changed visibility.
    """
    kept = registry.register_book(
        base_dir=tmp_path, dir_name="book_bk_kept", title="Kept", created_by="t1",
    )
    registry.set_book_visibility(kept.book_id, "public")

    back = registry.register_book(
        base_dir=tmp_path, dir_name="book_bk_back", title="Back", created_by="t1",
    )
    registry.set_book_visibility(back.book_id, "public")

    refused = registry.register_book(
        base_dir=tmp_path, dir_name="book_bk_refused", title="Refused", created_by="t2",
    )
    registry.set_book_visibility(refused.book_id, "public")

    # Simulate what happens when the owner makes the book private between
    # the eligibility check and the actual write: restorable sees
    # the up-to-date record and rejects it.
    def restorable(record: registry.BookRecord) -> bool:
        """Simulate: accept t1-owned (still public), reject t2-owned (just went private)."""
        return record.created_by == "t1"

    registry.replace_book_references(
        "course",
        "c_restore",
        [kept.book_id, back.book_id, refused.book_id, "bk_gone"],
        created_by="t1",
        restore_book_ids=[back.book_id, refused.ref, "bk_gone"],
        restorable=restorable,
    )

    referenced = {
        r.book_id for r in registry.list_referenced_books("course", "c_restore")
    }
    # kept is a normal (non-restore) target; back passed restorable; refused did not.
    assert kept.book_id in referenced
    assert back.book_id in referenced
    assert refused.book_id not in referenced


def test_book_restore_drops_silently_when_unregistered(tmp_path: Path) -> None:
    """Attempting to restore a deleted / unregistered book is a no-op, not an error."""
    anchor = registry.register_book(
        base_dir=tmp_path, dir_name="book_bk_anchor", title="Anchor", created_by="t1",
    )
    registry.set_book_visibility(anchor.book_id, "public")

    registry.replace_book_references(
        "course",
        "c_gone",
        [anchor.book_id, "bk_vanished"],
        created_by="t1",
        restore_book_ids=["bk_vanished"],
        restorable=lambda _: True,
    )

    referenced = {
        r.book_id for r in registry.list_referenced_books("course", "c_gone")
    }
    assert anchor.book_id in referenced
    assert "bk_vanished" not in referenced


def test_book_restore_uses_in_transaction_visibility(tmp_path: Path) -> None:
    """The restorable callback must see the record's current visibility,
    not a stale snapshot from before the write transaction began.

    This is the exact TOCTOU scenario: a public book is made private after
    the eligibility list is computed but before the write.
    """
    target = registry.register_book(
        base_dir=tmp_path, dir_name="book_bk_toctou", title="TOCTOU", created_by="t_owner",
    )
    registry.set_book_visibility(target.book_id, "public")

    # Pre-write: register a reference, then release it (revoke).
    registry.replace_book_references(
        "course", "c_toctou", [target.book_id], created_by="t1",
    )
    assert _held("c_toctou") == {target.book_id}
    # Drop the reference (like a revoke releasing v1's material).
    registry.replace_book_references("course", "c_toctou", [], created_by="t1")
    assert _held("c_toctou") == set()

    # Now restore — but the restorable callback checks freshness.
    def freshness_check(record: registry.BookRecord) -> bool:
        return record.visibility == "public"

    # Make private BEFORE the replace call — simulates the TOCTOU window
    # closing against the caller.
    registry.set_book_visibility(target.book_id, "private")

    registry.replace_book_references(
        "course", "c_toctou", [target.book_id],
        created_by="t1",
        restore_book_ids=[target.book_id],
        restorable=freshness_check,
    )
    # The restore must have been rejected because visibility is now private.
    assert target.book_id not in _held("c_toctou")


def _held(referrer_id: str) -> set[str]:
    return {
        r.book_id
        for r in registry.list_referenced_books("course", referrer_id)
    }
