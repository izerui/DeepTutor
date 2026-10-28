"""B2-19 leftover: book deletion renames inside the writer and purges outside it.

The writer transaction only moves the book directory to a hidden sibling
(O(1)); the tree is removed after COMMIT, in a worker thread, outside both
registry writer locks. A failure before COMMIT renames the directory back.
"""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import HTTPException
import pytest

from deeptutor.api.routers import book as router
from deeptutor.book import registry
from deeptutor.knowledge import registry as kbs
from tests.multi_user.test_book_registry_router import owned, snapshot  # noqa: F401


@pytest.mark.asyncio
async def test_delete_never_copies_the_book_tree(owned, as_user, monkeypatch):
    from deeptutor.book import storage

    resolved, _kb = owned
    root = resolved.engine.storage.book_root("bk_one")

    def no_copy(*_args, **_kwargs):
        raise OSError("synthetic: deletion must not copy the book")

    monkeypatch.setattr(storage.shutil, "copytree", no_copy)
    with as_user("owner", role="teacher"):
        assert (await router.delete_book("bk_one"))["deleted"]
    assert registry.get_book("bk_one") is None
    assert not root.exists()
    assert not list(root.parent.glob(".book-delete-*"))


def _free(lock) -> bool:
    """Whether another thread could take ``lock`` now (an RLock re-enters on its owner)."""
    result = []

    def probe():
        acquired = lock.acquire(blocking=False)
        if acquired:
            lock.release()
        result.append(acquired)

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join()
    return result[0]


@pytest.mark.asyncio
async def test_staged_tree_is_purged_outside_the_writers_and_off_the_loop(
    owned, as_user, monkeypatch,
):
    from deeptutor.book import storage

    resolved, _kb = owned
    root = resolved.engine.storage.book_root("bk_one")
    loop_thread = threading.current_thread()
    original = storage.shutil.rmtree
    purges = []

    def observe_purge(path, *args, **kwargs):
        if Path(path).name.startswith(".book-delete-"):
            purges.append({
                "writers_free": [_free(registry._write_lock), _free(kbs._write_lock)],
                "off_loop": threading.current_thread() is not loop_thread,
                "row_gone": registry.get_book("bk_one") is None,
            })
        return original(path, *args, **kwargs)

    monkeypatch.setattr(storage.shutil, "rmtree", observe_purge)
    with as_user("owner", role="teacher"):
        assert (await router.delete_book("bk_one"))["deleted"]
    assert purges == [{"writers_free": [True, True], "off_loop": True, "row_gone": True}]
    assert not root.exists() and not list(root.parent.glob(".book-delete-*"))


@pytest.mark.asyncio
async def test_unregister_failure_renames_the_book_back(owned, as_user, monkeypatch):
    resolved, _kb = owned
    root = resolved.engine.storage.book_root("bk_one")
    before = snapshot(root)
    record = registry.get_book("bk_one")

    def broken_unregister(_conn, _book_id):
        assert not root.exists(), "the book directory is staged before unregistering"
        raise RuntimeError("synthetic unregister failure")

    monkeypatch.setattr(registry, "_unregister", broken_unregister)
    with as_user("owner", role="teacher"):
        with pytest.raises(RuntimeError, match="synthetic unregister failure"):
            await router.delete_book("bk_one")
    assert snapshot(root) == before
    assert registry.get_book("bk_one") == record
    assert not list(root.parent.glob(".book-delete-*"))
    assert resolved.engine.load_book("bk_one").title == "Waves"


@pytest.mark.asyncio
async def test_unsettled_delete_reports_the_last_rounds_counts(owned, as_user, monkeypatch):
    """P2 follow-up: an exhausted confirmation loop reports real counts, not 0/0."""
    from deeptutor.teaching import access

    rounds = iter([("t1", "t2"), ("t1",), ("t2",)])

    async def always_changing(_book_id, *, force, confirmed):
        raise registry.BookWithdrawalNeedsConfirmation(next(rounds))

    def counts(template_ids, *, resource):
        ids = sorted(template_ids)
        return {
            "code": access.WITHDRAWAL_CONFIRMATION_CODE, "requires_confirmation": True,
            "resource": resource, "affected_paths": len(ids), "affected_students": 3 * len(ids),
            "message": "synthetic",
        }

    monkeypatch.setattr(router, "_delete_book", always_changing)
    monkeypatch.setattr(access, "withdrawal_confirmation_detail", counts)
    assert access.WITHDRAWAL_ROUNDS == 3
    with as_user("owner", role="teacher"):
        with pytest.raises(HTTPException) as unsettled:
            await router.delete_book(
                "bk_one", confirm=True, expected_paths=None, expected_students=None,
            )
    detail = unsettled.value.detail
    assert unsettled.value.status_code == 409
    assert detail["changed"] is True and detail["resource"] == "book"
    assert (detail["affected_paths"], detail["affected_students"]) == (1, 3)
