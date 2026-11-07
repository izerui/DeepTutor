"""Closing the loops found by the 2026-10-30 audit of roles, scopes and practice sets.

Spec 2026-10-29: every account event (role change, deletion, password reset)
and publication keeps one consistent answer to "who may see and do what".
(Classes are gone: learners reach content by joining it.)
"""

from __future__ import annotations

import asyncio
import time

import pytest

from deeptutor.multi_user.context import get_current_user
from deeptutor.practice_sets.service import PracticeSetService
from deeptutor.publishing import scope as scopes
from deeptutor.publishing.scope import BOOK, KNOWLEDGE_BASE, PRACTICE_SET, get_scope


def _leave(teaching_users, name: str, kind: str, item_id: str) -> None:
    from deeptutor.catalog.enrollment import leave
    from deeptutor.multi_user.models import CurrentUser, UserScope

    user = teaching_users[name]
    leave(CurrentUser(user["id"], name, user["role"], UserScope(kind="user", user_id=user["id"], root=None)),
          kind, item_id)


def _set_role(teaching_users, name: str, role: str) -> None:
    from deeptutor.multi_user.content_lifecycle import after_role_change
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


def test_a_token_from_the_same_second_as_a_password_reset_also_ends(teaching_users, monkeypatch):
    from deeptutor.multi_user.identity import set_password
    from deeptutor.services import auth

    monkeypatch.setattr(auth, "AUTH_SECRET", "s" * 48)
    # Freeze the clock so issue and reset share one second, where ``iat`` cannot tell them apart.
    old = auth.create_token("tina", role="teacher")
    issued = auth.decode_token(old)
    assert issued is not None
    second = float(int(time.time()))
    monkeypatch.setattr("deeptutor.multi_user.identity.time.time", lambda: second)
    set_password("tina", auth.hash_password("another-password"))
    assert auth.decode_token(old) is None
    assert auth.decode_token(auth.create_token("tina", role="teacher")) is not None


# ── role changes ───────────────────────────────────────────


def test_a_learner_made_administrator_leaves_their_assignments(
    author_draft, publish_template, teaching_users, as_account
):
    from deeptutor.teaching.store import TeachingStore

    author_draft()
    template_id = publish_template()["template"]["id"]
    # Older data: the path named Sara and Sam; named receivers are learners only.
    scopes._store_scope(scopes.MASTERY_PATH, template_id,
                        scopes.Scope(users=[teaching_users["sara"]["id"], teaching_users["sam"]["id"]]),
                        actor_id=teaching_users["tina"]["id"])
    scopes.reconcile_template(template_id)
    _set_role(teaching_users, "sam", "admin")
    active = {a.student_user_id for a in TeachingStore().list_assignments(template_id) if a.status == "active"}
    assert active == {teaching_users["sara"]["id"]}


def test_the_first_administrators_role_is_fixed(teaching_users, as_account, monkeypatch):
    from fastapi import HTTPException

    from deeptutor.api.routers import auth as auth_router
    from deeptutor.multi_user.admin_accounts import primary_admin_id

    assert primary_admin_id() == teaching_users["adam"]["id"]
    with pytest.raises(HTTPException) as refused:
        asyncio.run(auth_router.update_user_role(
            "adam", auth_router.SetRoleRequest(role="learner"),
            current=auth_router.TokenPayload(username="other", role="admin", user_id="x"),
        ))
    assert refused.value.status_code == 400


# ── account deletion ───────────────────────────────────────


def test_deleting_accounts_hands_over_practice_sets(teaching_users, as_account):
    from deeptutor.multi_user.account_handoff import run_stages

    with as_account("tom"):
        created = PracticeSetService().create("Tom's set")
    adam = teaching_users["adam"]["id"]
    for name in ("tom", "sam"):
        failed, _ = run_stages(teaching_users[name]["id"], adam, ("publish_targets", "practice_sets"))
        assert failed == []
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


# ── publishing ─────────────────────────────────────────────


@pytest.mark.first_publish_rule
def test_a_first_publication_reaches_nobody_until_they_join_and_admins_still_see_it(
    teaching_users, as_account, tmp_path
):
    from deeptutor.knowledge import registry

    kb = registry.register_kb(
        base_dir=tmp_path / "kbs", name="notes", kind="uploaded", created_by=teaching_users["tina"]["id"],
    )
    with as_account("tina"):
        registry.set_visibility(kb.kb_id, "public")
    scope = get_scope(KNOWLEDGE_BASE, kb.kb_id)
    assert scope is not None and scope.empty
    current = registry.get_kb(kb.kb_id)
    for name, expected in (("sara", False), ("uma", False), ("adam", True)):
        with as_account(name):
            assert registry.can_read(current, get_current_user()) is expected, name


# ── practice sets ──────────────────────────────────────────

QUESTIONS = [
    {"question": "2 + 2 = ?", "question_type": "choice", "options": {"A": "3", "B": "4"},
     "correct_answer": "B", "explanation": "Add."},
]


@pytest.fixture
def practice_set(as_account, deliver):
    """An older set: answers after a deadline still an hour away (both no longer read)."""
    from deeptutor.practice_sets import store as practice_store

    with as_account("tina"):
        service = PracticeSetService()
        created = service.create("Week 1")
        service.update(created["id"], questions=QUESTIONS)
        service.publish(created["id"])
    with practice_store.write() as conn:
        conn.execute("UPDATE practice_sets SET reveal = 'after_due', due_at = ? WHERE id = ?",
                     (time.time() + 3600, created["id"]))
    deliver(PRACTICE_SET, created["id"], "sara", "sam")
    return created["id"], None


def _bank_entry(as_account, name, set_id):
    from deeptutor.services.session.sqlite_store import get_sqlite_session_store

    with as_account(name):
        listed = asyncio.run(get_sqlite_session_store().list_notebook_entries(limit=50))
    rows = listed["items"] if isinstance(listed, dict) else listed
    [row] = [r for r in rows if r.get("material_id") == set_id]
    return row


def test_a_first_published_set_names_nobody_and_reaches_who_joined(practice_set, teaching_users):
    set_id, _ = practice_set
    scope = get_scope(PRACTICE_SET, set_id)
    assert scope is not None and scope.empty
    assert set(scope.members) == {teaching_users["sara"]["id"], teaching_users["sam"]["id"]}


def test_an_older_after_due_set_shows_answers_right_after_submitting(practice_set, as_account):
    set_id, _ = practice_set
    with as_account("sara"):
        assert not PracticeSetService().take(set_id)["answers_revealed"]
        qid = PracticeSetService().take(set_id)["questions"][0]["question_id"]
        result = asyncio.run(PracticeSetService().submit(set_id, {qid: "A"}))
        assert result["answers_revealed"] and result["results"][0]["result"] == "incorrect"
        assert "late" not in result
        taken = PracticeSetService().take(set_id)
        assert taken["answers_revealed"] and taken["reveal"] == "after_submit" and "due_at" not in taken
    entry = _bank_entry(as_account, "sara", set_id)
    assert entry["correct_answer"] == "B" and entry["user_answer"] == "A"


def test_no_answers_before_submitting_even_after_an_older_deadline(practice_set, as_account):
    from deeptutor.practice_sets import store as practice_store

    set_id, _ = practice_set
    with practice_store.write() as conn:
        conn.execute("UPDATE practice_sets SET due_at = ? WHERE id = ?", (time.time() - 1, set_id))
    with as_account("sam"):
        taken = PracticeSetService().take(set_id)
    assert not taken["answers_revealed"] and "correct_answer" not in taken["questions"][0]


def test_reports_keep_learners_who_left(practice_set, as_account, teaching_users):
    set_id, _ = practice_set
    with as_account("sara"):
        qid = PracticeSetService().take(set_id)["questions"][0]["question_id"]
        asyncio.run(PracticeSetService().submit(set_id, {qid: "B"}))
    _leave(teaching_users, "sara", PRACTICE_SET, set_id)
    with as_account("tina"):
        practice = PracticeSetService().report(set_id)
    left = next(s for s in practice["students"] if s["username"] == "sara")
    assert left["completed"] and left["in_scope"] is False


def test_answers_are_graded_against_the_version_the_learner_opened(practice_set, as_account):
    from deeptutor.practice_sets import store as practice_store
    from deeptutor.practice_sets.service import PracticeError

    set_id, _ = practice_set
    with as_account("sara"):
        opened = PracticeSetService().take(set_id)
    qid = opened["questions"][0]["question_id"]
    # The author changes the key and republishes while Sara is answering.
    with as_account("tina"):
        PracticeSetService().update(set_id, questions=[{**QUESTIONS[0], "correct_answer": "A"}])
        PracticeSetService().publish(set_id)
    with as_account("sara"):
        result = asyncio.run(PracticeSetService().submit(
            set_id, {qid: "B"}, revision=opened["current_revision"],
        ))
        with pytest.raises(PracticeError):
            asyncio.run(PracticeSetService().submit(set_id, {qid: "B"}, revision=99))
    assert result["revision"] == opened["current_revision"] == 1
    with practice_store.read() as conn:
        [saved] = conn.execute(
            "SELECT revision, score FROM practice_submissions WHERE set_id = ?", (set_id,)
        ).fetchall()
    assert (saved["revision"], saved["score"]) == (1, 1)


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
    # Invites are ignored: registration does not depend on one, nor reports on it.
    assert result["ok"] and "joined_class" not in result and "invite_error" not in result


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
