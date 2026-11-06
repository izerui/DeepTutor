"""P2 / spec 2026-11-05: two roles; stored roles are never rewritten."""

from __future__ import annotations

from deeptutor.multi_user import identity
from deeptutor.multi_user.models import ASSIGNABLE_ROLES, normalize_role, role_label


def _record(username):
    return identity.load_users()[username]


def test_only_admin_and_learner_are_assignable():
    assert ASSIGNABLE_ROLES == frozenset({"admin", "student"})
    assert normalize_role("bogus") == "student"
    assert [role_label(r) for r in ("admin", "teacher", "student", "user")] == [
        "admin", "learner", "learner", "learner",
    ]


def test_new_accounts_default_to_learner(seed_user):
    seed_user("boss", role="admin")
    from deeptutor.services.auth import add_user

    add_user("sam", "password1234")
    assert _record("sam")["role"] == "student"


def test_stored_roles_are_not_rewritten(seed_user):
    seed_user("boss", role="admin")
    seed_user("una", role="user")
    seed_user("tina", role="teacher")
    assert _record("una")["role"] == "user"
    assert _record("tina")["role"] == "teacher"
    assert role_label(_record("una")["role"]) == "learner"
    assert role_label(_record("tina")["role"]) == "learner"
