"""Recovering the source message of legacy Little Tutor threads.

Threads opened before 2026-11-20 (spec 2026-11-20-message-tutor) never wrote
``tutor_source_message_id``. Their only durable record of where they came from
is the sidebar prompt kept in each assistant row's ``model_turn.system``:
the containing message, its role, the learner's exact selection and the source
session. Ported from upstream 50f1d9f52 / 58afb10b5 / 75d65b7de and adapted to
the local preference model.
"""

from __future__ import annotations

import asyncio

import pytest

from deeptutor.api.routers import sessions as sessions_router
from deeptutor.services.session import thread_attribution
from deeptutor.services.session._turn_runtime_shared import (
    _format_selection_tutor_context,
    _selection_source_excerpt,
)
from deeptutor.services.session.sqlite_store import SQLiteSessionStore
from deeptutor.services.session.thread_attribution import (
    BACKFILL_FAILED_AT_KEY,
    TUTOR_SOURCE_KEY,
    backfill_thread_sources,
    locate_selected_message,
)

pytestmark = pytest.mark.asyncio

REPLY = (
    "Paging splits memory into fixed-size pages. The page table maps each "
    "virtual page to a physical frame, and a TLB caches recent translations."
)
SELECTION = "a TLB caches recent translations"


@pytest.fixture(autouse=True)
def _fresh_cooldowns():
    thread_attribution._FAILED_UNTIL.clear()
    yield
    thread_attribution._FAILED_UNTIL.clear()


def _prompt(
    parent_id: str,
    *,
    selection: str = SELECTION,
    source_text: str = REPLY,
    role: str = "assistant",
    language: str = "en",
) -> str:
    return _format_selection_tutor_context(
        {
            "selected_text": selection,
            "parent_session_id": parent_id,
            "source_message_text": _selection_source_excerpt(source_text, selection),
            "source_message_role": role,
        },
        language=language,
    )


def _model_turn(system: str) -> dict:
    return {
        "model_turn": {
            "version": 1,
            "system": system,
            "messages": [
                {"role": "user", "content": "What does this mean?"},
                {"role": "assistant", "content": "It means..."},
            ],
        }
    }


async def _parent(store, reply: str = REPLY) -> tuple[dict, int, int]:
    parent = await store.ensure_session(None)
    user_id = await store.add_message(parent["id"], "user", "Explain paging.")
    assistant_id = await store.add_message(
        parent["id"], "assistant", reply, parent_message_id=user_id
    )
    return parent, user_id, assistant_id


async def _legacy_thread(
    store,
    parent_id: str,
    *,
    system: str | None,
    extra_preferences: dict | None = None,
) -> dict:
    """A thread exactly as the pre-2026-11-20 runtime left it."""
    thread = await store.ensure_session(None)
    await store.add_message(thread["id"], "user", "What does this mean?")
    await store.add_message(
        thread["id"],
        "assistant",
        "It means...",
        metadata=_model_turn(system) if system is not None else None,
    )
    await store.update_session_preferences(
        thread["id"],
        {
            "parent_session_id": parent_id,
            "session_kind": "selection_tutor",
            "course_id": "",
            **(extra_preferences or {}),
        },
    )
    return await store.get_session(thread["id"])


async def _threads(store, parent_id: str) -> list[dict]:
    rows = await store.list_sessions(limit=200)
    return [
        row
        for row in rows
        if (row.get("preferences") or {}).get("parent_session_id") == parent_id
        and (row.get("preferences") or {}).get("session_kind") == "selection_tutor"
    ]


async def _prefs(store, session_id: str) -> dict:
    return (await store.get_session(session_id))["preferences"]


# ── recovery ───────────────────────────────────────────────────────────────


async def test_legacy_thread_recovers_its_source_without_reordering(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "chat.db")
    parent, _, assistant_id = await _parent(store)
    # The sidebar block sits between other system-prompt blocks.
    system = f"You are DeepTutor.\n\n{_prompt(parent['id'])}\n\n[Course conventions]\nUse C."
    thread = await _legacy_thread(store, parent["id"], system=system)
    before = await store.get_session(thread["id"])

    rows = await backfill_thread_sources(store, parent["id"], await _threads(store, parent["id"]))

    prefs = await _prefs(store, thread["id"])
    assert prefs[TUTOR_SOURCE_KEY] == assistant_id
    assert prefs["tutor_scope"] == "selection"
    assert prefs["tutor_selected_text"] == SELECTION
    assert prefs["tutor_source_deleted"] is False
    # The returned rows reflect the write, so the marker shows up at once.
    assert rows[0]["preferences"][TUTOR_SOURCE_KEY] == assistant_id
    # Derived metadata must not move the thread in the session list.
    after = await store.get_session(thread["id"])
    assert after["updated_at"] == before["updated_at"]


async def test_chinese_prompt_and_threads_endpoint(monkeypatch, tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "chat.db")
    monkeypatch.setattr(sessions_router, "get_session_store", lambda: store)
    parent, _, assistant_id = await _parent(store)
    thread = await _legacy_thread(
        store, parent["id"], system=_prompt(parent["id"], language="zh")
    )

    listed = await sessions_router.list_session_threads(parent["id"])

    [row] = listed["threads"]
    assert row["session_id"] == thread["id"]
    assert row["preferences"][TUTOR_SOURCE_KEY] == assistant_id


async def test_role_and_containing_message_disambiguate_an_echoed_selection(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "chat.db")
    parent = await store.ensure_session(None)
    first = await store.add_message(parent["id"], "assistant", REPLY)
    # The learner quotes the passage back in their own message.
    await store.add_message(parent["id"], "user", f"Why do you say {SELECTION}?")
    # A later answer repeats the passage in different surroundings.
    await store.add_message(
        parent["id"], "assistant", f"To summarise: {SELECTION}. Misses walk the table."
    )
    thread = await _legacy_thread(store, parent["id"], system=_prompt(parent["id"]))

    await backfill_thread_sources(store, parent["id"], await _threads(store, parent["id"]))

    assert (await _prefs(store, thread["id"]))[TUTOR_SOURCE_KEY] == first


async def test_excerpted_containing_message_still_matches(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "chat.db")
    long_reply = ("Background about memory. " * 800) + REPLY + (" More detail." * 800)
    parent, _, assistant_id = await _parent(store, long_reply)
    system = _prompt(parent["id"], source_text=long_reply)
    assert "[earlier content omitted]" in system
    thread = await _legacy_thread(store, parent["id"], system=system)

    await backfill_thread_sources(store, parent["id"], await _threads(store, parent["id"]))

    assert (await _prefs(store, thread["id"]))[TUTOR_SOURCE_KEY] == assistant_id


# ── refusing to guess ──────────────────────────────────────────────────────


async def test_two_identical_candidates_are_not_guessed(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "chat.db")
    parent = await store.ensure_session(None)
    await store.add_message(parent["id"], "assistant", REPLY)
    await store.add_message(parent["id"], "assistant", REPLY)
    thread = await _legacy_thread(store, parent["id"], system=_prompt(parent["id"]))

    await backfill_thread_sources(store, parent["id"], await _threads(store, parent["id"]))

    prefs = await _prefs(store, thread["id"])
    assert TUTOR_SOURCE_KEY not in prefs
    assert prefs[BACKFILL_FAILED_AT_KEY] > 0


async def test_thread_without_a_recorded_prompt_is_left_alone(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "chat.db")
    parent, _, _ = await _parent(store)
    thread = await _legacy_thread(store, parent["id"], system=None)

    await backfill_thread_sources(store, parent["id"], await _threads(store, parent["id"]))

    assert TUTOR_SOURCE_KEY not in await _prefs(store, thread["id"])


async def test_prompt_from_another_conversation_is_not_trusted(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "chat.db")
    parent, _, _ = await _parent(store)
    elsewhere, _, _ = await _parent(store)
    # The thread is filed under ``parent`` but its prompt names another chat.
    thread = await _legacy_thread(store, parent["id"], system=_prompt(elsewhere["id"]))

    await backfill_thread_sources(store, parent["id"], await _threads(store, parent["id"]))

    assert TUTOR_SOURCE_KEY not in await _prefs(store, thread["id"])


def test_short_or_absent_selection_matches_nothing() -> None:
    messages = [{"id": 1, "role": "assistant", "content": "a b c d e f"}]
    assert locate_selected_message(messages, "b c") is None
    assert locate_selected_message(messages, "") is None


# ── the learner's own choices win ──────────────────────────────────────────


@pytest.mark.parametrize(
    "preferences",
    [
        {TUTOR_SOURCE_KEY: ""},  # detached by the learner
        {TUTOR_SOURCE_KEY: "", "tutor_source_deleted": True},  # source deleted
        {"tutor_source_deleted": True},
    ],
)
async def test_detached_or_deleted_threads_are_not_reattached(tmp_path, preferences) -> None:
    store = SQLiteSessionStore(tmp_path / "chat.db")
    parent, _, _ = await _parent(store)
    thread = await _legacy_thread(
        store, parent["id"], system=_prompt(parent["id"]), extra_preferences=preferences
    )

    await backfill_thread_sources(store, parent["id"], await _threads(store, parent["id"]))

    prefs = await _prefs(store, thread["id"])
    assert prefs.get(TUTOR_SOURCE_KEY) == preferences.get(TUTOR_SOURCE_KEY)
    assert BACKFILL_FAILED_AT_KEY not in prefs


async def test_a_detach_that_lands_during_the_scan_wins(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "chat.db")
    parent, _, _ = await _parent(store)
    thread = await _legacy_thread(store, parent["id"], system=_prompt(parent["id"]))
    get_messages = store.get_messages

    async def detach_while_reading(session_id: str):
        if session_id == parent["id"]:
            await store.update_session_preferences(thread["id"], {TUTOR_SOURCE_KEY: ""})
        return await get_messages(session_id)

    store.get_messages = detach_while_reading  # type: ignore[method-assign]
    rows = await backfill_thread_sources(
        store, parent["id"], await _threads(store, parent["id"])
    )

    assert (await _prefs(store, thread["id"]))[TUTOR_SOURCE_KEY] == ""
    assert rows[0]["preferences"][TUTOR_SOURCE_KEY] == ""


async def test_a_move_that_lands_during_the_scan_wins(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "chat.db")
    parent, user_id, _ = await _parent(store)
    thread = await _legacy_thread(store, parent["id"], system=_prompt(parent["id"]))
    get_messages = store.get_messages

    async def move_while_reading(session_id: str):
        if session_id == parent["id"]:
            await store.update_session_preferences(thread["id"], {TUTOR_SOURCE_KEY: user_id})
        return await get_messages(session_id)

    store.get_messages = move_while_reading  # type: ignore[method-assign]
    await backfill_thread_sources(store, parent["id"], await _threads(store, parent["id"]))

    assert (await _prefs(store, thread["id"]))[TUTOR_SOURCE_KEY] == user_id


async def test_reparented_thread_is_not_attributed_to_the_old_parent(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "chat.db")
    parent, _, _ = await _parent(store)
    other, _, _ = await _parent(store)
    thread = await _legacy_thread(store, parent["id"], system=_prompt(parent["id"]))
    get_messages = store.get_messages

    async def reparent_while_reading(session_id: str):
        if session_id == parent["id"]:
            await store.update_session_preferences(
                thread["id"], {"parent_session_id": other["id"]}
            )
        return await get_messages(session_id)

    store.get_messages = reparent_while_reading  # type: ignore[method-assign]
    await backfill_thread_sources(store, parent["id"], await _threads(store, parent["id"]))

    assert TUTOR_SOURCE_KEY not in await _prefs(store, thread["id"])


# ── account and conversation boundaries ────────────────────────────────────


async def test_another_accounts_conversation_is_never_matched(tmp_path) -> None:
    admin = SQLiteSessionStore(tmp_path / "admin.db")
    learner = SQLiteSessionStore(tmp_path / "learner.db")
    admin_parent, _, _ = await _parent(admin)
    # A learner thread claiming the admin's conversation as its parent, with a
    # prompt that quotes the admin's message verbatim.
    thread = await _legacy_thread(
        learner, admin_parent["id"], system=_prompt(admin_parent["id"])
    )

    await backfill_thread_sources(
        learner, admin_parent["id"], await _threads(learner, admin_parent["id"])
    )

    assert TUTOR_SOURCE_KEY not in await _prefs(learner, thread["id"])


async def test_threads_of_other_parents_in_the_list_are_ignored(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "chat.db")
    parent, _, _ = await _parent(store)
    other, _, _ = await _parent(store)
    thread = await _legacy_thread(store, other["id"], system=_prompt(other["id"]))

    # A caller handing in a thread of another conversation gets nothing written.
    await backfill_thread_sources(store, parent["id"], await _threads(store, other["id"]))

    assert TUTOR_SOURCE_KEY not in await _prefs(store, thread["id"])


# ── cooldown ───────────────────────────────────────────────────────────────


async def test_failed_attribution_cools_down_then_retries(monkeypatch, tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "chat.db")
    parent = await store.ensure_session(None)
    first = await store.add_message(parent["id"], "assistant", REPLY)
    duplicate = await store.add_message(parent["id"], "assistant", REPLY)
    thread = await _legacy_thread(store, parent["id"], system=_prompt(parent["id"]))
    reads: list[str] = []
    get_messages = store.get_messages

    async def counting(session_id: str):
        reads.append(session_id)
        return await get_messages(session_id)

    store.get_messages = counting  # type: ignore[method-assign]
    clock = [1_000_000.0]
    monkeypatch.setattr(thread_attribution, "_wall_clock", lambda: clock[0])
    monkeypatch.setattr(thread_attribution, "_monotonic", lambda: clock[0])

    await backfill_thread_sources(store, parent["id"], await _threads(store, parent["id"]))
    assert reads  # scanned once
    reads.clear()

    # Within the cooldown: no transcript is read at all, even after a restart
    # forgets the in-memory record (the failure time is persisted).
    thread_attribution._FAILED_UNTIL.clear()
    await backfill_thread_sources(store, parent["id"], await _threads(store, parent["id"]))
    assert reads == []

    # The ambiguity goes away; after the cooldown the thread is retried.
    await store.delete_message(duplicate)
    clock[0] += thread_attribution.BACKFILL_RETRY_SECONDS + 1
    await backfill_thread_sources(store, parent["id"], await _threads(store, parent["id"]))
    assert (await _prefs(store, thread["id"]))[TUTOR_SOURCE_KEY] == first


async def test_one_request_scans_a_bounded_number_of_threads(monkeypatch, tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "chat.db")
    parent, _, _ = await _parent(store)
    for _ in range(3):
        await _legacy_thread(store, parent["id"], system=_prompt(parent["id"]))
    monkeypatch.setattr(thread_attribution, "MAX_BACKFILLED_THREADS", 2)

    await backfill_thread_sources(store, parent["id"], await _threads(store, parent["id"]))

    attributed = [
        row
        for row in await _threads(store, parent["id"])
        if (row.get("preferences") or {}).get(TUTOR_SOURCE_KEY)
    ]
    assert len(attributed) == 2


async def test_concurrent_reads_scan_once(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "chat.db")
    parent, _, assistant_id = await _parent(store)
    thread = await _legacy_thread(store, parent["id"], system=_prompt(parent["id"]))
    parent_reads = 0
    get_messages = store.get_messages

    async def counting(session_id: str):
        nonlocal parent_reads
        if session_id == parent["id"]:
            parent_reads += 1
        return await get_messages(session_id)

    store.get_messages = counting  # type: ignore[method-assign]
    await asyncio.gather(
        *[
            backfill_thread_sources(store, parent["id"], await _threads(store, parent["id"]))
            for _ in range(3)
        ]
    )

    assert parent_reads == 1
    assert (await _prefs(store, thread["id"]))[TUTOR_SOURCE_KEY] == assistant_id


# ── conditional store write ────────────────────────────────────────────────


async def test_sqlite_conditional_write_refuses_once_a_source_is_set(tmp_path) -> None:
    store = SQLiteSessionStore(tmp_path / "chat.db")
    session = await store.ensure_session(None)
    await store.update_session_preferences(
        session["id"], {"parent_session_id": "p", TUTOR_SOURCE_KEY: ""}
    )
    assert not await store.update_session_preferences_if_absent(
        session["id"], {TUTOR_SOURCE_KEY: 5}, absent=(TUTOR_SOURCE_KEY,)
    )
    other = await store.ensure_session(None)
    await store.update_session_preferences(other["id"], {"parent_session_id": "p"})
    assert not await store.update_session_preferences_if_absent(
        other["id"], {TUTOR_SOURCE_KEY: 5}, absent=(TUTOR_SOURCE_KEY,),
        expected={"parent_session_id": "q"},
    )
    assert await store.update_session_preferences_if_absent(
        other["id"], {TUTOR_SOURCE_KEY: 5}, absent=(TUTOR_SOURCE_KEY,),
        expected={"parent_session_id": "p"},
    )
    assert (await _prefs(store, other["id"]))[TUTOR_SOURCE_KEY] == 5
    assert not await store.update_session_preferences_if_absent(
        "missing", {TUTOR_SOURCE_KEY: 5}, absent=(TUTOR_SOURCE_KEY,)
    )
