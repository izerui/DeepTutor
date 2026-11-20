"""Quiz follow-up chats are filed under their quiz conversation (spec §4).

Reproduced against the real turn runtime before the fix: a follow-up started
with ``followup_question_context.parent_quiz_session_id`` got preferences
without ``parent_session_id``, so every followed-up question became its own
top-level conversation in the sidebar (which hides only parented sessions).
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from deeptutor.multi_user import paths as user_paths
from deeptutor.multi_user.context import reset_current_user, set_current_user
from deeptutor.multi_user.models import CurrentUser, UserScope
from deeptutor.services.session import get_sqlite_session_store
from deeptutor.services.session.sqlite_store import SQLiteSessionStore
from deeptutor.services.session.turn_runtime import TurnRuntimeManager


def _runtime(monkeypatch: pytest.MonkeyPatch, store) -> TurnRuntimeManager:
    runtime = TurnRuntimeManager(store)

    async def _noop_run_turn(_execution):
        return None

    monkeypatch.setattr(runtime, "_run_turn", _noop_run_turn)
    return runtime


def _quiz_followup_turn(parent_quiz_session_id: str, *, session_id: str | None = None) -> dict:
    return {
        "type": "start_turn",
        "session_id": session_id,
        "capability": "chat",
        "content": "Why is B wrong?",
        "tools": [],
        "knowledge_bases": [],
        "attachments": [],
        "language": "en",
        "followup_question_context": {
            "parent_quiz_session_id": parent_quiz_session_id,
            "question_id": "q1",
            "question": "Which option is correct?",
        },
    }


@pytest.mark.asyncio
async def test_quiz_followup_is_filed_under_its_quiz_conversation(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    store = SQLiteSessionStore(tmp_path / "chat.db")
    runtime = _runtime(monkeypatch, store)
    quiz = await store.ensure_session(None)

    followup, turn = await runtime.start_turn(_quiz_followup_turn(quiz["id"]))
    prefs = (await store.get_session(followup["id"]))["preferences"]
    assert prefs["parent_session_id"] == quiz["id"]
    assert prefs["session_kind"] == "quiz_followup"

    # Later turns keep it there.
    runtime._executions.pop(turn["id"], None)
    await store.update_turn_status(turn["id"], "completed")
    await runtime.start_turn(_quiz_followup_turn(quiz["id"], session_id=followup["id"]))
    prefs = (await store.get_session(followup["id"]))["preferences"]
    assert prefs["parent_session_id"] == quiz["id"]

    # It is no longer a top-level conversation of the workspace list.
    top_level = await store.list_sessions(workspace_id="")
    assert followup["id"] not in {row["session_id"] for row in top_level}


@pytest.mark.asyncio
async def test_quiz_followup_with_unknown_parent_stays_standalone(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    store = SQLiteSessionStore(tmp_path / "chat.db")
    runtime = _runtime(monkeypatch, store)

    followup, _ = await runtime.start_turn(_quiz_followup_turn("missing-session"))
    prefs = (await store.get_session(followup["id"]))["preferences"]
    assert not prefs.get("parent_session_id")
    assert prefs.get("session_kind") in (None, "chat")


@contextmanager
def _as_user(uid: str, role: str, root: Path):
    token = set_current_user(
        CurrentUser(
            id=uid, username=uid, role=role, scope=UserScope(kind="user", user_id=uid, root=root)
        )
    )
    try:
        yield
    finally:
        reset_current_user(token)


@pytest.mark.asyncio
async def test_a_learner_cannot_file_a_followup_under_another_users_quiz(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(user_paths, "ensure_user_workspace", lambda _uid: tmp_path)
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

    with _as_user("u_admin", "admin", tmp_path / "data" / "users" / "u_admin"):
        admin_quiz = await get_sqlite_session_store().ensure_session(None)

    with _as_user("u_learner", "user", tmp_path / "data" / "users" / "u_learner"):
        store = get_sqlite_session_store()
        runtime = _runtime(monkeypatch, store)
        followup, _ = await runtime.start_turn(_quiz_followup_turn(admin_quiz["id"]))
        prefs = (await store.get_session(followup["id"]))["preferences"]
        assert not prefs.get("parent_session_id")
