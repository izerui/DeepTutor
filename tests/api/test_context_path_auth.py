"""Verify learner permission gate with context path (root_path=/kaoyan).

Calls ``require_learning_surface`` through a real FastAPI dependency chain,
with ``require_auth`` overridden to skip token validation and a mocked
learner policy that only allows the ``reading`` surface.

The "with prefix" tests request ``/kaoyan/api/...`` to match uvicorn's
``full_path = root_path + path`` behavior (h11_impl.py:206), so
``scope["path"]`` is ``"/kaoyan/api/..."`` — the same value the gate sees
in production. Without ``get_route_path()`` these tests would wrongly pass
because the classifier would never match a surface, and
``assert_learning_surface("")`` would deny everything uniformly.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI
from fastapi import status as http_status
from starlette.testclient import TestClient

from deeptutor.api.routers.auth import require_auth, require_learning_surface
from deeptutor.multi_user.context import set_current_user, reset_current_user
from deeptutor.multi_user.models import CurrentUser, UserScope


READING_ONLY_POLICY = {"allowed_surfaces": ["reading"]}


async def _fake_require_auth():
    return None


def _make_app(root_path: str = "") -> FastAPI:
    app = FastAPI(root_path=root_path)
    app.dependency_overrides[require_auth] = _fake_require_auth

    @app.get("/api/reading/materials", dependencies=[Depends(require_learning_surface)])
    async def reading_endpoint():
        return {"ok": True}

    @app.get("/api/books", dependencies=[Depends(require_learning_surface)])
    async def books_endpoint():
        return {"ok": True}

    @app.get("/api/settings/workspace", dependencies=[Depends(require_learning_surface)])
    async def admin_endpoint():
        return {"ok": True}

    return app


@pytest.fixture()
def learner_context():
    """Set up a non-admin user context so current_learning_policy() resolves."""
    learner = CurrentUser(
        id="learner-001",
        username="test-learner",
        role="user",
        scope=UserScope(kind="user", user_id="learner-001", root=Path("/tmp/test")),
    )
    token = set_current_user(learner)
    yield learner
    reset_current_user(token)


def _patch_policy():
    return patch(
        "deeptutor.multi_user.learning_access.current_learning_policy",
        return_value=READING_ONLY_POLICY,
    )


class TestContextPathLearnerPermission:
    """require_learning_surface must use get_route_path() to strip root_path
    before classifying.  The "with prefix" tests request /kaoyan/api/... so
    scope["path"] is "/kaoyan/api/...", matching uvicorn's real behavior."""

    # -- With root_path: request the full /kaoyan/api/... path so scope["path"]
    #    includes the prefix, just like uvicorn would set it.

    def test_reading_allowed_with_prefix(self, learner_context):
        app = _make_app(root_path="/kaoyan")
        client = TestClient(app, root_path="/kaoyan")
        with _patch_policy():
            r = client.get("/kaoyan/api/reading/materials")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_books_denied_with_prefix(self, learner_context):
        app = _make_app(root_path="/kaoyan")
        client = TestClient(app, root_path="/kaoyan")
        with _patch_policy():
            r = client.get("/kaoyan/api/books")
        assert r.status_code == http_status.HTTP_403_FORBIDDEN, (
            f"Expected 403, got {r.status_code}: {r.text}"
        )

    def test_admin_endpoint_denied_with_prefix(self, learner_context):
        app = _make_app(root_path="/kaoyan")
        client = TestClient(app, root_path="/kaoyan")
        with _patch_policy():
            r = client.get("/kaoyan/api/settings/workspace")
        assert r.status_code == http_status.HTTP_403_FORBIDDEN, (
            f"Expected 403, got {r.status_code}: {r.text}"
        )

    # -- Without root_path: baseline to confirm the gate still works normally.

    def test_reading_allowed_without_prefix(self, learner_context):
        app = _make_app(root_path="")
        client = TestClient(app)
        with _patch_policy():
            r = client.get("/api/reading/materials")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_books_denied_without_prefix(self, learner_context):
        app = _make_app(root_path="")
        client = TestClient(app)
        with _patch_policy():
            r = client.get("/api/books")
        assert r.status_code == http_status.HTTP_403_FORBIDDEN, (
            f"Expected 403, got {r.status_code}: {r.text}"
        )

    def test_admin_endpoint_denied_without_prefix(self, learner_context):
        app = _make_app(root_path="")
        client = TestClient(app)
        with _patch_policy():
            r = client.get("/api/settings/workspace")
        assert r.status_code == http_status.HTTP_403_FORBIDDEN, (
            f"Expected 403, got {r.status_code}: {r.text}"
        )


class TestRegressionGuard:
    """Prove the fix is load-bearing: reverting get_route_path() to
    request.scope["path"] would make the prefixed reading test fail
    (surface="" → 403 instead of 200)."""

    def test_raw_scope_path_breaks_classification_with_prefix(self, learner_context):
        """Directly call the classifier with the raw scope path that uvicorn
        produces.  Without get_route_path stripping, this returns empty."""
        from deeptutor.api.routers.auth import _learning_surface_for_path

        # This is what scope["path"] looks like in uvicorn --root-path /kaoyan
        assert _learning_surface_for_path("/kaoyan/api/reading/materials") == "", (
            "Raw prefixed path should NOT match any surface "
            "(this is the bug get_route_path fixes)"
        )
        # After stripping, it should match
        from starlette._utils import get_route_path

        stripped = get_route_path({"path": "/kaoyan/api/reading/materials", "root_path": "/kaoyan"})
        assert _learning_surface_for_path(stripped) == "reading"


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
