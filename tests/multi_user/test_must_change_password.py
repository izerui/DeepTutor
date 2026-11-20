"""An admin-assigned password is temporary: its owner is asked to replace it.

Ported from upstream 35512dccf. The flag is set when an administrator creates
an account or resets someone else's password, cleared when the owner changes
it themselves, and left alone when that change is refused.
"""

from __future__ import annotations

import asyncio

from fastapi import HTTPException, Response, WebSocket
import pytest


def _flag(username: str) -> bool:
    from deeptutor.multi_user.identity import get_user

    return bool((get_user(username) or {}).get("must_change_password", False))


def _token(teaching_users, name: str, role: str):
    from deeptutor.api.routers import auth as auth_router

    return auth_router.TokenPayload(username=name, role=role, user_id=teaching_users[name]["id"])


@pytest.fixture
def auth_on(monkeypatch):
    from deeptutor.api.routers import auth as auth_router
    from deeptutor.services import auth

    monkeypatch.setattr(auth, "AUTH_SECRET", "s" * 48)
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)


def test_admin_created_account_is_flagged_and_listed(teaching_users, auth_on):
    from deeptutor.api.routers import auth as auth_router
    from deeptutor.services.auth import get_user_info

    created = asyncio.run(
        auth_router.admin_create_user(
            auth_router.AdminCreateUserRequest(username="nora", password="temp-password"),
            current=_token(teaching_users, "adam", "admin"),
        )
    )

    assert created["must_change_password"] is True
    assert _flag("nora") is True
    assert get_user_info("nora")["must_change_password"] is True


def test_admin_reset_flags_the_owner_but_not_the_admin_themself(teaching_users, auth_on):
    from deeptutor.api.routers import auth as auth_router

    adam = _token(teaching_users, "adam", "admin")
    asyncio.run(
        auth_router.admin_reset_password(
            "sara", auth_router.AdminPasswordReset(new_password="fresh-password"), current=adam
        )
    )
    asyncio.run(
        auth_router.admin_reset_password(
            "adam", auth_router.AdminPasswordReset(new_password="fresh-password"), current=adam
        )
    )

    assert _flag("sara") is True
    assert _flag("adam") is False


def test_own_change_clears_the_flag_and_a_refused_one_keeps_it(teaching_users, auth_on):
    from deeptutor.api.routers import auth as auth_router
    from deeptutor.multi_user.identity import set_must_change_password

    set_must_change_password("sara", True)
    sara = _token(teaching_users, "sara", "student")

    with pytest.raises(HTTPException):
        asyncio.run(
            auth_router.change_own_password(
                auth_router.ChangeOwnPassword(current_password="wrong", new_password="brand-new-pass"),
                Response(),
                payload=sara,
            )
        )
    assert _flag("sara") is True

    asyncio.run(
        auth_router.change_own_password(
            auth_router.ChangeOwnPassword(
                current_password="password1234", new_password="brand-new-pass"
            ),
            Response(),
            payload=sara,
        )
    )
    assert _flag("sara") is False


def test_login_and_profile_report_the_flag(teaching_users, auth_on, monkeypatch):
    from deeptutor.api.routers import auth as auth_router
    from deeptutor.multi_user import rate_limit
    from deeptutor.multi_user.identity import set_must_change_password

    monkeypatch.setattr(rate_limit, "check_login", lambda *a, **k: None)
    set_must_change_password("sam", True)

    class _Req:
        headers: dict = {}
        client = None

    result = asyncio.run(
        auth_router.login(
            auth_router.LoginRequest(username="sam", password="password1234"),
            _Req(),
            Response(),
        )
    )
    assert result["must_change_password"] is True

    profile = asyncio.run(auth_router.get_profile(payload=_token(teaching_users, "sam", "student")))
    assert profile.must_change_password is True
    assert profile.can_change_password is True


def test_pocketbase_mode_hides_and_refuses_password_changes(teaching_users, auth_on, monkeypatch):
    """Upstream 42cf57bf9: PocketBase owns its passwords; local resets must not apply."""
    from deeptutor.api.routers import auth as auth_router
    from deeptutor.multi_user.identity import get_user

    monkeypatch.setattr(auth_router, "POCKETBASE_ENABLED", True)
    before = get_user("sara")["hash"]

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            auth_router.admin_reset_password(
                "sara",
                auth_router.AdminPasswordReset(new_password="fresh-password"),
                current=_token(teaching_users, "adam", "admin"),
            )
        )
    assert exc.value.status_code == 400
    assert get_user("sara")["hash"] == before
    assert _flag("sara") is False

    profile = asyncio.run(auth_router.get_profile(payload=_token(teaching_users, "sam", "student")))
    assert profile.can_change_password is False


# ---------------------------------------------------------------------------
# Enforcement (port recheck M7): the flag is a server-side gate, not a hint.
# ---------------------------------------------------------------------------


@pytest.fixture
def gated_app(teaching_users, auth_on):
    from fastapi import APIRouter, Depends, FastAPI
    from fastapi.testclient import TestClient

    from deeptutor.api.routers import auth as auth_router
    from deeptutor.services.auth import create_token

    work = APIRouter()

    @work.get("/thing")
    async def thing():
        return {"ok": True}

    @work.post("/thing")
    async def write_thing():
        return {"ok": True}

    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    app.include_router(work, prefix="/api/work", dependencies=[Depends(auth_router.require_auth)])

    @app.websocket("/ws/work")
    async def ws_work(ws: WebSocket):
        user_token = await auth_router.ws_require_auth(ws)
        if user_token is auth_router.ws_auth_failed:
            return
        await ws.accept()
        await ws.send_json({"ok": True})
        await ws.close()

    def headers(name: str) -> dict[str, str]:
        record = teaching_users[name]
        return {"Authorization": f"Bearer {create_token(name, record['role'], record['id'])}"}

    return TestClient(app), headers


def test_decoded_token_carries_the_flag(teaching_users, auth_on):
    from deeptutor.multi_user.identity import set_must_change_password
    from deeptutor.services.auth import create_token, decode_token

    record = teaching_users["sara"]
    token = create_token("sara", record["role"], record["id"])
    assert decode_token(token).must_change_password is False
    set_must_change_password("sara", True)
    assert decode_token(token).must_change_password is True


def test_flagged_account_is_refused_everything_but_the_password_surface(gated_app):
    from deeptutor.api.routers import auth as auth_router
    from deeptutor.multi_user.identity import set_must_change_password

    client, headers = gated_app
    assert client.get("/api/work/thing", headers=headers("sara")).status_code == 200
    set_must_change_password("sara", True)
    sara = headers("sara")

    for method in ("GET", "POST"):
        refused = client.request(method, "/api/work/thing", headers=sara)
        assert refused.status_code == 403
        assert refused.json()["detail"] == auth_router.PASSWORD_CHANGE_DETAIL
        assert refused.headers[auth_router.PASSWORD_CHANGE_HEADER] == "required"

    # Who am I, read the profile, change the password, sign out: still open.
    assert client.get("/api/auth/status", headers=sara).status_code == 200
    assert client.get("/api/auth/profile", headers=sara).json()["must_change_password"] is True
    assert client.get("/api/auth/profile/learner-profile", headers=sara).status_code == 200
    wrong = client.put(
        "/api/auth/profile/password",
        headers=sara,
        json={"current_password": "wrong-password", "new_password": "brand-new-pass"},
    )
    assert wrong.status_code == 400  # reached the handler, not the gate
    assert client.post("/api/auth/logout", headers=sara).status_code == 200
    # Other profile writes are not part of the change-password surface.
    assert client.put("/api/auth/profile", headers=sara, json={}).status_code == 403

    # Other accounts are unaffected.
    assert client.get("/api/work/thing", headers=headers("sam")).status_code == 200


def test_changing_the_password_lifts_the_gate(gated_app):
    from deeptutor.multi_user.identity import set_must_change_password

    client, headers = gated_app
    set_must_change_password("sara", True)
    changed = client.put(
        "/api/auth/profile/password",
        headers=headers("sara"),
        json={"current_password": "password1234", "new_password": "brand-new-pass"},
    )
    assert changed.status_code == 200, changed.text
    # The response re-issues the session cookie; the old token is void.
    fresh = changed.cookies.get("dt_token") or client.cookies.get("dt_token")
    assert fresh
    response = client.get("/api/work/thing", headers={"Authorization": f"Bearer {fresh}"})
    assert response.status_code == 200


def test_flagged_account_cannot_open_sockets(gated_app):
    from starlette.websockets import WebSocketDisconnect

    from deeptutor.multi_user.identity import set_must_change_password

    client, headers = gated_app
    token = headers("sara")["Authorization"].split()[1]
    with client.websocket_connect(f"/ws/work?token={token}") as ws:
        assert ws.receive_json() == {"ok": True}
    set_must_change_password("sara", True)
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(f"/ws/work?token={token}") as ws:
            ws.receive_json()
    assert closed.value.code == 4003


def test_admin_created_account_must_change_before_using_the_api(gated_app, teaching_users):
    """End to end: admin creates nora with a temporary password; nora is gated."""
    from deeptutor.api.routers import auth as auth_router
    from deeptutor.services.auth import create_token, get_user_info

    asyncio.run(
        auth_router.admin_create_user(
            auth_router.AdminCreateUserRequest(username="nora", password="temp-password"),
            current=_token(teaching_users, "adam", "admin"),
        )
    )
    client, _ = gated_app
    info = get_user_info("nora")
    token = create_token("nora", info["role"], info["id"])
    response = client.get("/api/work/thing", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
