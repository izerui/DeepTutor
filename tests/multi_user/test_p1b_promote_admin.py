"""P1b: promoting a second administrator is allowed and keeps both data sets apart."""

from __future__ import annotations

import asyncio

from deeptutor.api.routers.auth import SetRoleRequest, update_user_role
from deeptutor.multi_user import admin_accounts, paths
from deeptutor.services.auth import TokenPayload, list_users


def test_promoting_a_second_admin_records_the_first_as_primary(seed_user):
    boss = seed_user("boss", role="admin")
    tina = seed_user("tina", role="teacher")
    asyncio.run(update_user_role(
        "tina", SetRoleRequest(role="admin"), TokenPayload(username="boss", role="admin"),
    ))
    assert {u["username"]: u["role"] for u in list_users()}["tina"] == "admin"
    assert admin_accounts.primary_admin_id() == boss["id"]
    assert paths.scope_for_user(boss["id"], is_admin=True) == paths.admin_scope()
    assert paths.scope_for_user(tina["id"], is_admin=True).root == (
        paths.USERS_ROOT / tina["id"]
    ).resolve()
