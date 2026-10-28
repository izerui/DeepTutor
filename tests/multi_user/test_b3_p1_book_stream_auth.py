"""B3-1: the book socket re-checks authority at most once per TTL, off the loop."""

from __future__ import annotations

import asyncio
import threading

import pytest

from deeptutor.api.routers import book as router
from deeptutor.book import registry
from deeptutor.book.event_hub import get_book_stream
from deeptutor.book.models import Book
from deeptutor.book.storage import get_book_storage
from deeptutor.multi_user import book_access

TTL = 1.0


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(router, "_stream_auth_clock", fake, raising=False)
    return fake


@pytest.fixture
def shared_book(as_user):
    with as_user("owner", role="teacher"):
        get_book_storage().save_book(Book(id="bk_one", title="Waves"))
        record = book_access.resolve_book("bk_one").record
        registry.set_book_visibility(record.book_id, "public")
    return record


async def _until(predicate, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            return False
        await asyncio.sleep(0.005)
    return True


async def _settle(rounds=20):
    for _ in range(rounds):
        await asyncio.sleep(0.005)


def _kinds(frames):
    return [frame["metadata"]["kind"] for frame in frames]


@pytest.mark.asyncio
async def test_events_within_ttl_reuse_one_off_loop_check(shared_book, as_user, clock, monkeypatch):
    calls: list[int] = []
    real_get_book = registry.get_book

    def counting_get_book(book_id):
        calls.append(threading.get_ident())
        return real_get_book(book_id)

    frames = []

    async def send(frame):
        frames.append(frame)

    with as_user("reader"):
        resolved = book_access.resolve_book(shared_book.ref)
        monkeypatch.setattr(registry, "get_book", counting_get_book)
        fanout = router._SocketFanout(send)
        fanout.attach_book(shared_book.book_id, resolved)
        stream = get_book_stream(shared_book.book_id, storage=resolved.engine.storage)
        try:
            for index in range(50):
                await stream.book_event(f"e{index}", {})
            assert await _until(lambda: len(frames) == 50)
        finally:
            await fanout.close()
    assert _kinds(frames) == [f"e{index}" for index in range(50)]
    assert 1 <= len(calls) <= 2, f"{len(calls)} registry reads for 50 events"
    assert threading.get_ident() not in calls, "authorization ran on the event loop thread"


@pytest.mark.asyncio
async def test_made_private_stops_forwarding_once_the_ttl_has_passed(
    shared_book, as_user, clock,
):
    frames = []

    async def send(frame):
        frames.append(frame)

    with as_user("reader"):
        resolved = book_access.resolve_book(shared_book.ref)
        fanout = router._SocketFanout(send)
        fanout.attach_book(shared_book.book_id, resolved)
        stream = get_book_stream(shared_book.book_id, storage=resolved.engine.storage)
        try:
            await stream.book_event("before", {})
            assert await _until(lambda: len(frames) == 1)
            with as_user("owner", role="teacher"):
                registry.set_book_visibility(shared_book.book_id, "private")
            clock.now += TTL + 0.01
            await stream.book_event("after", {})
            await stream.book_event("later", {})
            await _settle()
        finally:
            await fanout.close()
    assert _kinds(frames) == ["before"]


@pytest.mark.asyncio
async def test_check_failure_stays_fail_closed(shared_book, as_user, clock, monkeypatch):
    frames = []

    async def send(frame):
        frames.append(frame)

    def broken(_book_id):
        raise RuntimeError("synthetic registry failure")

    with as_user("reader"):
        resolved = book_access.resolve_book(shared_book.ref)
        fanout = router._SocketFanout(send)
        fanout.attach_book(shared_book.book_id, resolved)
        stream = get_book_stream(shared_book.book_id, storage=resolved.engine.storage)
        try:
            await stream.book_event("before", {})
            assert await _until(lambda: len(frames) == 1)
            monkeypatch.setattr(registry, "get_book", broken)
            clock.now += TTL + 0.01
            await stream.book_event("after", {})
            await _settle()
        finally:
            await fanout.close()
    assert _kinds(frames) == ["before"]
