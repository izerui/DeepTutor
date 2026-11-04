"""P3b: open sign-up creates students; closed sign-up admits nobody (invites are ignored)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def auth_client(seed_user, monkeypatch):
    import deeptutor.api.routers.auth as auth_router
    from deeptutor.services.auth import TokenPayload

    boss = seed_user("boss", role="admin")
    tina = seed_user("tina", role="teacher")
    tokens = {"admin-token": TokenPayload(username="boss", role="admin", user_id=boss["id"])}
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "POCKETBASE_ENABLED", False)
    monkeypatch.setattr(auth_router, "decode_token", lambda token: tokens.get(token))
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    return TestClient(app), tina


def test_anyone_signs_up_as_a_student_while_registration_is_open(auth_client):
    client, _ = auth_client
    assert client.get("/api/auth/registration").json() == {"open": True}
    response = client.post("/api/auth/register", json={"username": "newbie", "password": "password-123"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["role"] == "student" and body["is_admin"] is False
    assert "joined_class" not in body and "invite_error" not in body


def test_closed_registration_admits_nobody_even_with_an_invite(auth_client):
    from deeptutor.services.auth import list_users

    client, _ = auth_client
    assert client.put(
        "/api/auth/registration", json={"open": False}, headers={"Authorization": "Bearer admin-token"},
    ).json() == {"open": False}
    for name, extra in (("walkin", {}), ("invited", {"invite": "some-old-link"})):
        body = {"username": name, "password": "password-123", **extra}
        refused = client.post("/api/auth/register", json=body)
        assert refused.status_code == 403
        assert refused.json()["detail"] == "Registration is closed. Ask an administrator for an account."
    assert not {"walkin", "invited"} & {u["username"] for u in list_users()}


def test_open_registration_ignores_an_invite(auth_client):
    """An open sign-up does not depend on the link; it joins nothing."""
    client, _ = auth_client
    response = client.post(
        "/api/auth/register", json={"username": "ghost", "password": "password-123", "invite": "nope"},
    )
    assert response.status_code == 201
    assert "joined_class" not in response.json() and "invite_error" not in response.json()


def test_only_admins_change_the_registration_setting(auth_client):
    client, _ = auth_client
    assert client.put("/api/auth/registration", json={"open": False}).status_code == 401
