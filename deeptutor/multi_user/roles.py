"""Teaching roles and the student policy.

``admin`` stays the only elevated role. ``teacher`` unlocks the teaching
surfaces guarded by ``require_teacher_or_admin``; ``student`` is a teaching
identity whose account-level abilities are narrowed by ``student_policy`` in
the user's grant. Roles are read from the live user record so a role change
takes effect without waiting for the session token to expire.
"""

from __future__ import annotations

from typing import Any

from .context import get_current_user
from .models import normalize_role

TEACHING_ROLES: frozenset[str] = frozenset({"admin", "teacher"})

#: ``None`` in a grant means "use these". Spec 2026-10-29 §6: personal
#: Mastery Paths and books stay open to students by default; private
#: knowledge bases are closed by default.
STUDENT_POLICY_DEFAULTS: dict[str, bool] = {
    "allow_personal_mastery": True,
    "allow_personal_book": True,
    "allow_private_kb": False,
    # Spec 2026-11-03 batch 3 §7.1: own courses and task cards stay open;
    # Partners, agents and Co-Writer are off for students unless granted.
    "allow_personal_course": True,
    "allow_personal_task_card": True,
    "allow_partners": False,
    "allow_agents": False,
    "allow_co_writer": False,
}

#: Batch 3 §8: a student's daily AI tokens; ``0`` means no limit.
AI_DAILY_TOKENS = "ai_daily_tokens"
AI_DAILY_TOKENS_MAX = 100_000_000


def _ai_tokens(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= AI_DAILY_TOKENS_MAX else None


def normalize_student_policy(value: Any) -> dict[str, Any] | None:
    """The items set for this student; missing items inherit (spec 2026-10-29 §6)."""
    if not isinstance(value, dict):
        return None
    cleaned: dict[str, Any] = {
        key: value[key] for key in STUDENT_POLICY_DEFAULTS if isinstance(value.get(key), bool)
    }
    tokens = _ai_tokens(value.get(AI_DAILY_TOKENS))
    if tokens is not None:
        cleaned[AI_DAILY_TOKENS] = tokens
    return cleaned


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


def is_teacher_or_admin() -> bool:
    return current_role() in TEACHING_ROLES


def effective_student_policy(user_id: str) -> dict[str, bool]:
    """The student's policy; every ability is denied when the grant is unreadable.

    Spec 2026-10-29 §6: a setting made for this student wins; otherwise the
    classes they are in decide (any class that allows, allows); otherwise the
    defaults apply.
    """
    from .grants import GrantStorageError, load_grant

    try:
        stored = load_grant(user_id).get("student_policy")
    except GrantStorageError:
        return {key: False for key in STUDENT_POLICY_DEFAULTS}
    from deeptutor.classes.service import class_student_policy

    policy = dict(STUDENT_POLICY_DEFAULTS)
    policy.update(class_student_policy(user_id))
    # Per item: a setting made for this student wins over their classes.
    if isinstance(stored, dict):
        policy.update({k: v for k, v in stored.items() if k in policy and isinstance(v, bool)})
    return policy


def widest_ai_limit(limits: list[int]) -> int | None:
    """The most generous of several limits: no limit (``0``) beats any number."""
    if not limits:
        return None
    return 0 if 0 in limits else max(limits)


def effective_ai_daily_tokens(user_id: str) -> int:
    """A student's daily AI tokens (batch 3 §8); ``0`` is no limit.

    Their own setting wins; otherwise the most generous of their classes;
    otherwise the school default.
    """
    from .grants import GrantStorageError, load_grant

    try:
        stored = load_grant(user_id).get("student_policy")
    except GrantStorageError:
        stored = None
    own = _ai_tokens(stored.get(AI_DAILY_TOKENS)) if isinstance(stored, dict) else None
    if own is not None:
        return own
    from deeptutor.classes.service import class_ai_daily_tokens

    from_classes = class_ai_daily_tokens(user_id)
    if from_classes is not None:
        return from_classes
    from deeptutor.services.llm.usage_limit import school_default_daily_tokens

    return school_default_daily_tokens()


def _student_allows(key: str) -> bool:
    if current_role() != "student":
        return True
    return effective_student_policy(get_current_user().id)[key]


def can_create_personal_mastery() -> bool:
    return _student_allows("allow_personal_mastery")


def can_write_private_kb() -> bool:
    return _student_allows("allow_private_kb")


def can_create_personal_book() -> bool:
    return _student_allows("allow_personal_book")


def student_allows(key: str) -> bool:
    """Whether the caller may use ``key`` (always, unless they are a student it denies)."""
    return _student_allows(key)
