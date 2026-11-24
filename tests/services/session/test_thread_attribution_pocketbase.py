"""Legacy Little Tutor thread recovery on the PocketBase backend.

PocketBase message ids are strings and preference writes are serialized by the
store's per-account, per-session ``_prefs_lock``; the conditional backfill
write must take that same lock and read inside it.
"""

from __future__ import annotations

import asyncio

import pytest

from deeptutor.services.session import thread_attribution
from deeptutor.services.session._turn_runtime_shared import _format_selection_tutor_context
from deeptutor.services.session.pocketbase_store import PocketBaseSessionStore
from deeptutor.services.session.thread_attribution import (
    BACKFILL_FAILED_AT_KEY,
    TUTOR_SOURCE_KEY,
    backfill_thread_sources,
)
from tests.services.session.test_pocketbase_isolation import _slow_session_updates, as_user
from tests.services.session.test_pocketbase_isolation import fake_pb as fake_pb

pytestmark = pytest.mark.asyncio

REPLY = "The page table maps each virtual page to a physical frame; a TLB caches it."
SELECTION = "a TLB caches it"


@pytest.fixture(autouse=True)
def _fresh_cooldowns():
    thread_attribution._FAILED_UNTIL.clear()
    yield
    thread_attribution._FAILED_UNTIL.clear()


def _system(parent_id: str) -> str:
    return _format_selection_tutor_context(
        {
            "selected_text": SELECTION,
            "parent_session_id": parent_id,
            "source_message_text": REPLY,
            "source_message_role": "assistant",
        }
    )


async def _setup(store: PocketBaseSessionStore, parent_id: str = "parent", **extra) -> str:
    await store.create_session(session_id=parent_id)
    await store.add_message(parent_id, "user", "Explain paging.")
    source = await store.add_message(parent_id, "assistant", REPLY)
    await store.create_session(session_id="thread")
    await store.add_message("thread", "user", "Why?")
    await store.add_message(
        "thread",
        "assistant",
        "Because...",
        metadata={
            "model_turn": {
                "version": 1,
                "system": _system(parent_id),
                "messages": [
                    {"role": "user", "content": "Why?"},
                    {"role": "assistant", "content": "Because..."},
                ],
            }
        },
    )
    await store.update_session_preferences(
        "thread",
        {"parent_session_id": parent_id, "session_kind": "selection_tutor", **extra},
    )
    return str(source)


async def test_pocketbase_legacy_thread_recovers_a_string_source(fake_pb) -> None:
    store = PocketBaseSessionStore()
    with as_user("alice"):
        source = await _setup(store)
        before = await store.get_session("thread")
        rows = await backfill_thread_sources(store, "parent", [before])
        after = await store.get_session("thread")
    assert after["preferences"][TUTOR_SOURCE_KEY] == source
    assert after["preferences"]["tutor_selected_text"] == SELECTION
    assert rows[0]["preferences"][TUTOR_SOURCE_KEY] == source
    assert after["updated_at"] == before["updated_at"]


async def test_pocketbase_detached_thread_is_not_reattached(fake_pb) -> None:
    store = PocketBaseSessionStore()
    with as_user("alice"):
        await _setup(store, **{TUTOR_SOURCE_KEY: ""})
        await backfill_thread_sources(store, "parent", [await store.get_session("thread")])
        prefs = (await store.get_session("thread"))["preferences"]
    assert prefs[TUTOR_SOURCE_KEY] == ""
    assert BACKFILL_FAILED_AT_KEY not in prefs


async def test_pocketbase_other_account_parent_is_invisible(fake_pb) -> None:
    store = PocketBaseSessionStore()
    with as_user("alice"):
        await store.create_session(session_id="parent")
        await store.add_message("parent", "assistant", REPLY)
    with as_user("bob"):
        # Bob's thread names Alice's conversation and quotes her message.
        await store.create_session(session_id="thread")
        await store.add_message(
            "thread",
            "assistant",
            "Because...",
            metadata={
                "model_turn": {
                    "version": 1,
                    "system": _system("parent"),
                    "messages": [{"role": "assistant", "content": "Because..."}],
                }
            },
        )
        await store.update_session_preferences(
            "thread", {"parent_session_id": "parent", "session_kind": "selection_tutor"}
        )
        await backfill_thread_sources(store, "parent", [await store.get_session("thread")])
        prefs = (await store.get_session("thread"))["preferences"]
    assert TUTOR_SOURCE_KEY not in prefs


async def test_pocketbase_conditional_write_and_detach_race(fake_pb, monkeypatch) -> None:
    """Whichever lands first, a concurrent detach is never undone."""
    store = PocketBaseSessionStore()
    with as_user("alice"):
        source = await _setup(store)
        _slow_session_updates(fake_pb, monkeypatch)
        results = await asyncio.gather(
            store.update_session_preferences_if_absent(
                "thread",
                {TUTOR_SOURCE_KEY: source},
                absent=(TUTOR_SOURCE_KEY,),
                expected={"parent_session_id": "parent"},
            ),
            store.update_session_preferences("thread", {TUTOR_SOURCE_KEY: ""}),
        )
        prefs = (await store.get_session("thread"))["preferences"]
    assert results[1] is True
    assert prefs[TUTOR_SOURCE_KEY] == ""


async def test_pocketbase_conditional_write_after_detach_is_refused(fake_pb) -> None:
    store = PocketBaseSessionStore()
    with as_user("alice"):
        source = await _setup(store)
        await store.update_session_preferences("thread", {TUTOR_SOURCE_KEY: ""})
        assert not await store.update_session_preferences_if_absent(
            "thread", {TUTOR_SOURCE_KEY: source}, absent=(TUTOR_SOURCE_KEY,)
        )
        assert not await store.update_session_preferences_if_absent(
            "missing", {TUTOR_SOURCE_KEY: source}, absent=(TUTOR_SOURCE_KEY,)
        )
    with as_user("bob"):
        # Another account cannot write Alice's thread even conditionally.
        assert not await store.update_session_preferences_if_absent(
            "thread", {"x": 1}, absent=("y",)
        )
    with as_user("alice"):
        prefs = (await store.get_session("thread"))["preferences"]
    assert prefs[TUTOR_SOURCE_KEY] == ""
    assert "x" not in prefs


async def test_pocketbase_failure_is_recorded_and_cools_down(fake_pb) -> None:
    store = PocketBaseSessionStore()
    with as_user("alice"):
        await _setup(store)
        # A second identical answer makes the passage ambiguous.
        await store.add_message("parent", "assistant", REPLY)
        await backfill_thread_sources(store, "parent", [await store.get_session("thread")])
        prefs = (await store.get_session("thread"))["preferences"]
    assert TUTOR_SOURCE_KEY not in prefs
    assert prefs[BACKFILL_FAILED_AT_KEY] > 0
