"""Which stored ``created_by`` values belong to the current account.

Spec 2026-10-29 D2: an administrator is an ordinary owner of personal data and
does not bypass other accounts' private items. Until per-administrator storage
The primary administrator writes under ``LOCAL_ADMIN_ID`` as well as their own id
(P1b); administrator-managed partners keep storing on their behalf.

The system itself -- code running with no request user, such as startup
backfills, migrations and background maintenance -- still acts as the local
administrator over every item, exactly as before.
"""

from __future__ import annotations

from .models import LOCAL_ADMIN_ID, CurrentUser


def owner_ids(user: CurrentUser) -> frozenset[str]:
    # The primary administrator also owns what the legacy root recorded under
    # LOCAL_ADMIN_ID; everyone else, later administrators included, owns only
    # their own account (spec 2026-10-29 P1b).
    from .admin_accounts import storage_owner_id

    return frozenset({user.id, storage_owner_id(user)})


def is_system_actor(user: CurrentUser) -> bool:
    """The implicit local administrator of code that has no request user."""
    from .context import get_current_user_or_none

    return user.id == LOCAL_ADMIN_ID and user.is_admin and get_current_user_or_none() is None


def owns(created_by: str, user: CurrentUser) -> bool:
    if is_system_actor(user):
        return True
    mine = owner_ids(user)
    if created_by in mine:
        return True
    if not user.is_admin:
        return False
    from deeptutor.services.partners.scope import PARTNER_USER_PREFIX, is_partner_user_id

    if not is_partner_user_id(created_by):
        return False
    from .partner_access import partner_owner_id

    return (partner_owner_id(created_by[len(PARTNER_USER_PREFIX):]) or LOCAL_ADMIN_ID) in mine
