"""Account preset persistence, expansion, and admin HTTP behavior."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def preset_client(mu_isolated_root, monkeypatch, as_user):
    from deeptutor.api.routers import multi_user as multi_user_router
    import deeptutor.api.routers.auth as auth_router
    from deeptutor.api.routers.auth import require_admin
    from deeptutor.multi_user.identity import save_user
    from deeptutor.services.auth import TokenPayload

    admin = save_user("admin", "$2b$12$placeholder", role="admin")
    tokens = {"admin-token": TokenPayload(username="admin", role="admin", user_id=admin["id"])}
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "decode_token", lambda token: tokens.get(token))

    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    app.include_router(multi_user_router.router, prefix="/api/multi-user")
    app.dependency_overrides[require_admin] = lambda: tokens["admin-token"]
    return TestClient(app), admin, tokens


def test_legacy_and_new_standard_users_default_to_standard(seed_user):
    user = seed_user("legacy")
    assert user["preset"] == "standard"


@pytest.mark.parametrize("preset", ["standard", "learner", "custom"])
def test_no_preset_installs_learning_restrictions(preset_client, preset):
    """Spec 2026-11-06: the learner preset no longer adds a learning policy."""
    client, _admin, _tokens = preset_client

    response = client.post(
        "/api/auth/users",
        headers={"Authorization": "Bearer admin-token"},
        json={
            "username": f"{preset}-user",
            "password": "reading-password-1",
            "preset": preset,
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["role"] == "learner"
    from deeptutor.multi_user.grants import grant_path, load_grant

    assert not grant_path(body["user_id"]).exists()
    assert "learning_policy" not in load_grant(body["user_id"])


@pytest.mark.parametrize("preset", ["learner", "custom"])
def test_pocketbase_rejects_presets_it_cannot_enforce(preset_client, monkeypatch, preset):
    import deeptutor.api.routers.auth as auth_router

    client, _admin, _tokens = preset_client
    monkeypatch.setattr(auth_router, "POCKETBASE_ENABLED", True)
    response = client.post(
        "/api/auth/users",
        headers={"Authorization": "Bearer admin-token"},
        json={
            "username": "remote-student",
            "password": "reading-password-1",
            "preset": preset,
        },
    )

    assert response.status_code == 400
    assert "standard preset" in response.json()["detail"]


def test_auth_status_ignores_a_stored_learning_policy(preset_client):
    """A legacy grant with ``learning_policy`` reads as an ordinary learner."""
    import json

    from deeptutor.multi_user.grants import grant_path
    from deeptutor.services.auth import TokenPayload

    client, _admin, tokens = preset_client
    created = client.post(
        "/api/auth/users",
        headers={"Authorization": "Bearer admin-token"},
        json={"username": "student", "password": "reading-password-1", "preset": "learner"},
    ).json()
    path = grant_path(created["user_id"])
    path.write_text(
        json.dumps({"learning_policy": {"allowed_surfaces": ["chat"], "locked_persona": "teacher"}}),
        encoding="utf-8",
    )
    tokens["learner-token"] = TokenPayload(
        username="student", role="user", user_id=created["user_id"]
    )
    response = client.get("/api/auth/status", headers={"Authorization": "Bearer learner-token"})

    assert response.status_code == 200
    assert response.json()["preset"] == "standard"
    assert "learning_policy" not in response.json()
    # The stored value is left in place, unread.
    assert "learning_policy" in json.loads(path.read_text(encoding="utf-8"))
