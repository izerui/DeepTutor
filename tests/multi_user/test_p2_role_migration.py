"""P2: three roles; ``user`` accounts become students without losing abilities."""

from __future__ import annotations

import json

from deeptutor.multi_user import identity, role_migration
from deeptutor.multi_user.grants import load_grant, save_grant
from deeptutor.multi_user.models import ASSIGNABLE_ROLES, normalize_role


def _record(username):
    return identity.load_users()[username]


def test_only_three_roles_are_assignable():
    assert ASSIGNABLE_ROLES == frozenset({"admin", "teacher", "student"})
    assert normalize_role("bogus") == "student"


def test_new_accounts_default_to_student(seed_user):
    seed_user("boss", role="admin")
    from deeptutor.services.auth import add_user

    add_user("sam", "password1234")
    assert _record("sam")["role"] == "student"


def test_user_accounts_become_students_and_keep_their_abilities(seed_user):
    seed_user("boss", role="admin")
    seed_user("una", role="user")
    report = role_migration.run_role_migration()
    record = _record("una")
    assert record["role"] == "student"
    assert report["students"] == ["una"]
    policy = load_grant(record["id"])["student_policy"]
    assert policy == {"allow_personal_mastery": True, "allow_private_kb": True}


def test_learner_preset_restrictions_are_written_into_the_grant(seed_user):
    seed_user("boss", role="admin")
    seed_user("lia", role="user")
    users = identity.load_users()
    users["lia"]["preset"] = "learner"
    identity._write_users(users)
    lia_id = _record("lia")["id"]
    assert load_grant(lia_id).get("learning_policy") is None
    role_migration.run_role_migration()
    policy = load_grant(lia_id)["learning_policy"]
    assert policy["allowed_surfaces"] == ["chat", "reading"]
    assert policy["locked_persona"] == "teacher"


def test_a_stored_learning_policy_and_student_policy_are_kept(seed_user):
    seed_user("boss", role="admin")
    seed_user("kim", role="user")
    kim_id = _record("kim")["id"]
    grant = load_grant(kim_id)
    grant["student_policy"] = {"allow_personal_mastery": False, "allow_private_kb": False}
    save_grant(kim_id, grant)
    role_migration.run_role_migration()
    assert load_grant(kim_id)["student_policy"] == {
        "allow_personal_mastery": False, "allow_private_kb": False,
    }


def test_migration_is_idempotent_and_backs_up_once(seed_user, mu_isolated_root):
    from deeptutor.multi_user import paths

    seed_user("boss", role="admin")
    seed_user("una", role="user")
    first = role_migration.run_role_migration()
    second = role_migration.run_role_migration()
    assert first["students"] == ["una"] and second["students"] == []
    backups = list((paths.SYSTEM_ROOT / "auth" / "backups").glob("users-*.json"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text())["una"]["role"] == "user"
