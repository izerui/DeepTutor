"""Personal MCP in Learning Space is admin-only (port recheck P1, spec 2026-11-06).

Learners get 403 on every ``/api/space/mcp`` route, their turns no longer
connect servers they configured earlier, and the workspace resource picker no
longer offers them. Their data stays on disk, unused.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient
import pytest

from deeptutor.api.routers import auth as auth_router
from deeptutor.multi_user.context import reset_current_user, set_current_user
from tests.api.test_book_shared_access import shared_env  # noqa: F401


def _space_mcp_routes():
    """``(path, methods, dependant)`` across flat and lazily-included routers."""
    from deeptutor.api.main import app

    found = []
    for route in app.routes:
        contexts = getattr(route, "effective_route_contexts", None)
        if callable(contexts):
            items = [
                (c.path, set(c.methods or ()), c.dependant)
                for c in contexts()
                if getattr(c, "dependant", None) is not None
            ]
        elif getattr(route, "dependant", None) is not None and getattr(route, "methods", None):
            items = [(route.path, set(route.methods), route.dependant)]
        else:
            items = []
        found.extend(item for item in items if item[0].startswith("/api/space/mcp"))
    return found


def _calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _calls(dep)


def test_every_space_mcp_route_requires_admin():
    routes = _space_mcp_routes()
    assert routes, "the per-user MCP router is no longer mounted"
    for path, _methods, dependant in routes:
        assert auth_router.require_admin in set(_calls(dependant)), path


@pytest.mark.parametrize("learner", ["alice", "teacher", "reader"])
def test_learners_get_403_on_every_space_mcp_route(shared_env, learner):  # noqa: F811
    from deeptutor.api.main import app

    client = TestClient(app)
    routes = _space_mcp_routes()
    assert len(routes) >= 8
    for template, methods, _dependant in routes:
        path = re.sub(r"\{[^}]+\}", "x", template)
        for method in methods - {"HEAD", "OPTIONS"}:
            response = client.request(
                method, path, headers={"Authorization": f"Bearer {learner}"}, json={}
            )
            assert response.status_code == 403, (method, path, response.status_code)


def test_admin_still_reaches_space_mcp(shared_env, monkeypatch):  # noqa: F811
    from deeptutor.api.main import app
    from deeptutor.api.routers import space_mcp

    class _Manager:
        async def ensure_started(self):
            return None

        async def ensure_scope(self, owner):
            return []

        def status(self, owner="_shared"):
            return []

    monkeypatch.setattr(space_mcp, "get_mcp_manager", lambda: _Manager())
    response = TestClient(app).get(
        "/api/space/mcp/servers", headers={"Authorization": "Bearer root"}
    )
    assert response.status_code == 200, response.text


def _user(role: str):
    from deeptutor.multi_user.models import CurrentUser
    from deeptutor.multi_user.paths import scope_for_user

    user_id = f"u_{role}"
    return CurrentUser(
        id=user_id,
        username=role,
        role=role,
        scope=scope_for_user(user_id, is_admin=role == "admin"),
    )


@pytest.mark.asyncio
async def test_learner_turns_do_not_connect_personal_servers():
    from deeptutor.runtime.providers.scope import ToolScope
    from deeptutor.runtime.providers.view import _owned_tools

    connected: list[str] = []

    class _Manager:
        async def ensure_scope(self, owner):
            connected.append(owner)
            return ["tool"]

    for role, expected in (("student", []), ("admin", ["tool"])):
        token = set_current_user(_user(role))
        try:
            tools = await _owned_tools(_Manager(), ToolScope(owner_id=f"u_{role}"))
        finally:
            reset_current_user(token)
        assert tools == expected
    assert connected == ["u_admin"]


def test_resource_catalog_offers_personal_servers_to_admins_only(monkeypatch):
    from deeptutor.services.mcp.config import MCPConfig
    from deeptutor.services.workspace.resources import resource_catalog

    owned = MCPConfig.model_validate({"servers": {"notes": {"url": "https://mcp.example.com"}}})
    monkeypatch.setattr(
        "deeptutor.services.mcp.user_config.load_user_mcp_config", lambda owner: (owned, [])
    )
    monkeypatch.setattr(
        "deeptutor.services.mcp.config.load_mcp_config", lambda: MCPConfig(servers={})
    )
    monkeypatch.setattr("deeptutor.multi_user.tool_access.allowed_mcp_tools", lambda: None)
    monkeypatch.setattr("deeptutor.multi_user.paths.current_owner_id", lambda: "u_owner")
    monkeypatch.setattr(
        "deeptutor.services.skill.runtime.skill_sources", lambda workspace_id="": []
    )
    monkeypatch.setattr(
        "deeptutor.services.workspace.knowledge.knowledge_catalog", lambda: []
    )
    for role, expected in (("student", []), ("admin", ["account:notes"])):
        token = set_current_user(_user(role))
        try:
            catalog = resource_catalog()
        finally:
            reset_current_user(token)
        assert [item["id"] for item in catalog["mcp"]] == expected

