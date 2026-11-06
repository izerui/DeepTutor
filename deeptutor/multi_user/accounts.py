"""Accounts as the rest of the code reads them: who exists, their live role.

Read from the live user store, so a role change or a disabled account takes
effect before a session token expires.
"""

from __future__ import annotations

import logging
from typing import Any

from deeptutor.multi_user.models import CurrentUser

logger = logging.getLogger(__name__)


def accounts() -> dict[str, dict[str, Any]]:
    """Account id -> {username, role, disabled}."""
    from deeptutor.multi_user.identity import load_users
    from deeptutor.multi_user.models import normalize_role

    out: dict[str, dict[str, Any]] = {}
    for username, record in load_users().items():
        user_id = str(record.get("id") or "")
        if user_id:
            out[user_id] = {
                "username": username,
                "role": normalize_role(str(record.get("role") or "")),
                "disabled": bool(record.get("disabled", False)),
            }
    return out


def live_role(user: CurrentUser) -> str:
    """The actor's role as stored now (a role change counts before the token expires)."""
    if user.is_admin:
        return "admin"
    account = accounts().get(user.id)
    return account["role"] if account else user.role


__all__ = ["accounts", "live_role"]
