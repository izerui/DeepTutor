"""P3b: open sign-up creates students; an invite link still admits when closed."""

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


def _invite(as_user, tina):
    from deeptutor.classes.service import ClassService

    with as_user(tina["id"], role="teacher", username="tina"):
        cls = ClassService().create("Physics 1")
        return cls["id"], ClassService().regenerate_link(cls["id"])["token"]


def test_anyone_signs_up_as_a_student_while_registration_is_open(auth_client):
    client, _ = auth_client
    assert client.get("/api/auth/registration").json() == {"open": True}
    response = client.post("/api/auth/register", json={"username": "newbie", "password": "password-123"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["role"] == "student" and body["is_admin"] is False and body["joined_class"] is None


def test_closed_registration_still_admits_an_invited_student(auth_client, as_user):
    from deeptutor.classes.service import student_class_ids

    client, tina = auth_client
    assert client.put(
        "/api/auth/registration", json={"open": False}, headers={"Authorization": "Bearer admin-token"},
    ).json() == {"open": False}
    refused = client.post("/api/auth/register", json={"username": "walkin", "password": "password-123"})
    assert refused.status_code == 403
    class_id, token = _invite(as_user, tina)
    invited = client.post(
        "/api/auth/register", json={"username": "invited", "password": "password-123", "invite": token},
    )
    assert invited.status_code == 201, invited.text
    body = invited.json()
    assert body["joined_class"]["class_name"] == "Physics 1"
    assert student_class_ids(body["user_id"]) == {class_id}


def test_a_bad_invite_is_refused_before_any_account_is_made(auth_client):
    """While registration is closed, only a valid invite admits a new account."""
    from deeptutor.multi_user.registration import set_public_registration
    from deeptutor.services.auth import list_users

    client, _ = auth_client
    set_public_registration(False)
    response = client.post(
        "/api/auth/register", json={"username": "ghost", "password": "password-123", "invite": "nope"},
    )
    assert response.status_code == 404
    assert "ghost" not in {u["username"] for u in list_users()}


def test_open_registration_ignores_a_dead_invite(auth_client):
    """Spec 2026-10-30 closure: an open sign-up does not depend on the link; it joins nothing."""
    client, _ = auth_client
    response = client.post(
        "/api/auth/register", json={"username": "ghost", "password": "password-123", "invite": "nope"},
    )
    assert response.status_code == 201
    assert response.json()["joined_class"] is None and response.json()["invite_error"]


def test_only_admins_change_the_registration_setting(auth_client):
    client, _ = auth_client
    assert client.put("/api/auth/registration", json={"open": False}).status_code == 401
