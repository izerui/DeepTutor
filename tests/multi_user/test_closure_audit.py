"""Closing the loops found by the 2026-10-30 audit of roles, classes, scopes and practice sets.

Spec 2026-10-29: every account event (role change, deletion, password reset),
class event (archive, teacher change) and publication keeps one consistent
answer to "who may see and do what".
"""

from __future__ import annotations

import asyncio
import time

import pytest

from deeptutor.classes.service import ClassConflict, ClassForbidden, ClassService
from deeptutor.multi_user.context import get_current_user
from deeptutor.practice_sets.service import PracticeSetService
from deeptutor.publishing.scope import BOOK, KNOWLEDGE_BASE, PRACTICE_SET, get_scope
from deeptutor.publishing.service import PublishService
from deeptutor.publishing.scope import ScopeForbidden


def _class_id(as_account) -> str:
    with as_account("tina"):
        [row] = ClassService().list_classes()
    return row["id"]


def _set_role(teaching_users, name: str, role: str) -> None:
    from deeptutor.classes.lifecycle import after_role_change
    from deeptutor.multi_user.identity import set_role

    old = teaching_users[name]["role"]
    set_role(name, role)
    after_role_change(teaching_users[name]["id"], old, role)


# ── sessions follow the account store ──────────────────────


def test_tokens_follow_deletion_role_and_password_reset(teaching_users, monkeypatch):
    from deeptutor.multi_user.identity import delete_user, set_password, set_role
    from deeptutor.services import auth

    monkeypatch.setattr(auth, "AUTH_SECRET", "s" * 48)
    tom = auth.create_token("tom", role="teacher")
    assert auth.decode_token(tom).role == "teacher"

    set_role("tom", "student")
    # The live role, not the one minted into the token.
    assert auth.decode_token(tom).role == "student"

    time.sleep(1.1)  # tokens carry whole seconds
    set_password("tom", auth.hash_password("another-password"))
    assert auth.decode_token(tom) is None
    assert auth.decode_token(auth.create_token("tom", role="student")) is not None

    sam = auth.create_token("sam", role="student")
    delete_user("sam")
    assert auth.decode_token(sam) is None


# ── role changes ───────────────────────────────────────────


def test_a_demoted_teacher_manages_no_class_and_resets_no_password(teaching_users, as_account):
    class_id = _class_id(as_account)
    _set_role(teaching_users, "tina", "student")
    with as_account("tina"):
        with pytest.raises(ClassForbidden):
            ClassService().reset_student_password(teaching_users["sara"]["id"], "new-password-1")
        with pytest.raises(Exception):
            ClassService().update(class_id, name="Taken over")
    with as_account("tom"):
        detail = ClassService().get(class_id)
    assert [t["username"] for t in detail["teachers"]] == ["tom"]


def test_a_promoted_student_leaves_classes_and_assignments(
    teacher_draft, publish_template, teaching_users, as_account
):
    from deeptutor.teaching.store import TeachingStore

    teacher_draft()
    template_id = publish_template(students=("sara", "sam"))["template"]["id"]
    _set_role(teaching_users, "sam", "teacher")
    class_id = _class_id(as_account)
    with as_account("tina"):
        students = [s["username"] for s in ClassService().get(class_id)["students"]]
    assert students == ["sara"]
    active = {a.student_user_id for a in TeachingStore().list_assignments(template_id) if a.status == "active"}
    assert active == {teaching_users["sara"]["id"]}


def test_the_first_administrators_role_is_fixed(teaching_users, as_account, monkeypatch):
    from fastapi import HTTPException

    from deeptutor.api.routers import auth as auth_router
    from deeptutor.multi_user.admin_accounts import primary_admin_id

    assert primary_admin_id() == teaching_users["adam"]["id"]
    with pytest.raises(HTTPException) as refused:
        asyncio.run(auth_router.update_user_role(
            "adam", auth_router.SetRoleRequest(role="teacher"),
            current=auth_router.TokenPayload(username="other", role="admin", user_id="x"),
        ))
    assert refused.value.status_code == 400


# ── account deletion ───────────────────────────────────────


def test_deleting_accounts_clears_class_rows_and_hands_over_practice_sets(teaching_users, as_account):
    from deeptutor.multi_user.account_handoff import run_stages

    class_id = _class_id(as_account)
    with as_account("tom"):
        created = PracticeSetService().create("Tom's set")
    adam = teaching_users["adam"]["id"]
    for name in ("tom", "sam"):
        failed, _ = run_stages(teaching_users[name]["id"], adam, ("classes", "publish_targets", "practice_sets"))
        assert failed == []
    with as_account("tina"):
        detail = ClassService().get(class_id)
    assert [t["username"] for t in detail["teachers"]] == ["tina"]
    assert [s["username"] for s in detail["students"]] == ["sara"]
    with as_account("adam"):
        assert PracticeSetService().get(created["id"])["title"] == "Tom's set"


def test_an_admin_resets_any_password_and_old_sessions_end(teaching_users, monkeypatch):
    from deeptutor.api.routers import auth as auth_router
    from deeptutor.services import auth

    monkeypatch.setattr(auth, "AUTH_SECRET", "s" * 48)
    old = auth.create_token("tina", role="teacher")
    time.sleep(1.1)
    asyncio.run(auth_router.admin_reset_password(
        "tina", auth_router.AdminPasswordReset(new_password="fresh-password"),
        current=auth_router.TokenPayload(username="adam", role="admin", user_id=teaching_users["adam"]["id"]),
    ))
    assert auth.decode_token(old) is None
    assert auth.authenticate("tina", "fresh-password") is not None


# ── classes ────────────────────────────────────────────────


def test_teachers_only_step_down_and_never_leave_a_class_empty(teaching_users, as_account):
    class_id = _class_id(as_account)
    with as_account("tom"):
        with pytest.raises(ClassForbidden):
            ClassService().remove_teacher(class_id, teaching_users["tina"]["id"])
        ClassService().remove_teacher(class_id, teaching_users["tom"]["id"])
    with as_account("tina"):
        with pytest.raises(ClassConflict):
            ClassService().remove_teacher(class_id, teaching_users["tina"]["id"])


def test_archiving_a_class_ends_its_link_and_what_reached_students_through_it(
    teacher_draft, publish_template, teaching_users, as_account
):
    from deeptutor.publishing.scope import MASTERY_PATH
    from deeptutor.teaching.store import TeachingStore

    teacher_draft()
    template_id = publish_template()["template"]["id"]
    class_id = _class_id(as_account)
    with as_account("tina"):
        PublishService().set_scope(MASTERY_PATH, template_id, everyone=False, users=[], classes=[class_id])
        ClassService().regenerate_link(class_id)
        ClassService().update(class_id, archived=True)
        assert ClassService().invite_link(class_id) is None

    def active():
        return {a.student_user_id for a in TeachingStore().list_assignments(template_id) if a.status == "active"}

    assert active() == set()
    with as_account("tina"):
        ClassService().update(class_id, archived=False)
    assert active() == {teaching_users["sara"]["id"], teaching_users["sam"]["id"]}


# ── publishing ─────────────────────────────────────────────


def test_a_first_publication_reaches_the_teachers_classes_and_admins_still_see_it(
    teaching_users, as_account, tmp_path
):
    from deeptutor.knowledge import registry

    kb = registry.register_kb(
        base_dir=tmp_path / "kbs", name="notes", kind="uploaded", created_by=teaching_users["tina"]["id"],
    )
    class_id = _class_id(as_account)
    with as_account("tina"):
        registry.set_visibility(kb.kb_id, "public")
    scope = get_scope(KNOWLEDGE_BASE, kb.kb_id)
    assert scope is not None and scope.classes == [class_id] and not scope.everyone
    current = registry.get_kb(kb.kb_id)
    for name, expected in (("sara", True), ("uma", False), ("adam", True)):
        with as_account(name):
            assert registry.can_read(current, get_current_user()) is expected, name


def test_teachers_pick_only_their_students_but_keep_existing_targets(
    teaching_books, teaching_users, as_account, seed_user
):
    stan = seed_user("stan", role="student")["id"]  # a student in no class of Tom's
    sara = teaching_users["sara"]["id"]
    with as_account("adam"):
        other = ClassService().create("Other class")
    with as_account("tom"):
        assert [p["username"] for p in PublishService().search_people("stan")] == []
        with pytest.raises(ScopeForbidden):
            PublishService().set_scope(BOOK, teaching_books.public.book_id, everyone=False, users=[stan],
                                       classes=[], confirm=True)
        PublishService().set_scope(BOOK, teaching_books.public.book_id, everyone=False, users=[sara],
                                   classes=[], confirm=True)
    # Sara leaves every class; the stale target must not block another change.
    with as_account("sara"):
        ClassService().leave(_class_id(as_account))
    with as_account("tom"):
        PublishService().set_scope(BOOK, teaching_books.public.book_id, everyone=True, users=[sara],
                                   classes=[], confirm=True)
    assert other["id"]


# ── practice sets ──────────────────────────────────────────

QUESTIONS = [
    {"question": "2 + 2 = ?", "question_type": "choice", "options": {"A": "3", "B": "4"},
     "correct_answer": "B", "explanation": "Add."},
]


@pytest.fixture
def practice_set(as_account):
    class_id = _class_id(as_account)
    with as_account("tina"):
        service = PracticeSetService()
        created = service.create("Week 1")
        service.update(created["id"], questions=QUESTIONS, reveal="after_due", due_at=time.time() + 3600)
        service.publish(created["id"])
    return created["id"], class_id


def _bank_entry(as_account, name, set_id):
    from deeptutor.services.session.sqlite_store import get_sqlite_session_store

    with as_account(name):
        listed = asyncio.run(get_sqlite_session_store().list_notebook_entries(limit=50))
    rows = listed["items"] if isinstance(listed, dict) else listed
    [row] = [r for r in rows if r.get("material_id") == set_id]
    return row


def test_a_first_published_set_reaches_the_teachers_classes(practice_set):
    set_id, class_id = practice_set
    scope = get_scope(PRACTICE_SET, set_id)
    assert scope is not None and scope.classes == [class_id]


def test_answers_stay_out_of_the_bank_until_the_deadline(practice_set, as_account):
    set_id, _ = practice_set
    with as_account("sara"):
        qid = PracticeSetService().take(set_id)["questions"][0]["question_id"]
        result = asyncio.run(PracticeSetService().submit(set_id, {qid: "A"}))
        assert not result["answers_revealed"] and result["results"][0]["feedback"] == ""
    assert _bank_entry(as_account, "sara", set_id)["correct_answer"] == ""

    with as_account("tina"):
        PracticeSetService().update(set_id, due_at=time.time() - 1)
    with as_account("sara"):
        assert PracticeSetService().take(set_id)["answers_revealed"]
        asyncio.run(PracticeSetService().reveal_in_bank(set_id))
    entry = _bank_entry(as_account, "sara", set_id)
    assert entry["correct_answer"] == "B" and entry["user_answer"] == "A"


def test_no_answers_before_submitting_even_after_the_deadline(practice_set, as_account):
    set_id, _ = practice_set
    with as_account("tina"):
        PracticeSetService().update(set_id, due_at=time.time() - 1)
    with as_account("sam"):
        taken = PracticeSetService().take(set_id)
    assert not taken["answers_revealed"] and "correct_answer" not in taken["questions"][0]


def test_reports_keep_students_who_left_and_show_practice_in_class_reports(practice_set, as_account):
    from deeptutor.classes.report import ReportService

    set_id, class_id = practice_set
    with as_account("sara"):
        qid = PracticeSetService().take(set_id)["questions"][0]["question_id"]
        asyncio.run(PracticeSetService().submit(set_id, {qid: "B"}))
    with as_account("tina"):
        report = ReportService().class_report(class_id)
    sara = next(s for s in report["students"] if s["username"] == "sara")
    assert sara["practice_sets"][0]["first_score"] == 1
    with as_account("sara"):
        ClassService().leave(class_id)
    with as_account("tina"):
        practice = PracticeSetService().report(set_id)
    left = next(s for s in practice["students"] if s["username"] == "sara")
    assert left["completed"] and left["in_scope"] is False


def test_after_due_needs_a_deadline(as_account):
    from deeptutor.practice_sets.service import PracticeError

    with as_account("tina"):
        service = PracticeSetService()
        created = service.create("No deadline")
        service.update(created["id"], questions=QUESTIONS, reveal="after_due")
        with pytest.raises(PracticeError):
            service.publish(created["id"])


# ── student permissions ────────────────────────────────────


def test_personal_settings_override_classes_item_by_item(teaching_users, as_account):
    from deeptutor.multi_user.grants import load_grant, save_grant
    from deeptutor.multi_user.roles import effective_student_policy

    class_id = _class_id(as_account)
    sara = teaching_users["sara"]["id"]
    with as_account("adam"):
        ClassService().set_student_policy(class_id, {"allow_private_kb": True, "allow_personal_book": False})
    save_grant(sara, {**load_grant(sara), "student_policy": {"allow_personal_mastery": False}})
    policy = effective_student_policy(sara)
    assert policy == {"allow_personal_mastery": False, "allow_personal_book": False, "allow_private_kb": True}


# ── registration ───────────────────────────────────────────


def test_open_registration_does_not_depend_on_a_dead_invite(teaching_users, monkeypatch):
    from fastapi import Response

    from deeptutor.api.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "_require_private_frontend", lambda request: None)
    result = asyncio.run(auth_router.register(
        auth_router.PublicRegisterRequest(username="newbie", password="password1234", invite="no-such-link"),
        request=None, response=Response(),
    ))
    assert result["ok"] and result["joined_class"] is None and result["invite_error"]


def test_people_change_their_own_password_and_keep_this_session(teaching_users, monkeypatch):
    from fastapi import HTTPException, Response

    from deeptutor.api.routers import auth as auth_router
    from deeptutor.services import auth

    monkeypatch.setattr(auth, "AUTH_SECRET", "s" * 48)
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    sara = auth_router.TokenPayload(username="sara", role="student", user_id=teaching_users["sara"]["id"])
    old = auth.create_token("sara", role="student")
    with pytest.raises(HTTPException):
        asyncio.run(auth_router.change_own_password(
            auth_router.ChangeOwnPassword(current_password="wrong", new_password="brand-new-pass"),
            Response(), payload=sara,
        ))
    time.sleep(1.1)
    response = Response()
    asyncio.run(auth_router.change_own_password(
        auth_router.ChangeOwnPassword(current_password="password1234", new_password="brand-new-pass"),
        response, payload=sara,
    ))
    assert auth.decode_token(old) is None
    assert "dt_token" in response.headers.get("set-cookie", "") or response.headers.get("set-cookie")
    assert auth.authenticate("sara", "brand-new-pass") is not None
