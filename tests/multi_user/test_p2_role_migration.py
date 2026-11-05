"""P2 / spec 2026-11-05: two roles; stored roles are never rewritten, learner presets move into grants."""

from __future__ import annotations

from deeptutor.multi_user import identity, role_migration
from deeptutor.multi_user.grants import load_grant
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
    report = role_migration.run_role_migration()
    assert _record("una")["role"] == "user"
    assert _record("tina")["role"] == "teacher"
    assert report == {"learner_policies": []}


def test_learner_preset_restrictions_are_written_into_the_grant(seed_user):
    seed_user("boss", role="admin")
    seed_user("lia", role="user")
    users = identity.load_users()
    users["lia"]["preset"] = "learner"
    identity._write_users(users)
    lia_id = _record("lia")["id"]
    assert load_grant(lia_id).get("learning_policy") is None
    report = role_migration.run_role_migration()
    assert report["learner_policies"] == ["lia"]
    policy = load_grant(lia_id)["learning_policy"]
    assert policy["allowed_surfaces"] == ["chat", "reading"]
    assert policy["locked_persona"] == "teacher"
    assert role_migration.run_role_migration() == {"learner_policies": []}
