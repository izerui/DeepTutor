"""Regression tests for learner surfaces and restriction preset changes.

Two failure modes covered:

1. Knowledge Center browsing needs a precise GET allowlist. Admin diagnostics
   and engine configuration endpoints share the same URL prefix.
2. Assigning guardian restrictions to a `standard`-preset account left the
   account in a `standard` preset + `learning_policy` mix: the frontend then
   rendered the full admin shell and every surface request 403'd. The PUT
   restrictions handler now seeds the default policy and flips the preset to
   ``learner``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient
import pytest

from deeptutor.api.routers.auth import _learning_surface_for_path


@pytest.mark.parametrize(
    ("path", "method", "expected"),
    [
        # Pre-existing mappings keep working.
        ("/api/reading/materials", "GET", "reading"),
        ("/api/courses", "GET", "reading"),
        ("/api/dashboard/learning-library/materials", "GET", "reading"),
        ("/api/dashboard/learning-library/reading", "GET", "reading"),
        ("/api/books", "GET", "books"),
        ("/api/books/bk_123", "GET", "books"),
        ("/api/books/quiz-attempt", "POST", "books"),
        ("/api/dashboard/learning-library/books", "GET", "books"),
        ("/api/books-private", "GET", ""),
        ("/api/chat/sessions", "GET", "chat"),
        ("/api/question/generate", "POST", "chat"),
        ("/api/sessions/abc", "GET", "chat"),
        ("/api/task-board", "GET", "chat"),
        ("/api/task-board/cards", "POST", "chat"),
        ("/api/task-board/cards/card-1", "PATCH", "chat"),
        ("/api/task-board-private", "GET", ""),
        # Mastery Path: the learner's own per-user progress, all methods.
        ("/api/mastery-paths/topics", "GET", "chat"),
        ("/api/mastery-paths/topics/index", "GET", "chat"),
        ("/api/mastery-paths/progress/book-1", "GET", "chat"),
        ("/api/mastery-paths/progress/book-1", "PATCH", "chat"),
        ("/api/mastery-paths/progress/book-1/redo", "POST", "chat"),
        ("/api/dashboard/learning-library/mastery", "GET", "chat"),
        # KB writes stay denied, even when the route is otherwise browsable.
        ("/api/knowledge-bases", "POST", ""),
        ("/api/knowledge-bases/kb1/upload", "POST", ""),
        ("/api/knowledge-bases/kb1/files/a.pdf", "DELETE", ""),
        # Everything else still default-denies.
        ("/api/settings", "GET", ""),
        # Model choice for a chat turn. Not a prefix: other settings stay denied.
        ("/api/settings/llm-options", "GET", "chat"),
        ("/api/settings/llm-options", "POST", ""),
        ("/api/settings/llm-options/extra", "GET", ""),
        ("/api/system/status", "GET", ""),
        ("/api/dashboard/learning-library/chats", "GET", ""),
        ("/api/dashboard/learning-library/materials-private", "GET", ""),
        ("/api/partners", "GET", ""),
        ("/api/memory/overview", "GET", ""),
        ("", "GET", ""),
    ],
)
def test_learning_surface_map(path: str, method: str, expected: str) -> None:
    assert _learning_surface_for_path(path, method) == expected


@pytest.mark.parametrize(
    ("path", "route_path", "expected"),
    [
        ("/api/knowledge-bases", "/api/knowledge-bases", "reading"),
        ("/api/knowledge-bases/list", "/api/knowledge-bases/list", "reading"),
        ("/api/knowledge-bases/kb1", "/api/knowledge-bases/{kb_name}", "reading"),
        ("/api/knowledge-bases/kb1/files", "/api/knowledge-bases/{kb_name}/files", "reading"),
        (
            "/api/knowledge-bases/kb1/files/a.pdf",
            "/api/knowledge-bases/{kb_name}/files/{filename:path}",
            "reading",
        ),
        (
            "/api/knowledge-bases/kb1/file-preview-text/a.pdf",
            "/api/knowledge-bases/{kb_name}/file-preview-text/{filename:path}",
            "reading",
        ),
        ("/api/knowledge-bases/kb1/progress", "/api/knowledge-bases/{kb_name}/progress", "reading"),
        ("/api/knowledge-bases/health", "/api/knowledge-bases/health", ""),
        ("/api/knowledge-bases/configs", "/api/knowledge-bases/configs", ""),
        ("/api/knowledge-bases/default", "/api/knowledge-bases/default", ""),
        (
            "/api/knowledge-bases/rag-pipelines/lightrag/config",
            "/api/knowledge-bases/rag-pipelines/lightrag/config",
            "",
        ),
        ("/api/knowledge-bases/kb1/config", "/api/knowledge-bases/{kb_name}/config", ""),
        (
            "/api/knowledge-bases/kb1/reindex-config",
            "/api/knowledge-bases/{kb_name}/reindex-config",
            "",
        ),
        (
            "/api/knowledge-bases/kb1/linked-folders",
            "/api/knowledge-bases/{kb_name}/linked-folders",
            "",
        ),
        (
            "/api/knowledge-bases/kb1/github-sources",
            "/api/knowledge-bases/{kb_name}/github-sources",
            "",
        ),
        (
            "/api/knowledge-bases/kb1/web-sources",
            "/api/knowledge-bases/{kb_name}/web-sources",
            "",
        ),
    ],
)
def test_learner_kb_get_allowlist(path: str, route_path: str, expected: str) -> None:
    assert _learning_surface_for_path(path, "GET", route_path=route_path) == expected
    if expected:
        assert _learning_surface_for_path(path, "HEAD", route_path=route_path) == ""
        assert _learning_surface_for_path(path, "OPTIONS", route_path=route_path) == ""
        assert _learning_surface_for_path(path, "POST", route_path=route_path) == ""
        assert _learning_surface_for_path(path, "GET") == ""


@pytest.mark.parametrize(
    ("path", "method", "route_path", "expected"),
    [
        # UI preferences and the per-user settings draft.
        ("/api/settings/ui", "PUT", "/api/settings/ui", "chat"),
        ("/api/settings/draft", "PUT", "/api/settings/draft", "chat"),
        ("/api/settings/draft", "DELETE", "/api/settings/draft", "chat"),
        # Workspace self-management.
        ("/api/settings/workspace", "PUT", "/api/settings/workspace", "chat"),
        (
            "/api/settings/workspace/validate",
            "POST",
            "/api/settings/workspace/validate",
            "chat",
        ),
        (
            "/api/settings/workspace/registrations",
            "POST",
            "/api/settings/workspace/registrations",
            "chat",
        ),
        (
            "/api/settings/workspace/registrations/ws_1",
            "PATCH",
            "/api/settings/workspace/registrations/{workspace_id}",
            "chat",
        ),
        # Admin-grade neighbors under the same prefixes stay denied.
        ("/api/settings/ui", "POST", "/api/settings/ui", ""),
        ("/api/settings/draft", "GET", "/api/settings/draft", ""),
        (
            "/api/settings/workspace/registrations",
            "GET",
            "/api/settings/workspace/registrations",
            "",
        ),
        (
            "/api/settings/workspace/data/migrate",
            "POST",
            "/api/settings/workspace/data/migrate",
            "",
        ),
        (
            "/api/settings/workspace/data/export",
            "POST",
            "/api/settings/workspace/data/export",
            "",
        ),
        (
            "/api/settings/workspace/registrations/migrate-root",
            "POST",
            "/api/settings/workspace/registrations/migrate-root",
            "",
        ),
        (
            "/api/settings/workspace/registrations/ws_1/migrate",
            "POST",
            "/api/settings/workspace/registrations/{workspace_id}/migrate",
            "",
        ),
        (
            "/api/settings/workspace/registrations/system-snapshot",
            "POST",
            "/api/settings/workspace/registrations/system-snapshot",
            "",
        ),
        (
            "/api/settings/workspace/knowledge-bases/move",
            "POST",
            "/api/settings/workspace/knowledge-bases/move",
            "",
        ),
    ],
)
def test_learner_settings_write_allowlist(
    path: str, method: str, route_path: str, expected: str
) -> None:
    assert _learning_surface_for_path(path, method, route_path=route_path) == expected
    if expected:
        # Without the resolved route template the guard must stay denied:
        # prefix matching alone cannot tell these from admin operations.
        assert _learning_surface_for_path(path, method) == ""


def test_route_template_is_passed_to_surface_guard(monkeypatch) -> None:
    import asyncio
    from types import SimpleNamespace

    from deeptutor.api.routers.auth import require_learning_surface
    from deeptutor.multi_user import learning_access

    observed = []
    monkeypatch.setattr(learning_access, "assert_learning_surface", observed.append)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/knowledge-bases/health",
        "headers": [],
        "route": SimpleNamespace(path="/api/knowledge-bases/health"),
    }
    asyncio.run(require_learning_surface(Request(scope), None))
    assert observed == [""]


def test_fastapi_guard_denies_kb_diagnostics_but_allows_browsing(monkeypatch) -> None:
    from deeptutor.api.routers.auth import require_auth, require_learning_surface
    from deeptutor.multi_user import learning_access

    def deny_unmapped(surface: str) -> None:
        if not surface:
            raise PermissionError("denied")

    monkeypatch.setattr(learning_access, "assert_learning_surface", deny_unmapped)
    app = FastAPI()
    app.dependency_overrides[require_auth] = lambda: None

    @app.get("/api/knowledge-bases/health", dependencies=[Depends(require_learning_surface)])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/knowledge-bases/{kb_name}", dependencies=[Depends(require_learning_surface)])
    def details(kb_name: str) -> dict[str, str]:
        return {"name": kb_name}

    client = TestClient(app)
    assert client.get("/api/knowledge-bases/health").status_code == 403
    assert client.get("/api/knowledge-bases/kb1").json() == {"name": "kb1"}


def test_set_preset_checks_expected_user_id(mu_isolated_root, seed_user) -> None:
    from deeptutor.multi_user.identity import get_user_by_id, set_preset

    seed_user("bootstrap-admin")
    record = seed_user("student-standard")
    assert set_preset("student-standard", "learner", expected_user_id="different") is False
    _username, unchanged = get_user_by_id(record["id"])
    assert unchanged["preset"] == "standard"


@pytest.mark.parametrize("surfaces, expected", [(["reading"], 200), (["chat"], 403)])
@pytest.mark.parametrize("nested_router", [False, True])
def test_learner_proxy_list_obeys_reading_policy_over_http(
    monkeypatch, mu_isolated_root, surfaces, expected, nested_router
) -> None:
    from deeptutor.api.routers import auth, knowledge
    from deeptutor.multi_user import learning_access
    from deeptutor.multi_user.identity import save_user
    from deeptutor.services.auth import TokenPayload, hash_password

    save_user("admin", hash_password("admin-password"), role="admin")
    learner = save_user("student", hash_password("student-password"), preset="learner")
    token = TokenPayload(username="student", role="user", user_id=learner["id"])
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "decode_token", lambda _token: token)
    monkeypatch.setattr(
        learning_access,
        "learning_policy_for_user",
        lambda _user_id, **_kwargs: {"allowed_surfaces": surfaces},
    )
    calls = []

    async def collection():
        calls.append(True)
        return []

    monkeypatch.setattr(knowledge, "list_knowledge_bases", collection)
    app = FastAPI()
    parent = APIRouter() if nested_router else app
    parent.include_router(
        knowledge.router, prefix="/api", dependencies=[Depends(auth.require_learning_surface)]
    )
    if nested_router:
        app.include_router(parent)
    client = TestClient(app)
    headers = {"Authorization": "Bearer student-token"}
    response = client.get("/api/knowledge-bases/list", headers=headers)
    assert response.status_code == expected, response.text
    assert calls == ([True] if expected == 200 else [])
    if expected == 200:
        assert response.json() == []
    assert client.get("/api/knowledge-bases/health", headers=headers).status_code == 403
