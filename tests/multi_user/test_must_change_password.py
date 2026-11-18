"""An admin-assigned password is temporary: its owner is asked to replace it.

Ported from upstream 35512dccf. The flag is set when an administrator creates
an account or resets someone else's password, cleared when the owner changes
it themselves, and left alone when that change is refused.
"""

from __future__ import annotations

import asyncio

from fastapi import HTTPException, Response
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
