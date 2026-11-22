from types import SimpleNamespace

from fastapi import Depends, FastAPI, Response
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from deeptutor.api.routers import settings
from deeptutor.services.settings.page_customization import ChatPageCustomization, LearningPageCustomization
from deeptutor.services.workspace.context import WorkspaceScope, workspace_context


def test_anonymous_requests_cannot_read_or_write_personal_pages(monkeypatch):
    from deeptutor.api.routers import auth

    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    app = FastAPI()
    app.include_router(settings.public_router, prefix="/api/settings")
    app.include_router(settings.router, prefix="/api/settings", dependencies=[Depends(auth.require_auth)])
    client = TestClient(app)
    base = "/api/settings/page-customization"
    assert client.get(base).status_code == 401
    for section in ["chat", "learning"]:
        assert client.put(f"{base}/{section}", json={}).status_code == 401


@pytest.mark.asyncio
async def test_private_choices_follow_the_account_across_workspaces(monkeypatch, tmp_path):
    account = tmp_path / "alice"
    monkeypatch.setattr(settings, "get_account_path_service", lambda: SimpleNamespace(
        get_settings_file=lambda name: account / f"{name}.json"
    ))
    monkeypatch.setattr(settings, "_settings_file", lambda: account / "interface.json")
    with workspace_context(WorkspaceScope("a", account, account / "a")):
        await settings.update_learning_page_customization(LearningPageCustomization(hidden_features=["mastery"]))
        await settings.update_chat_page_customization(ChatPageCustomization(
            controls={"voice": False}, hidden_modes=["deep_research"],
            starters={"explain": [{"label": "Personal", "prompt": "Private prompt"}], "solve": []},
        ))
    with workspace_context(WorkspaceScope("b", account, account / "b")):
        response = Response()
        saved = await settings.get_page_customization(response)
        assert "no-store" in response.headers["cache-control"]
        assert saved.chat.starters["explain"][0].prompt == "Private prompt"
        assert saved.chat.controls.voice is False and saved.chat.controls.skills is True
        assert saved.chat.starters["solve"] == []
        assert saved.learning.hidden_features == ["mastery"]
        assert "page_customization" not in await settings.get_ui_settings()
    account = tmp_path / "bob"
    other = await settings.get_page_customization(Response())
    assert other.chat.starters == {} and other.learning.hidden_features == []


@pytest.mark.parametrize("payload", [
    {"starters": {"unknown": []}},
    {"starters": {"explain": [{"label": " ", "prompt": "ok"}]}},
    {"starters": {"explain": [{"label": "ok", "prompt": "x" * 4001}]}},
    {"starters": {"explain": [{"label": "ok", "prompt": "ok"}] * 21}},
    {"controls": {"install_module": True}},
])
def test_customization_rejects_invalid_input(payload):
    with pytest.raises(ValidationError):
        ChatPageCustomization.model_validate(payload)
