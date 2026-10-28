"""B3-3: the sharing/references GETs read a snapshot without the joint writers."""

from __future__ import annotations

import asyncio
import contextvars
import sqlite3
import threading

from fastapi import HTTPException
import pytest

from deeptutor.api.routers import book as router
from deeptutor.book import registry, sharing
from deeptutor.book.models import Book
from deeptutor.book.storage import get_book_storage
from deeptutor.knowledge import registry as kbs
from deeptutor.multi_user import book_access


@pytest.fixture
def owned(as_user, tmp_path):
    with as_user("owner", role="teacher"):
        kb = kbs.register_kb(
            base_dir=tmp_path / "kbs", name="answers", kind="uploaded", created_by="owner",
        )
        get_book_storage().save_book(Book(id="bk_one", title="Waves", knowledge_bases=[kb.ref]))
        book_access.resolve_book("bk_one")
    registry.replace_book_references("course", "c1", ["bk_one"], created_by="other")
    return kb


def _run_in_thread(call):
    """Run ``call()`` on its own event loop; returns (thread, result box, loop ids)."""
    box: dict[str, object] = {}
    loop_threads: list[int] = []
    context = contextvars.copy_context()

    async def main():
        loop_threads.append(threading.get_ident())
        return await call()

    def target():
        try:
            box["result"] = context.run(asyncio.run, main())
        except BaseException as exc:  # noqa: BLE001 - surfaced by the test
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, box, loop_threads


@pytest.mark.parametrize("endpoint", ["sharing", "references"])
def test_read_endpoints_do_not_wait_for_a_held_joint_writer(owned, as_user, monkeypatch, endpoint):
    connects: list[int] = []
    real_connect = sqlite3.connect

    def recording_connect(*args, **kwargs):
        connects.append(threading.get_ident())
        return real_connect(*args, **kwargs)

    call = router.get_book_sharing if endpoint == "sharing" else router.get_book_references
    with as_user("owner", role="teacher"):
        baseline = asyncio.run(call("bk_one"))
    entered, release = threading.Event(), threading.Event()

    def hold():
        with sharing.shared_write():
            entered.set()
            release.wait(10)

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    assert entered.wait(5)
    try:
        with as_user("owner", role="teacher"):
            monkeypatch.setattr(sqlite3, "connect", recording_connect)
            runner, box, loop_threads = _run_in_thread(lambda: call("bk_one"))
            runner.join(1.0)
            returned_in_time = not runner.is_alive()
    finally:
        release.set()
        holder.join(10)
        runner.join(35)
        monkeypatch.setattr(sqlite3, "connect", real_connect)
    assert returned_in_time, "GET waited for the joint writer"
    assert "error" not in box, box.get("error")
    body = box["result"]
    assert body == baseline
    assert body["book_id"] == "bk_one"
    assert body["other_references"] == {"total": 1, "by_kind": {"course": 1}}
    if endpoint == "sharing":
        assert body["visibility"] == "private" and body["can_set_visibility"] is True
        # The book's reference protects its KB, so the default is reference-only.
        assert body["kbs"]["private_kbs"] == [
            {"kb_id": owned.kb_id, "name": "answers", "default": "reference_only"},
        ]
    assert connects, "expected SQLite reads"
    assert loop_threads[0] not in connects, "SQLite ran on the event loop thread"


@pytest.mark.asyncio
async def test_read_snapshot_opens_existing_files_read_only_and_creates_nothing(owned):
    with sharing.shared_read() as conn:
        assert conn is not None
        assert registry._get_book(conn, "bk_one").title == "Waves"
        for table in ("book_registry", "kb_registry"):
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                conn.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed names


def test_missing_book_database_reads_as_unregistered(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "DB_PATH_OVERRIDE", tmp_path / "absent" / "books.sqlite3")
    monkeypatch.setattr(kbs, "DB_PATH_OVERRIDE", tmp_path / "absent" / "kbs.sqlite3")
    with sharing.shared_read() as conn:
        assert conn is None
    assert not (tmp_path / "absent").exists()


@pytest.mark.asyncio
async def test_missing_kb_database_reads_as_unregistered_and_creates_nothing(owned, as_user):
    """Same 409 as _registered_writable; a read never creates registry files."""
    kb_path = kbs._db_path()
    kb_path.unlink()
    with as_user("owner", role="teacher"):
        for call in (router.get_book_sharing, router.get_book_references):
            with pytest.raises(HTTPException) as conflict:
                await call("bk_one")
            assert conflict.value.status_code == 409
    assert not kb_path.exists()


@pytest.mark.asyncio
async def test_sharing_read_still_denies_strangers(owned, as_user):
    with as_user("stranger"):
        with pytest.raises(HTTPException) as hidden:
            await router.get_book_sharing("bk_one")
        assert hidden.value.status_code == 404
