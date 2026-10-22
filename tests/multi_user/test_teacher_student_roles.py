"""Teacher/student role helpers and the student policy grant field."""

from __future__ import annotations

import pytest

from deeptutor.multi_user import roles


@pytest.fixture(autouse=True)
def _ensure_admin_exists(seed_user):
    """Seed a bootstrap admin so subsequent seed_user calls honor the requested role."""
    seed_user("_bootstrap_admin", role="admin")


def test_teaching_roles_are_admin_and_teacher_only():
    assert roles.TEACHING_ROLES == frozenset({"admin", "teacher"})


def test_student_policy_defaults():
    assert roles.STUDENT_POLICY_DEFAULTS == {
        "allow_personal_mastery": True,
        "allow_private_kb": False,
    }


def test_normalize_student_policy_fills_defaults_and_drops_unknown_keys():
    assert roles.normalize_student_policy(None) is None
    assert roles.normalize_student_policy("bogus") is None
    assert roles.normalize_student_policy({"allow_private_kb": True, "x": 1}) == {
        "allow_personal_mastery": True,
        "allow_private_kb": True,
    }
    # Non-boolean values fall back to the default instead of being coerced.
    assert roles.normalize_student_policy({"allow_personal_mastery": "no"}) == {
        "allow_personal_mastery": True,
        "allow_private_kb": False,
    }


def test_current_role_reads_live_role_not_token_role(seed_user, as_user):
    from deeptutor.services.auth import set_role

    record = seed_user("tina", role="teacher")
    with as_user(record["id"], role="teacher", username="tina"):
        assert roles.current_role() == "teacher"
        assert roles.is_teacher_or_admin() is True
        set_role("tina", "user")
        # The token still says "teacher"; the live record wins.
        assert roles.current_role() == "user"
        assert roles.is_teacher_or_admin() is False


def test_current_role_falls_back_to_context_role_without_local_record(as_user):
    with as_user("u_external", role="student"):
        assert roles.current_role() == "student"


def test_admin_context_is_always_admin(as_user):
    with as_user("u_admin", role="admin"):
        assert roles.current_role() == "admin"
        assert roles.is_teacher_or_admin() is True


@pytest.mark.parametrize("role", ["admin", "teacher", "user"])
def test_non_students_ignore_student_policy(seed_user, as_user, role):
    from deeptutor.multi_user.grants import save_grant

    record = seed_user(f"n_{role}", role=role)
    if role != "admin":
        save_grant(
            record["id"],
            {"student_policy": {"allow_personal_mastery": False, "allow_private_kb": False}},
        )
    with as_user(record["id"], role=role, username=f"n_{role}"):
        assert roles.can_create_personal_mastery() is True
        assert roles.can_write_private_kb() is True


def test_student_defaults(seed_user, as_user):
    record = seed_user("stu", role="student")
    with as_user(record["id"], role="student", username="stu"):
        assert roles.can_create_personal_mastery() is True
        assert roles.can_write_private_kb() is False


def test_student_policy_from_grant(seed_user, as_user):
    from deeptutor.multi_user.grants import load_grant, save_grant

    record = seed_user("stu2", role="student")
    save_grant(
        record["id"],
        {"student_policy": {"allow_personal_mastery": False, "allow_private_kb": True}},
    )
    assert load_grant(record["id"])["student_policy"] == {
        "allow_personal_mastery": False,
        "allow_private_kb": True,
    }
    with as_user(record["id"], role="student", username="stu2"):
        assert roles.can_create_personal_mastery() is False
        assert roles.can_write_private_kb() is True


def test_unreadable_grant_denies_student_capabilities(seed_user, as_user):
    from deeptutor.multi_user.grants import grant_path

    record = seed_user("stu3", role="student")
    path = grant_path(record["id"])
    path.write_text("{not json", encoding="utf-8")
    with as_user(record["id"], role="student", username="stu3"):
        assert roles.can_create_personal_mastery() is False
        assert roles.can_write_private_kb() is False


def test_empty_grant_has_null_student_policy():
    from deeptutor.multi_user.grants import empty_grant, normalize_grant

    assert empty_grant("u1")["student_policy"] is None
    assert normalize_grant("u1", {"student_policy": {"allow_private_kb": True}})[
        "student_policy"
    ] == {"allow_personal_mastery": True, "allow_private_kb": True}


def test_load_grant_rejects_non_object_student_policy(seed_user):
    import json

    from deeptutor.multi_user.grants import GrantStorageError, grant_path, load_grant

    record = seed_user("stu4", role="student")
    grant_path(record["id"]).write_text(
        json.dumps({"student_policy": ["nope"]}), encoding="utf-8"
    )
    with pytest.raises(GrantStorageError):
        load_grant(record["id"])
