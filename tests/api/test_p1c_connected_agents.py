"""P1c: connected local agents (Claude Code, Codex, ...) are closed for every account."""

from __future__ import annotations

import asyncio
import json

from fastapi import HTTPException
import pytest

from deeptutor.api.routers import subagents
from deeptutor.knowledge.kb_types import (
    RETIRED_CONNECTED_AGENT_MESSAGE,
    is_retired_connected_agent_kb,
)
from deeptutor.services.connected_agents import CONNECTED_AGENTS_DISABLED


def _refused(coro):
    with pytest.raises(HTTPException) as caught:
        asyncio.run(coro)
    assert caught.value.status_code == 403
    assert caught.value.detail == CONNECTED_AGENTS_DISABLED


def test_every_route_that_runs_or_adds_an_agent_is_refused():
    _refused(subagents.detect_subagents())
    _refused(subagents.backend_options())
    _refused(subagents.sync_backend("claude_code"))
    _refused(subagents.create_connection(
        subagents.ConnectSubagentRequest(name="cc", agent_kind="claude_code", cwd="/"),
    ))
    _refused(subagents.message_connection(
        "cc", subagents.SubagentMessageRequest(message="hi"),
    ))


def test_local_agent_kbs_are_retired_but_partner_delegates_are_not():
    assert is_retired_connected_agent_kb({"type": "subagent", "agent_kind": "claude_code"})
    assert is_retired_connected_agent_kb({"type": "subagent", "agent_kind": "hermes_remote"})
    assert not is_retired_connected_agent_kb({"type": "subagent", "agent_kind": "partner"})
    assert not is_retired_connected_agent_kb({"type": "obsidian"})
    assert not is_retired_connected_agent_kb(None)


def test_a_local_agent_kb_lists_as_an_error(tmp_path):
    from deeptutor.knowledge.manager import KnowledgeBaseManager

    base = tmp_path / "kbs"
    base.mkdir()
    (base / "kb_config.json").write_text(json.dumps({"knowledge_bases": {
        "cc": {"type": "subagent", "agent_kind": "claude_code", "cwd": "/"},
    }}), encoding="utf-8")
    info = KnowledgeBaseManager(base_dir=str(base)).get_info("cc")
    assert info["status"] == "error"
    assert info["progress"]["message"] == RETIRED_CONNECTED_AGENT_MESSAGE


def test_a_selected_local_agent_kb_never_becomes_the_turn_connection(monkeypatch):
    from types import SimpleNamespace

    from deeptutor.capabilities.subagent import binding
    from deeptutor.multi_user import knowledge_access

    monkeypatch.setattr(
        knowledge_access, "resolve_kb_metadata",
        lambda ref: {"type": "subagent", "agent_kind": "claude_code", "cwd": "/", "name": "cc"},
    )
    context = SimpleNamespace(
        runtime=SimpleNamespace(partner_discussion_group_id="", consult_partner_id=""),
        knowledge_bases=["account:kb:cc"],
    )
    assert binding._resolve(context) is None
