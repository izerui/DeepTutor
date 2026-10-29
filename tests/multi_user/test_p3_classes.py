"""P3a: classes — teachers, invitations, invite links, leaving (spec 2026-10-29 §5)."""

from __future__ import annotations

import pytest

from deeptutor.classes.service import (
    ClassConflict,
    ClassForbidden,
    ClassNotFound,
    ClassService,
    link_info,
    teacher_student_ids,
)


@pytest.fixture
def people(seed_user):
    boss = seed_user("boss", role="admin")
    tina = seed_user("tina", role="teacher")
    tom = seed_user("tom", role="teacher")
    sam = seed_user("sam", role="student")
    sue = seed_user("sue", role="student")
    return {"boss": boss, "tina": tina, "tom": tom, "sam": sam, "sue": sue}


def _as(as_user, people, name, role):
    return as_user(people[name]["id"], role=role, username=name)


def test_a_teacher_creates_a_class_and_teaches_it(as_user, people):
    with _as(as_user, people, "tina", "teacher"):
        created = ClassService().create("Physics 1")
        assert created["can_manage"] is True
        assert [t["username"] for t in created["teachers"]] == ["tina"]
        assert [c["name"] for c in ClassService().list_classes()] == ["Physics 1"]
    with _as(as_user, people, "tom", "teacher"):
        assert ClassService().list_classes() == []
        with pytest.raises(ClassNotFound):
            ClassService().get(created["id"])


def test_students_cannot_create_classes(as_user, people):
    with _as(as_user, people, "sam", "student"):
        with pytest.raises(ClassForbidden):
            ClassService().create("Mine")


def test_invitation_must_be_accepted_by_the_student(as_user, people):
    with _as(as_user, people, "tina", "teacher"):
        cls = ClassService().create("Physics 1")
        assert [s["username"] for s in ClassService().search_students("s")] == ["sam", "sue"]
        ClassService().invite(cls["id"], people["sam"]["id"])
        assert ClassService().get(cls["id"])["students"] == []
    with _as(as_user, people, "sam", "student"):
        [invitation] = ClassService().my_invitations()
        assert invitation["class_name"] == "Physics 1"
        assert invitation["invited_by"]["username"] == "tina"
        ClassService().respond(invitation["id"], accept=True)
        assert [c["name"] for c in ClassService().list_classes()] == ["Physics 1"]
        assert ClassService().my_invitations() == []
    with _as(as_user, people, "tina", "teacher"):
        assert [s["username"] for s in ClassService().get(cls["id"])["students"]] == ["sam"]
        with pytest.raises(ClassConflict):
            ClassService().invite(cls["id"], people["sam"]["id"])
    assert teacher_student_ids(people["tina"]["id"]) == {people["sam"]["id"]}


def test_a_declined_invitation_leaves_the_student_out(as_user, people):
    with _as(as_user, people, "tina", "teacher"):
        cls = ClassService().create("Physics 1")
        ClassService().invite(cls["id"], people["sue"]["id"])
    with _as(as_user, people, "sue", "student"):
        [invitation] = ClassService().my_invitations()
        ClassService().respond(invitation["id"], accept=False)
        assert ClassService().list_classes() == []


def test_invite_link_joins_and_regenerating_revokes_the_old_one(as_user, people):
    with _as(as_user, people, "tina", "teacher"):
        cls = ClassService().create("Physics 1")
        first = ClassService().regenerate_link(cls["id"])
        assert link_info(first["token"])["class_name"] == "Physics 1"
        second = ClassService().regenerate_link(cls["id"], expires_in_days=7)
    with pytest.raises(ClassNotFound):
        link_info(first["token"])
    with _as(as_user, people, "sam", "student"):
        joined = ClassService().join_by_link(second["token"])
        assert joined["class_name"] == "Physics 1"
        ClassService().join_by_link(second["token"])  # idempotent
    with _as(as_user, people, "tina", "teacher"):
        assert [s["username"] for s in ClassService().get(cls["id"])["students"]] == ["sam"]
        ClassService().revoke_link(cls["id"])
    with pytest.raises(ClassNotFound):
        link_info(second["token"])


def test_an_expired_link_no_longer_works(as_user, people, monkeypatch):
    from deeptutor.classes import service

    with _as(as_user, people, "tina", "teacher"):
        cls = ClassService().create("Physics 1")
        link = ClassService().regenerate_link(cls["id"], expires_in_days=1)
    real_now = service._now
    monkeypatch.setattr(service, "_now", lambda: real_now() + 2 * 86400)
    with pytest.raises(ClassNotFound, match="expired"):
        link_info(link["token"])


def test_teachers_cannot_join_by_link_and_students_can_leave(as_user, people):
    with _as(as_user, people, "tina", "teacher"):
        cls = ClassService().create("Physics 1")
        token = ClassService().regenerate_link(cls["id"])["token"]
    with _as(as_user, people, "tom", "teacher"):
        with pytest.raises(ClassForbidden):
            ClassService().join_by_link(token)
    with _as(as_user, people, "sam", "student"):
        ClassService().join_by_link(token)
        ClassService().leave(cls["id"])
        assert ClassService().list_classes() == []


def test_only_admins_place_students_directly(as_user, people):
    with _as(as_user, people, "tina", "teacher"):
        cls = ClassService().create("Physics 1")
        with pytest.raises(ClassForbidden):
            ClassService().add_students(cls["id"], [people["sam"]["id"]])
    with as_user(people["boss"]["id"], role="admin", username="boss"):
        result = ClassService().add_students(cls["id"], [people["sam"]["id"], people["sue"]["id"]])
        assert {s["username"] for s in result["students"]} == {"sam", "sue"}
        assert [c["name"] for c in ClassService().list_classes()] == ["Physics 1"]


def test_a_teacher_adds_a_co_teacher_and_removes_a_student(as_user, people):
    with _as(as_user, people, "tina", "teacher"):
        cls = ClassService().create("Physics 1")
        ClassService().add_teacher(cls["id"], people["tom"]["id"])
        with pytest.raises(Exception):
            ClassService().add_teacher(cls["id"], people["sam"]["id"])  # not a teacher
    with as_user(people["boss"]["id"], role="admin", username="boss"):
        ClassService().add_students(cls["id"], [people["sam"]["id"]])
    with _as(as_user, people, "tom", "teacher"):
        assert ClassService().get(cls["id"])["can_manage"]
        ClassService().remove_student(cls["id"], people["sam"]["id"])
        assert ClassService().get(cls["id"])["students"] == []


def test_teachers_reset_passwords_only_for_their_students(as_user, people):
    from deeptutor.services.auth import authenticate

    with as_user(people["boss"]["id"], role="admin", username="boss"):
        cls = ClassService().create("Physics 1")
        ClassService().add_teacher(cls["id"], people["tina"]["id"])
        ClassService().add_students(cls["id"], [people["sam"]["id"]])
    with _as(as_user, people, "tina", "teacher"):
        ClassService().reset_student_password(people["sam"]["id"], "new-password-1")
        with pytest.raises(ClassForbidden):
            ClassService().reset_student_password(people["sue"]["id"], "new-password-1")
    assert authenticate("sam", "new-password-1") is not None


def test_archived_classes_drop_out_of_teacher_reach(as_user, people):
    with as_user(people["boss"]["id"], role="admin", username="boss"):
        cls = ClassService().create("Old")
        ClassService().add_teacher(cls["id"], people["tina"]["id"])
        ClassService().add_students(cls["id"], [people["sam"]["id"]])
        ClassService().update(cls["id"], archived=True)
    assert teacher_student_ids(people["tina"]["id"]) == set()


# ── P3c: class-wide student permissions (spec §6) ─────────────────────


def _class_with(as_user, people, *students, policy):
    with as_user(people["boss"]["id"], role="admin", username="boss"):
        cls = ClassService().create("Policy class")
        ClassService().add_students(cls["id"], [people[s]["id"] for s in students])
        ClassService().set_student_policy(cls["id"], policy)
    return cls


def test_a_class_setting_applies_to_its_students(as_user, people):
    from deeptutor.multi_user.roles import effective_student_policy

    sam = people["sam"]["id"]
    assert effective_student_policy(sam)["allow_private_kb"] is False  # default
    _class_with(as_user, people, "sam", policy={"allow_private_kb": True})
    assert effective_student_policy(sam)["allow_private_kb"] is True
    assert effective_student_policy(sam)["allow_personal_mastery"] is True  # unset → default


def test_across_classes_any_allow_wins(as_user, people):
    from deeptutor.multi_user.roles import effective_student_policy

    _class_with(as_user, people, "sam", policy={"allow_private_kb": False, "allow_personal_mastery": False})
    _class_with(as_user, people, "sam", policy={"allow_private_kb": True})
    policy = effective_student_policy(people["sam"]["id"])
    assert policy == {"allow_private_kb": True, "allow_personal_mastery": False, "allow_personal_book": True}


def test_a_personal_setting_wins_over_classes(as_user, people):
    from deeptutor.multi_user.grants import load_grant, save_grant
    from deeptutor.multi_user.roles import effective_student_policy

    sam = people["sam"]["id"]
    _class_with(as_user, people, "sam", policy={"allow_private_kb": True})
    grant = load_grant(sam)
    grant["student_policy"] = {"allow_personal_mastery": True, "allow_private_kb": False}
    save_grant(sam, grant)
    assert effective_student_policy(sam)["allow_private_kb"] is False


def test_only_admins_set_class_permissions(as_user, people):
    with _as(as_user, people, "tina", "teacher"):
        cls = ClassService().create("Physics 1")
        with pytest.raises(ClassForbidden):
            ClassService().set_student_policy(cls["id"], {"allow_private_kb": True})
