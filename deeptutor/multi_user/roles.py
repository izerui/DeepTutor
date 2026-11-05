"""Roles and the daily AI limit (spec 2026-11-05).

There are two roles: ``admin`` and learner. Everyone who is not an
administrator is a learner and may use every feature, author content and
publish it; stored ``teacher``, ``student`` and ``user`` records all read as
learners. Roles are read from the live user record so a role change takes
effect without waiting for the session token to expire.
"""

from __future__ import annotations

from typing import Any

from .context import get_current_user
from .models import normalize_role

#: Batch 3 §8: a learner's daily AI tokens; ``0`` means no limit.
AI_DAILY_TOKENS = "ai_daily_tokens"
AI_DAILY_TOKENS_MAX = 100_000_000


def normalize_ai_daily_tokens(value: Any) -> int | None:
    """A personal daily AI limit, or ``None`` (use the school default)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= AI_DAILY_TOKENS_MAX else None


def current_role() -> str:
    """The caller's role as stored now, not as minted into their token."""
    user = get_current_user()
    if user.is_admin:
        return "admin"
    from .identity import get_user_by_id

    found = get_user_by_id(user.id)
    if found is None:
        return user.role
    return normalize_role(str(found[1].get("role") or "user"))


def effective_ai_daily_tokens(user_id: str) -> int:
    """A learner's daily AI tokens (batch 3 §8); ``0`` is no limit.

    Their own setting wins; otherwise the school default.
    """
    from .grants import GrantStorageError, load_grant

    try:
        own = normalize_ai_daily_tokens(load_grant(user_id).get(AI_DAILY_TOKENS))
    except GrantStorageError:
        own = None
    if own is not None:
        return own
    from deeptutor.services.llm.usage_limit import school_default_daily_tokens

    return school_default_daily_tokens()
