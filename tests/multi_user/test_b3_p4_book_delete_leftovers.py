"""B2-19 follow-up: book deletion leftovers carry a marker; only committed ones are swept.

``.book-delete-*`` directories are either garbage of a committed deletion
(crash or failed purge) or, when renaming the book back during a rollback
failed, the only copy of a still-registered book. The marker tells them
apart; a later deletion sweeps only committed leftovers, outside every lock
and off the event loop, skips symlinks and logs the rest.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import pytest

from deeptutor.api.routers import book as router
from deeptutor.book import registry
from deeptutor.book.models import Book
from deeptutor.book.storage import get_book_storage
from deeptutor.knowledge import registry as kbs
from tests.multi_user.test_b3_p4_book_delete import _free
from tests.multi_user.test_book_registry_router import owned, snapshot  # noqa: F401

MARKER = ".deletion.json"


def _leftovers(root: Path) -> list[Path]:
    return sorted(root.parent.glob(".book-delete-*"))


def _state(staging: Path) -> str:
    return json.loads((staging / MARKER).read_text(encoding="utf-8"))["state"]


def _second_book():
    get_book_storage().save_book(Book(id="bk_two", title="Second"))


@pytest.mark.asyncio
async def test_rollback_rename_back_failure_keeps_the_only_copy(owned, as_user, monkeypatch, caplog):
    resolved, _kb = owned
    root = resolved.engine.storage.book_root("bk_one")
    record = registry.get_book("bk_one")
    original_rename = Path.rename

    def broken_unregister(_conn, _book_id):
        raise RuntimeError("synthetic unregister failure")

    def fail_rename_back(path, target):
        if path.name == "original":
            raise OSError("synthetic rename-back failure")
        return original_rename(path, target)

    monkeypatch.setattr(registry, "_unregister", broken_unregister)
    monkeypatch.setattr(Path, "rename", fail_rename_back)
    with as_user("owner", role="teacher"), caplog.at_level(logging.ERROR):
        # The caller sees the original failure, not the restoration's.
        with pytest.raises(RuntimeError, match="synthetic unregister failure"):
            await router.delete_book("bk_one")
    monkeypatch.setattr(Path, "rename", original_rename)
    [staging] = _leftovers(root)
    assert not root.exists()
    assert (staging / "original" / "manifest.json").is_file()
    assert _state(staging) == "restore_failed"
    assert registry.get_book("bk_one") == record
    assert any(str(staging) in message for message in caplog.messages)


@pytest.mark.asyncio
async def test_later_deletion_sweeps_only_committed_leftovers(owned, as_user, monkeypatch, caplog):
    from deeptutor.book import storage

    resolved, _kb = owned
    root = resolved.engine.storage.book_root("bk_one")
    parent = root.parent
    original_rmtree = storage.shutil.rmtree

    # 1. A committed deletion whose purge failed leaves a committed leftover.
    def fail_purge(*_args, **_kwargs):
        raise OSError("synthetic purge failure")

    monkeypatch.setattr(storage.shutil, "rmtree", fail_purge)
    with as_user("owner", role="teacher"):
        assert (await router.delete_book("bk_one"))["deleted"]
    monkeypatch.setattr(storage.shutil, "rmtree", original_rmtree)
    [committed] = _leftovers(root)
    assert _state(committed) == "committed"

    # 2. Leftovers that must never be removed automatically.
    restore_failed = parent / ".book-delete-restore"
    (restore_failed / "original").mkdir(parents=True)
    (restore_failed / "original" / "manifest.json").write_text("{}")
    (restore_failed / MARKER).write_text(json.dumps({
        "book_id": "bk_lost", "base_dir": str(parent), "state": "restore_failed",
    }))
    pending = parent / ".book-delete-pending"
    (pending / "original").mkdir(parents=True)
    (pending / MARKER).write_text(json.dumps({
        "book_id": "bk_pending", "base_dir": str(parent), "state": "pending",
    }))
    unmarked = parent / ".book-delete-unmarked"
    (unmarked / "original").mkdir(parents=True)
    unreadable = parent / ".book-delete-unreadable"
    (unreadable / "original").mkdir(parents=True)
    (unreadable / MARKER).write_text("not json")
    outside = parent.parent / "outside-target"
    outside.mkdir()
    (outside / MARKER).write_text(json.dumps({"book_id": "x", "state": "committed"}))
    link = parent / ".book-delete-link"
    link.symlink_to(outside, target_is_directory=True)
    kept = [restore_failed, pending, unmarked, unreadable]
    kept_before = {path: snapshot(path) for path in kept}

    # 3. A later deletion sweeps the committed one, off the loop and outside the writers.
    loop_thread = threading.current_thread()
    swept = []

    def observe(path, *args, **kwargs):
        if Path(path) == committed:
            swept.append({
                "writers_free": [_free(registry._write_lock), _free(kbs._write_lock)],
                "off_loop": threading.current_thread() is not loop_thread,
            })
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(storage.shutil, "rmtree", observe)
    with as_user("owner", role="teacher"):
        _second_book()
        with caplog.at_level(logging.WARNING):
            assert (await router.delete_book("bk_two"))["deleted"]
    assert swept == [{"writers_free": [True, True], "off_loop": True}]
    assert not committed.exists()
    assert {path: snapshot(path) for path in kept} == kept_before
    assert link.is_symlink() and (outside / MARKER).is_file()
    assert sorted(_leftovers(root)) == sorted([*kept, link])
    log = "\n".join(caplog.messages)
    assert str(link) in log
    restore_line = next(line for line in caplog.messages if str(restore_failed) in line)
    assert "bk_lost" in restore_line and "book_bk_lost" in restore_line
    for path in (pending, unmarked, unreadable):
        assert str(path) in log
