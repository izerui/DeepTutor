"""Only the lesson page is public, it is served sandboxed, and only to its owner."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from deeptutor.services.auth import TokenPayload
from deeptutor.services.path_service import PathService

LESSON = "workspace/chat/video_lesson/turn-1/artifacts/lesson.html"


def test_only_the_lesson_page_is_public(tmp_path: Path) -> None:
    service = PathService(workspace_root=tmp_path / "data")
    turn = service.get_agent_dir("video_lesson") / "turn-1"
    page = turn / "artifacts" / "lesson.html"
    source = turn / "source" / "Lesson.tsx"
    kit = turn / "source" / "kit.tsx"
    stray = turn / "artifacts" / "notes.html"
    nested = turn / "artifacts" / "x" / "lesson.html"
    for path in (page, source, kit, stray, nested):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    assert service.resolve_public_output_path(page) == page.resolve()
    for private in (source, kit, stray, nested):
        assert service.resolve_public_output_path(private) is None, private


@pytest.fixture
def client_for(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from deeptutor.api.routers import auth as auth_router
    from deeptutor.api.routers import outputs
    from deeptutor.multi_user import paths as multi_user_paths

    admin_root = tmp_path / "data"
    users_root = admin_root / "users"
    monkeypatch.setattr(multi_user_paths, "ADMIN_WORKSPACE_ROOT", admin_root)
    monkeypatch.setattr(multi_user_paths, "USERS_ROOT", users_root)
    monkeypatch.setattr(multi_user_paths, "_path_services", {})
    tokens = {
        "alice-token": TokenPayload(username="alice", role="user", user_id="u_alice"),
        "bob-token": TokenPayload(username="bob", role="user", user_id="u_bob"),
    }
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "decode_token", lambda token: tokens.get(token))
    app = FastAPI()
    app.include_router(outputs.router, prefix="/files/outputs")

    def write(owner: str, relative: str, text: str) -> Path:
        root = PathService(workspace_root=users_root / owner).get_public_outputs_root()
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    return TestClient(app), write


def test_lesson_page_is_served_script_only_sandboxed(client_for) -> None:
    client, write = client_for
    write("u_alice", LESSON, "<!doctype html><script>1</script>")
    with client:
        client.cookies.set("dt_token", "alice-token")
        response = client.get(f"/files/outputs/{LESSON}")

    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    directives = [d.strip() for d in csp.split(";")]
    assert directives[0] == "sandbox allow-scripts"
    assert "allow-same-origin" not in csp and "allow-popups" not in csp
    assert "default-src 'none'" in directives
    assert "connect-src data: blob:" in directives
    assert response.headers["x-content-type-options"] == "nosniff"


def test_other_users_and_private_sources_are_not_served(client_for) -> None:
    client, write = client_for
    write("u_alice", LESSON, "<!doctype html>alice lesson")
    write("u_alice", "workspace/chat/video_lesson/turn-1/source/Lesson.tsx", "secret source")
    with client:
        client.cookies.set("dt_token", "bob-token")
        bob = client.get(f"/files/outputs/{LESSON}")
        client.cookies.set("dt_token", "alice-token")
        source = client.get("/files/outputs/workspace/chat/video_lesson/turn-1/source/Lesson.tsx")
        client.cookies.clear()
        anonymous = client.get(f"/files/outputs/{LESSON}")

    assert bob.status_code == 404 and "alice lesson" not in bob.text
    assert source.status_code == 404 and "secret source" not in source.text
    assert anonymous.status_code == 401


def test_other_html_outputs_keep_their_normal_headers(client_for) -> None:
    client, write = client_for
    relative = "workspace/chat/chat/session-1/code_runs/page.html"
    write("u_alice", relative, "<html></html>")
    with client:
        client.cookies.set("dt_token", "alice-token")
        response = client.get(f"/files/outputs/{relative}")
    assert response.status_code == 200
    assert "content-security-policy" not in response.headers
