"""Device credentials (pairing code + PIN, daily limit, heartbeat) were removed.

Product decision 2026-11-06: the endpoints are gone, and a session token that
was minted by a device login (non-empty ``dcid`` claim) is no longer honoured.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def env(mu_isolated_root, monkeypatch):
    from deeptutor.api.routers import auth as auth_router
    from deeptutor.multi_user.identity import save_user
    from deeptutor.services import auth as auth_service
    from deeptutor.services.auth import create_token, hash_password

    admin = save_user("root", hash_password("root-password"), role="admin")
    learner = save_user("learner", hash_password("learner-password"))
    monkeypatch.setattr(auth_service, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_service, "AUTH_SECRET", "device-removed-test-secret")
    monkeypatch.setattr(auth_service, "POCKETBASE_ENABLED", False)
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "POCKETBASE_ENABLED", False)

    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    return {
        "client": TestClient(app),
        "admin": admin,
        "learner": learner,
        "admin_token": create_token("root", "admin", admin["id"]),
        "secret": "device-removed-test-secret",
    }


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/auth/device-login", {"pairing_code": "dc_abcdefgh", "pin": "123456"}),
        ("post", "/api/auth/device/heartbeat", None),
        ("get", "/api/auth/devices", None),
        (
            "post",
            "/api/auth/devices",
            {
                "user_id": "x",
                "device_name": "Tablet",
                "expires_in_days": 30,
                "daily_limit_minutes": 30,
            },
        ),
        ("delete", "/api/auth/devices/dc_missing", None),
    ],
)
def test_removed_device_endpoints_are_gone(env, method, path, body):
    kwargs = {"headers": _auth(env["admin_token"])}
    if body is not None:
        kwargs["json"] = body
    response = getattr(env["client"], method)(path, **kwargs)
    assert response.status_code in (404, 405), (path, response.status_code, response.text)


def test_removed_device_routes_are_not_registered():
    from deeptutor.api.routers import auth as auth_router

    paths = {getattr(route, "path", "") for route in auth_router.router.routes}
    assert not any("device" in path for path in paths), paths


def _legacy_token(env, *, username: str, user_id: str, role: str, dcid: str) -> str:
    from jose import jwt

    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": username,
            "role": role,
            "uid": user_id,
            "dcid": dcid,
            "dcs": "nonce" if dcid else "",
            "cv": 0,
            "exp": now + timedelta(hours=1),
            "iat": now,
        },
        env["secret"],
        algorithm="HS256",
    )


def test_token_from_a_device_login_is_rejected(env):
    from deeptutor.services.auth import decode_token

    learner = env["learner"]
    token = _legacy_token(
        env, username="learner", user_id=learner["id"], role="user", dcid="dc_old"
    )
    assert decode_token(token) is None
    assert env["client"].get("/api/auth/profile", headers=_auth(token)).status_code == 401


def test_normal_tokens_are_unaffected(env):
    from deeptutor.services.auth import create_token, decode_token

    learner = env["learner"]
    # Minted today: no device claims at all.
    fresh = create_token("learner", "user", learner["id"])
    from jose import jwt

    claims = jwt.get_unverified_claims(fresh)
    assert "dcid" not in claims and "dcs" not in claims
    assert decode_token(fresh).user_id == learner["id"]
    # Minted before the removal: empty ``dcid``/``dcs`` claims stay valid.
    legacy = _legacy_token(env, username="learner", user_id=learner["id"], role="user", dcid="")
    payload = decode_token(legacy)
    assert payload is not None and payload.username == "learner"
    assert env["client"].get("/api/auth/profile", headers=_auth(legacy)).status_code == 200
