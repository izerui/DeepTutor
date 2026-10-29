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
}


def normalize_student_policy(value: Any) -> dict[str, bool] | None:
    """The items set for this student; missing items inherit (spec 2026-10-29 §6)."""
    if not isinstance(value, dict):
        return None
    return {
        key: value[key] for key in STUDENT_POLICY_DEFAULTS if isinstance(value.get(key), bool)
    }


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
