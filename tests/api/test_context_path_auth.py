"""Context-path (root_path=/kaoyan) request classification.

Requests to ``/kaoyan/api/...`` match uvicorn's ``full_path = root_path +
path`` behavior, so ``scope["path"]`` carries the prefix the way it does in
production; classification must strip it with ``get_route_path()``.
"""

from __future__ import annotations

import pytest


def _prefixed_request(path: str, method: str = "GET"):
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": method,
            "path": f"/kaoyan{path}",
            "root_path": "/kaoyan",
            "query_string": b"",
            "headers": [],
            "scheme": "http",
            "server": ("localhost", 8001),
        }
    )


class TestWorkspaceScopeWithPrefix:
    """``_install_request_workspace`` classifies requests by route path, so a
    context-path deployment must strip root_path before matching prefixes."""

    @pytest.fixture()
    def calls(self, monkeypatch):
        from types import SimpleNamespace

        seen: dict[str, list] = {"scope": [], "lease": []}

        def fake_install(selected):
            seen["scope"].append(selected)
            return SimpleNamespace(archived=False)

        def fake_acquire():
            seen["lease"].append(True)
            return object()

        monkeypatch.setattr(
            "deeptutor.services.workspace.context.install_workspace_scope", fake_install
        )
        monkeypatch.setattr(
            "deeptutor.services.workspace.activity.acquire_activity", fake_acquire
        )
        return seen

    def test_task_board_events_stream_holds_no_activity_lease(self, calls):
        from deeptutor.api.routers.auth import _install_request_workspace

        _install_request_workspace(_prefixed_request("/api/task-board/events"))
        # Management request: no workspace selected, and the long-lived SSE
        # stream must not pin the shared lease that migrations wait on.
        assert calls["scope"] == [None]
        assert calls["lease"] == []

    def test_settings_request_is_management(self, calls):
        from deeptutor.api.routers.auth import _install_request_workspace

        _install_request_workspace(_prefixed_request("/api/settings/workspace", "PATCH"))
        assert calls["scope"] == [None]
        assert calls["lease"] == []

    def test_regular_request_still_takes_the_lease(self, calls):
        from deeptutor.api.routers.auth import _install_request_workspace

        _install_request_workspace(_prefixed_request("/api/sessions"))
        assert calls["lease"] == [True]


@pytest.mark.asyncio
async def test_expired_login_on_prefixed_invidious_callback_redirects(monkeypatch):
    from starlette.responses import Response

    from deeptutor.api.main import selective_access_log

    monkeypatch.setenv("CONTEXT_PATH", "/kaoyan")

    async def denied(_request):
        return Response(status_code=401)

    response = await selective_access_log(
        _prefixed_request("/api/video-learning/invidious/account/callback"), denied
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/kaoyan/reading?account=authorization_login_required"
