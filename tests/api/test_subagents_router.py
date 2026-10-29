"""API tests for the subagents router (detect / connect / list / disconnect).

Built on a standalone app with a fake KB manager and a stubbed detector, so it
exercises the HTTP contract without spawning a real CLI or touching the data
tree (mirrors test_knowledge_router's isolation).
"""

# Spec 2026-10-29 P1c: connected local agents are closed. Tests of connecting,
# detecting, syncing and consulting them were removed; the refusals are covered
# by tests/api/test_p1c_connected_agents.py.

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover - optional dependency in lightweight envs
    FastAPI = None
    TestClient = None

pytestmark = pytest.mark.skipif(
    FastAPI is None or TestClient is None, reason="fastapi not installed"
)

if FastAPI is not None and TestClient is not None:
    subagents_module = importlib.import_module("deeptutor.api.routers.subagents")
else:  # pragma: no cover
    subagents_module = None


class _FakeKBManager:
    def __init__(self) -> None:
        self.kbs: dict[str, dict] = {}

    def list_knowledge_bases(self) -> list[str]:
        return sorted(self.kbs)

    def get_metadata(self, name: str | None = None) -> dict:
        return dict(self.kbs.get(name or "", {}))

    def register_subagent_connection(
        self, name, agent_kind, *, cwd="", partner_id="", description=""
    ):
        if name in self.kbs:
            raise ValueError(f"A knowledge base named '{name}' already exists.")
        entry = {
            "path": name,
            "type": "subagent",
            "agent_kind": agent_kind,
            "cwd": cwd,
            "partner_id": partner_id,
            "description": description or f"Connected subagent: {name}",
        }
        self.kbs[name] = entry
        return entry

    def delete_knowledge_base(self, name, confirm=False):
        self.kbs.pop(name, None)
        return True


@pytest.fixture
def client(monkeypatch, tmp_path):
    manager = _FakeKBManager()
    monkeypatch.setattr(subagents_module, "account_kb_manager", lambda: manager)
    monkeypatch.setattr(
        subagents_module,
        "list_backend_kinds",
        lambda: ["claude_code", "codex", "grok", "hermes_remote", "partner"],
    )
    monkeypatch.setattr(subagents_module, "assert_path_allowed", lambda p: Path(p))
    # Isolate settings persistence to a temp file — the PUT path otherwise
    # writes the developer's real data/user/settings/subagent.json.
    monkeypatch.setattr(
        "deeptutor.services.subagent.config._settings_path",
        lambda: tmp_path / "subagent.json",
    )

    async def fake_detect():
        from deeptutor.services.subagent.types import DetectResult

        return [
            DetectResult("claude_code", "Claude Code", available=True, version="2.x"),
            DetectResult("codex", "Codex", available=False, detail="not installed"),
        ]

    monkeypatch.setattr(subagents_module, "detect_all", fake_detect)

    app = FastAPI()
    app.include_router(subagents_module.router, prefix="/api/subagents")
    # Settings PUT is admin-gated; bypass the auth dependency for the contract test.
    app.dependency_overrides[subagents_module.require_admin] = lambda: None
    return TestClient(app)


def test_disconnect_unknown_is_404(client):
    res = client.delete("/api/subagents/connections/nope")
    assert res.status_code == 404

