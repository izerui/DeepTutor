"""Which administrator owns the legacy administrator root (spec 2026-10-29 P1b).

Nothing is moved. The administrator who already used the deployment keeps the
historical root (``data/``) — their conversations, books and memory stay where
they are, and that root's settings remain the system configuration every
administrator shares. Any administrator added later gets a personal root at
``data/users/<id>/`` like every other account, while their settings directory
still points at the shared system configuration.

The primary administrator is recorded once, in ``data/system/auth/
primary_admin.json``, while exactly one administrator account exists. With no
accounts (single-machine use) everyone acting as an administrator is the
primary one. With several administrators and no record nobody is guessed:
they all keep the legacy shared root and a warning is logged.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from deeptutor.services.path_service import PathService

from .models import LOCAL_ADMIN_ID, UserScope

if TYPE_CHECKING:
    from .models import CurrentUser

logger = logging.getLogger(__name__)


def _record_file() -> Path:
    from . import paths

    return paths.SYSTEM_ROOT / "auth" / "primary_admin.json"


def primary_admin_id() -> str | None:
    """The recorded primary administrator, recording the only one when unset."""
    path = _record_file()
    try:
        recorded = json.loads(path.read_text(encoding="utf-8")).get("user_id")
    except FileNotFoundError:
        recorded = None
    except (OSError, ValueError, AttributeError):
        logger.warning("Primary administrator record %s is unreadable", path)
        return None
    if recorded:
        return str(recorded)

    from deeptutor.services.auth import list_users

    admins = [
        str(user.get("id") or "")
        for user in list_users()
        if str(user.get("role") or "") == "admin" and user.get("id")
    ]
    if not admins:
        return None
    if len(admins) > 1:
        logger.warning(
            "Several administrators exist without a primary-administrator record; "
            "they keep sharing the legacy administrator root until one is recorded."
        )
        return None
    from deeptutor.utils.secret_files import ensure_private_directory

    ensure_private_directory(path.parent)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"user_id": admins[0]}), encoding="utf-8")
    temporary.replace(path)
    return admins[0]


def is_primary_admin_id(user_id: str) -> bool:
    if not user_id or user_id == LOCAL_ADMIN_ID:
        return True
    primary = primary_admin_id()
    return primary is None or primary == user_id


def is_primary_admin(user: CurrentUser) -> bool:
    """An administrator whose storage is the legacy administrator root."""
    from . import paths

    return user.is_admin and user.scope.root.resolve() == paths.ADMIN_WORKSPACE_ROOT.resolve()


def storage_owner_id(user: CurrentUser) -> str:
    """The ``created_by`` written for this account's own items.

    ``LOCAL_ADMIN_ID`` for the primary administrator (the legacy root's owner),
    otherwise the account named by its storage scope.
    """
    return LOCAL_ADMIN_ID if is_primary_admin(user) else user.scope.user_id


def secondary_admin_scope(user_id: str) -> UserScope:
    from . import paths

    return UserScope(kind="admin", user_id=user_id, root=(paths.USERS_ROOT / user_id).resolve())


def is_secondary_admin_scope(scope: UserScope) -> bool:
    from . import paths

    return scope.kind == "admin" and scope.root.resolve() != paths.ADMIN_WORKSPACE_ROOT.resolve()


class AdminAccountPathService(PathService):
    """A later administrator: personal data at their own root, shared system settings."""

    def get_settings_dir(self) -> Path:
        from .paths import get_admin_path_service

        return get_admin_path_service().get_settings_dir()


__all__ = [
    "AdminAccountPathService",
    "is_primary_admin",
    "is_primary_admin_id",
    "is_secondary_admin_scope",
    "primary_admin_id",
    "secondary_admin_scope",
    "storage_owner_id",
]
