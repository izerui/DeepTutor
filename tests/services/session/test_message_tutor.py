"""Little Tutor threads opened from a message (spec 2026-11-20-message-tutor).

Covers: whole-reply questions read the message by id, the thread records its
source, deletion and regeneration detach (not delete) threads, the thread list
and organization endpoints, and per-user isolation.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException
import pytest

from deeptutor.api.routers import sessions as sessions_router
from deeptutor.multi_user import paths as user_paths
from deeptutor.multi_user.context import reset_current_user, set_current_user
from deeptutor.multi_user.models import CurrentUser, UserScope
from deeptutor.services.session import get_sqlite_session_store
from deeptutor.services.session._turn_runtime_shared import _format_selection_tutor_context
from deeptutor.services.session.sqlite_store import SQLiteSessionStore
from deeptutor.services.session.turn_runtime import TurnRuntimeManager


def _runtime(monkeypatch: pytest.MonkeyPatch, store) -> TurnRuntimeManager:
    runtime = TurnRuntimeManager(store)

    async def _noop_run_turn(_execution):
        return None

    monkeypatch.setattr(runtime, "_run_turn", _noop_run_turn)
    return runtime


def _tutor_turn(context: dict, *, session_id: str | None = None, content: str = "Why?") -> dict:
    return {
        "type": "start_turn",
        "session_id": session_id,
        "capability": "chat",
        "content": content,
        "tools": [],
        "knowledge_bases": [],
        "attachments": [],
        "language": "en",
        "selection_tutor_context": context,
    }


async def _finish(runtime: TurnRuntimeManager, store, turn: dict) -> None:
    """Let the next turn in the same thread start (one live turn per session)."""
    runtime._executions.pop(turn["id"], None)
    await store.update_turn_status(turn["id"], "completed")


async def _parent_with_reply(store, reply: str) -> tuple[dict, int, int]:
    parent = await store.ensure_session(None)
    user_id = await store.add_message(parent["id"], "user", "Explain paging.")
    assistant_id = await store.add_message(
        parent["id"], "assistant", reply, parent_message_id=user_id
    )
    return parent, user_id, assistant_id


# ── whole-reply questions and source recording ─────────────────────────────


@pytest.mark.asyncio
async def test_whole_reply_question_reads_the_message_by_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    store = SQLiteSessionStore(tmp_path / "chat.db")
    runtime = _runtime(monkeypatch, store)
    reply = "Paging splits memory into fixed-size pages mapped by a page table."
    parent, _, assistant_id = await _parent_with_reply(store, reply)

    child, turn = await runtime.start_turn(
        _tutor_turn(
            {
                "scope": "message",
                "selected_text": "forged selection",
                "parent_session_id": parent["id"],
                "source_message_id": assistant_id,
                "source_message_text": "forged message text",
            }
        )
    )

    context = runtime._executions[turn["id"]].payload["selection_tutor_context"]
    assert context["scope"] == "message"
    assert context["selected_text"] == ""
    assert context["source_message_text"] == reply
    prompt = _format_selection_tutor_context(context, "en")
    assert reply in prompt and "forged" not in prompt

    prefs = (await store.get_session(child["id"]))["preferences"]
    assert prefs["parent_session_id"] == parent["id"]
    assert prefs["session_kind"] == "selection_tutor"
    assert prefs["tutor_scope"] == "message"
    assert prefs["tutor_source_message_id"] == assistant_id
    assert prefs["tutor_selected_text"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("unsaved_id", [-1_795_588_873_681, 0, "-12"])
async def test_selection_on_an_unsaved_reply_leaves_the_thread_unattached(
    monkeypatch: pytest.MonkeyPatch, tmp_path, unsaved_id
) -> None:
    """Upstream 67fc8b331: an optimistic (not yet persisted) reply has no real
    id, so the thread must not record the placeholder as its source."""
    store = SQLiteSessionStore(tmp_path / "chat.db")
    runtime = _runtime(monkeypatch, store)
    reply = "Paging splits memory into fixed-size pages."
    parent, _, _ = await _parent_with_reply(store, reply)

    child, _ = await runtime.start_turn(
        _tutor_turn(
            {
                "selected_text": "fixed-size pages",
                "parent_session_id": parent["id"],
                "source_message_id": unsaved_id,
                "source_message_text": reply,
            }
        )
    )

    prefs = (await store.get_session(child["id"]))["preferences"]
    assert prefs["session_kind"] == "selection_tutor"
    assert prefs["tutor_source_message_id"] == ""
    assert prefs["tutor_selected_text"] == "fixed-size pages"


@pytest.mark.asyncio
async def test_whole_reply_question_requires_a_message_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    store = SQLiteSessionStore(tmp_path / "chat.db")
    runtime = _runtime(monkeypatch, store)
    parent, _, _ = await _parent_with_reply(store, "Some reply.")

    with pytest.raises(RuntimeError, match="message id"):
        await runtime.start_turn(
            _tutor_turn(
                {
                    "scope": "message",
                    "parent_session_id": parent["id"],
                    "source_message_text": "Some reply.",
                }
            )
        )


@pytest.mark.asyncio
async def test_selection_thread_records_source_and_selected_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    store = SQLiteSessionStore(tmp_path / "chat.db")
    runtime = _runtime(monkeypatch, store)
    parent, _, assistant_id = await _parent_with_reply(
        store, "A TLB caches recent page-table entries."
    )

    child, _ = await runtime.start_turn(
        _tutor_turn(
            {
                "selected_text": "caches recent page-table entries",
                "parent_session_id": parent["id"],
                "source_message_id": assistant_id,
            }
        )
    )

    prefs = (await store.get_session(child["id"]))["preferences"]
    assert prefs["tutor_scope"] == "selection"
    assert prefs["tutor_source_message_id"] == assistant_id
    assert prefs["tutor_selected_text"] == "caches recent page-table entries"
    assert prefs["tutor_source_deleted"] is False


@pytest.mark.asyncio
async def test_message_from_another_conversation_cannot_be_attached(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    store = SQLiteSessionStore(tmp_path / "chat.db")
    runtime = _runtime(monkeypatch, store)
    parent, _, _ = await _parent_with_reply(store, "Parent reply.")
    _, _, foreign_id = await _parent_with_reply(store, "Other conversation reply.")

    with pytest.raises(RuntimeError, match="not found"):
        await runtime.start_turn(
            _tutor_turn(
                {
                    "scope": "message",
                    "parent_session_id": parent["id"],
                    "source_message_id": foreign_id,
                }
            )
        )


# ── deletion and regeneration detach ───────────────────────────────────────


@pytest.mark.asyncio
async def test_deleting_the_source_turn_detaches_but_keeps_the_thread(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    store = SQLiteSessionStore(tmp_path / "chat.db")
    runtime = _runtime(monkeypatch, store)
    parent, user_id, assistant_id = await _parent_with_reply(store, "Reply to delete.")
    child, turn = await runtime.start_turn(
        _tutor_turn(
            {
                "scope": "message",
                "parent_session_id": parent["id"],
                "source_message_id": assistant_id,
            }
        )
    )
    await _finish(runtime, store, turn)

    result = await store.delete_turn_by_message(parent["id"], assistant_id)

    assert result["deleted"] is True
    assert result["detached_thread_ids"] == [child["id"]]
    assert set(result["deleted_message_ids"]) == {assistant_id, user_id}
    thread = await store.get_session(child["id"])
    assert thread is not None
    assert thread["preferences"]["tutor_source_message_id"] == ""
    assert thread["preferences"]["tutor_source_deleted"] is True
    assert thread["preferences"]["parent_session_id"] == parent["id"]

    # The thread keeps going without its source, and is not re-anchored.
    _, next_turn = await runtime.start_turn(
        _tutor_turn(
            {
                "scope": "message",
                "parent_session_id": parent["id"],
                "source_message_id": assistant_id,
            },
            session_id=child["id"],
            content="And then?",
        )
    )
    context = runtime._executions[next_turn["id"]].payload["selection_tutor_context"]
    assert context["source_detached"] is True
    assert context["source_message_text"] == ""
    prefs = (await store.get_session(child["id"]))["preferences"]
    assert prefs["tutor_source_message_id"] == ""


@pytest.mark.asyncio
async def test_regenerate_detaches_threads_from_the_replaced_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    store = SQLiteSessionStore(tmp_path / "chat.db")
    runtime = _runtime(monkeypatch, store)
    parent, _, assistant_id = await _parent_with_reply(store, "First answer.")
    child, turn = await runtime.start_turn(
        _tutor_turn(
            {
                "selected_text": "First answer",
                "parent_session_id": parent["id"],
                "source_message_id": assistant_id,
            }
        )
    )
    await _finish(runtime, store, turn)

    await runtime.regenerate_last_turn(parent["id"])

    remaining = {message["id"] for message in await store.get_messages(parent["id"])}
    assert assistant_id not in remaining
    prefs = (await store.get_session(child["id"]))["preferences"]
    assert prefs["tutor_source_message_id"] == ""
    assert prefs["tutor_source_deleted"] is True


@pytest.mark.asyncio
async def test_deleting_an_unrelated_message_leaves_threads_attached(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    store = SQLiteSessionStore(tmp_path / "chat.db")
    runtime = _runtime(monkeypatch, store)
    parent, user_id, assistant_id = await _parent_with_reply(store, "Kept reply.")
    second_user = await store.add_message(
        parent["id"], "user", "Another question", parent_message_id=assistant_id
    )
    second_reply = await store.add_message(
        parent["id"], "assistant", "Another reply", parent_message_id=second_user
    )
    child, _ = await runtime.start_turn(
        _tutor_turn(
            {
                "scope": "message",
                "parent_session_id": parent["id"],
                "source_message_id": assistant_id,
            }
        )
    )

    result = await store.delete_turn_by_message(parent["id"], second_reply)

    assert result["detached_thread_ids"] == []
    prefs = (await store.get_session(child["id"]))["preferences"]
    assert prefs["tutor_source_message_id"] == assistant_id


# ── thread list and organization ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_threads_endpoint_lists_tutor_threads_and_detach_rules(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    store = SQLiteSessionStore(tmp_path / "chat.db")
    runtime = _runtime(monkeypatch, store)
    monkeypatch.setattr(sessions_router, "get_session_store", lambda: store)
    parent, user_id, assistant_id = await _parent_with_reply(store, "Thread source.")
    child, turn = await runtime.start_turn(
        _tutor_turn(
            {
                "scope": "message",
                "parent_session_id": parent["id"],
                "source_message_id": assistant_id,
            }
        )
    )
    await _finish(runtime, store, turn)
    # A non-tutor child of the same conversation is not a thread.
    other_child = await store.ensure_session(None)
    await store.update_session_preferences(
        other_child["id"], {"parent_session_id": parent["id"], "session_kind": "chat"}
    )
    _, _, foreign_id = await _parent_with_reply(store, "Elsewhere.")

    listed = await sessions_router.list_session_threads(parent["id"])
    assert [row["session_id"] for row in listed["threads"]] == [child["id"]]

    # Moving to a message of another conversation is refused.
    with pytest.raises(HTTPException) as foreign:
        await sessions_router.update_session_organization(
            child["id"],
            sessions_router.SessionOrganizationRequest(tutor_source_message_id=foreign_id),
        )
    assert foreign.value.status_code == 400

    # Only tutor threads have a source.
    with pytest.raises(HTTPException) as not_thread:
        await sessions_router.update_session_organization(
            other_child["id"],
            sessions_router.SessionOrganizationRequest(tutor_source_message_id=None),
        )
    assert not_thread.value.status_code == 400

    moved = await sessions_router.update_session_organization(
        child["id"], sessions_router.SessionOrganizationRequest(tutor_source_message_id=user_id)
    )
    assert moved["session"]["preferences"]["tutor_source_message_id"] == user_id

    detached = await sessions_router.update_session_organization(
        child["id"], sessions_router.SessionOrganizationRequest(tutor_source_message_id=None)
    )
    prefs = detached["session"]["preferences"]
    assert prefs["tutor_source_message_id"] == ""
    assert prefs["parent_session_id"] == parent["id"]
    assert prefs.get("tutor_source_deleted") is False

    # A manually detached thread is not re-anchored by its next turn.
    await runtime.start_turn(
        _tutor_turn(
            {
                "scope": "message",
                "parent_session_id": parent["id"],
                "source_message_id": assistant_id,
            },
            session_id=child["id"],
        )
    )
    prefs = (await store.get_session(child["id"]))["preferences"]
    assert prefs["tutor_source_message_id"] == ""


# ── per-user isolation (admin + learner) ───────────────────────────────────


@contextmanager
def _as_user(uid: str, role: str, root: Path):
    user = CurrentUser(
        id=uid,
        username=uid,
        role=role,
        scope=UserScope(kind="user", user_id=uid, root=root),
    )
    token = set_current_user(user)
    try:
        yield
    finally:
        reset_current_user(token)


@pytest.mark.asyncio
async def test_a_learner_cannot_attach_threads_to_another_users_conversation(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(user_paths, "ensure_user_workspace", lambda _uid: tmp_path)
    # The learner has a granted model; model resolution itself is not under test.
    from deeptutor.multi_user import model_access, personal_models
    from deeptutor.services import model_selection

    monkeypatch.setattr(model_access, "has_capability_access", lambda _cap: True)
    monkeypatch.setattr(
        model_access,
        "redacted_model_access",
        lambda _uid: {"llm": [{"available": True, "profile_id": "p", "model_id": "m"}]},
    )
    monkeypatch.setattr(personal_models, "merge_personal_llm_profiles", lambda catalog: catalog)
    monkeypatch.setattr(model_selection, "apply_llm_selection_to_catalog", lambda *_a: None)
    admin_root = tmp_path / "data" / "users" / "u_admin"
    learner_root = tmp_path / "data" / "users" / "u_learner"

    with _as_user("u_admin", "admin", admin_root):
        admin_store = get_sqlite_session_store()
        # Message ids are per user database; pad the admin's so the id below
        # cannot coincide with one of the learner's own messages.
        filler = await admin_store.ensure_session(None)
        for index in range(6):
            await admin_store.add_message(filler["id"], "user", f"filler {index}")
        admin_parent, _, admin_reply = await _parent_with_reply(admin_store, "Admin reply.")

    with _as_user("u_learner", "user", learner_root):
        learner_store = get_sqlite_session_store()
        assert learner_store is not admin_store
        runtime = _runtime(monkeypatch, learner_store)

        # Not the learner's conversation or message: refused, nothing created.
        with pytest.raises(RuntimeError, match="source message was not found"):
            await runtime.start_turn(
                _tutor_turn(
                    {
                        "scope": "message",
                        "parent_session_id": admin_parent["id"],
                        "source_message_id": admin_reply,
                    }
                )
            )
        with pytest.raises(HTTPException) as listed:
            await sessions_router.list_session_threads(admin_parent["id"])
        assert listed.value.status_code == 404

        # The learner's own thread cannot be moved onto the admin's message.
        own_parent, _, own_reply = await _parent_with_reply(learner_store, "Learner reply.")
        thread, _ = await runtime.start_turn(
            _tutor_turn(
                {
                    "scope": "message",
                    "parent_session_id": own_parent["id"],
                    "source_message_id": own_reply,
                }
            )
        )
        with pytest.raises(HTTPException) as moved:
            await sessions_router.update_session_organization(
                thread["id"],
                sessions_router.SessionOrganizationRequest(tutor_source_message_id=admin_reply),
            )
        assert moved.value.status_code == 400

    with _as_user("u_admin", "admin", admin_root):
        listed = await sessions_router.list_session_threads(admin_parent["id"])
        assert listed["threads"] == []
