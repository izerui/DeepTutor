"""P1a / S2 stopgap: no second administrator until per-administrator storage (P1b)."""

from __future__ import annotations

import asyncio

from fastapi import HTTPException
import pytest

from deeptutor.api.routers.auth import SetRoleRequest, update_user_role
from deeptutor.services.auth import TokenPayload, list_users


def _roles():
    return {user["username"]: user["role"] for user in list_users()}


def test_promoting_a_second_admin_is_refused(seed_user):
    seed_user("boss", role="admin")
    seed_user("tina", role="teacher")
    with pytest.raises(HTTPException) as caught:
        asyncio.run(update_user_role(
            "tina", SetRoleRequest(role="admin"), TokenPayload(username="boss", role="admin"),
        ))
    assert caught.value.status_code == 409
    assert caught.value.detail == (
        "Only one administrator is supported until each administrator has separate data."
    )
    assert _roles()["tina"] == "teacher"


def test_other_role_changes_still_work(seed_user):
    seed_user("boss", role="admin")
    seed_user("tina", role="teacher")
    asyncio.run(update_user_role(
        "tina", SetRoleRequest(role="student"), TokenPayload(username="boss", role="admin"),
    ))
    assert _roles()["tina"] == "student"
