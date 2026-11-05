"""Second permission audit (2026-11-02): accounts, sockets, scopes and practice sets.

Spec 2026-10-29: the first administrator is fixed; sockets follow the same
learning surfaces as REST; citing a knowledge base never widens its scope;
administrators withdraw or delete others' content and never re-target it (D10);
practice answers (and right/wrong) wait until the student has submitted.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from deeptutor.practice_sets.service import PracticeSetService
from deeptutor.publishing import scope as scopes
from deeptutor.publishing.scope import BOOK, MASTERY_PATH, PRACTICE_SET, get_scope

QUESTIONS = [
    {"question": "2 + 2 = ?", "question_type": "choice", "options": {"A": "3", "B": "4"},
     "correct_answer": "B", "explanation": "Add."},
]


def _admin(teaching_users, name: str):
    from deeptutor.api.routers import auth as auth_router

    return auth_router.TokenPayload(username=name, role="admin", user_id=teaching_users[name]["id"])


# ── accounts ───────────────────────────────────────────────


def test_another_admin_resets_but_never_deletes_the_first_admin(teaching_users, monkeypatch):
    from deeptutor.api.routers import auth as auth_router
    from deeptutor.multi_user.admin_accounts import primary_admin_id
    from deeptutor.multi_user.identity import set_role
    from deeptutor.services import auth

    monkeypatch.setattr(auth, "AUTH_SECRET", "s" * 48)
    assert primary_admin_id() == teaching_users["adam"]["id"]
    set_role("tom", "admin")
    tom = _admin(teaching_users, "tom")
    old = auth.create_token("adam", role="admin")
    # D20: a forgotten first-admin password is recoverable by another administrator.
    asyncio.run(auth_router.admin_reset_password(
        "adam", auth_router.AdminPasswordReset(new_password="recovered-1"), current=tom,
    ))
    assert auth.decode_token(old) is None
    assert auth.authenticate("adam", "recovered-1") is not None
    assert auth_router._delete_admin_user("adam", tom)[0] == 400


def test_a_name_registered_twice_at_once_never_takes_over_the_first(teaching_users):
    from deeptutor.multi_user.identity import UsernameTaken, get_user, save_user

    before = get_user("sara")
    with pytest.raises(UsernameTaken):
        save_user("sara", "other-hash", role="student", only_new=True)
    assert get_user("sara")["hash"] == before["hash"]


def test_a_password_change_voids_a_pending_session_handoff(teaching_users):
    from deeptutor.api.routers import auth as auth_router
    from deeptutor.multi_user.identity import set_password

    before = auth_router._credentials_version("sara")
    set_password("sara", "new-hash")
    assert auth_router._credentials_version("sara") == before + 1
    assert auth_router._credentials_version("nobody") is None


def test_sockets_follow_the_learning_surfaces_of_their_rest_routes(monkeypatch):
    from deeptutor.api.routers.auth import _learning_surface_for_socket
    from deeptutor.multi_user import learning_access
    from deeptutor.multi_user.learning_access import assert_learning_surface

    assert _learning_surface_for_socket("/ws/books") == "books"
    assert _learning_surface_for_socket("/ws") == "chat"
    assert _learning_surface_for_socket("/ws/mastery-paths") == "chat"
    assert _learning_surface_for_socket("/ws/questions/judge") == "chat"
    assert _learning_surface_for_socket("/ws/knowledge-bases/kb/progress") == "reading"
    assert _learning_surface_for_socket("/ws/partners/p1") == ""
    # A learner whose policy leaves out books (the default learner surfaces).
    monkeypatch.setattr(learning_access, "current_learning_policy",
                        lambda: {"allowed_surfaces": ["chat", "reading"]})
    with pytest.raises(PermissionError):
        assert_learning_surface(_learning_surface_for_socket("/ws/books"))
    assert_learning_surface(_learning_surface_for_socket("/ws"))


# ── scopes ─────────────────────────────────────────────────


def test_citing_a_scoped_kb_passes_nothing_on(teaching_users, as_account, tmp_path):
    from deeptutor.knowledge import registry
    from deeptutor.publishing.scope import KNOWLEDGE_BASE
    from deeptutor.multi_user.context import get_current_user

    kb = registry.register_kb(
        base_dir=tmp_path / "kbs", name="notes", kind="uploaded", created_by=teaching_users["tina"]["id"],
    )
    with as_account("tina"):
        registry.set_visibility(kb.kb_id, "public")
    # An older scope names Tom.
    scopes._store_scope(KNOWLEDGE_BASE, kb.kb_id, scopes.Scope(users=[teaching_users["tom"]["id"]]),
                        actor_id=teaching_users["tina"]["id"])
    # Tom cites it in something every reader may open (a book everyone may read).
    registry.register_reference_reader("zz_everyone", lambda referrer_id, user_id: True)
    registry.add_reference(kb.kb_id, referrer_kind="zz_everyone", referrer_id="b1",
                           created_by=teaching_users["tom"]["id"])
    current = registry.get_kb(kb.kb_id)
    for name, expected in (("tom", True), ("sara", False)):
        with as_account(name):
            assert registry.can_read(current, get_current_user()) is expected, name
    # The owner's own references still share it.
    registry.add_reference(kb.kb_id, referrer_kind="zz_everyone", referrer_id="b2",
                           created_by=teaching_users["tina"]["id"])
    with as_account("sara"):
        assert registry.can_read(registry.get_kb(kb.kb_id), get_current_user())


def test_a_lost_scope_store_fails_closed(teaching_books, teaching_users, as_account):
    from deeptutor.book import registry as book_registry
    from deeptutor.multi_user.context import get_current_user
    from deeptutor.publishing import store as scope_store

    book_id = teaching_books.public.book_id
    scopes._store_scope(BOOK, book_id, scopes.Scope(users=[teaching_users["sam"]["id"]]),
                        actor_id=teaching_users["tom"]["id"])
    scope_store.scopes_db_path().unlink()
    record = book_registry.get_book(book_id)
    with as_account("sara"):
        assert not book_registry.can_read_book(record, get_current_user())
    with pytest.raises(scope_store.ScopeStoreMissing):
        with scope_store.write():
            pass


# ── mastery paths ──────────────────────────────────────────


def test_admins_withdraw_teacher_paths(teacher_draft, publish_template, as_account):
    from deeptutor.teaching.service import TeachingService

    teacher_draft()
    template_id = publish_template()["template"]["id"]
    with as_account("adam"):
        assert TeachingService().archive(template_id).status == "archived"
    assert get_scope(MASTERY_PATH, template_id).users == []


# ── practice sets ──────────────────────────────────────────


@pytest.fixture
def after_due_set(as_account, deliver):
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
    deliver(PRACTICE_SET, created["id"], "sara")
    return created["id"]


def _bank(as_account, set_id):
    from deeptutor.services.session.sqlite_store import get_sqlite_session_store

    with as_account("sara"):
        listed = asyncio.run(get_sqlite_session_store().list_notebook_entries(limit=50))
    rows = listed["items"] if isinstance(listed, dict) else listed
    return [r for r in rows if r.get("material_id") == set_id]


def test_right_or_wrong_shows_once_submitted_even_for_an_older_after_due_set(after_due_set, as_account):
    set_id = after_due_set
    with as_account("sara"):
        qid = PracticeSetService().take(set_id)["questions"][0]["question_id"]
        result = asyncio.run(PracticeSetService().submit(set_id, {qid: "B"}))
        assert result["score"] == 1 and result["results"][0]["result"] == "correct"
        taken = PracticeSetService().take(set_id)
        assert taken["submissions"][0]["score"] == 1
        assert taken["latest_results"][0]["result"] == "correct"
        assert "answer" not in taken["latest_results"][0]
        # Their own answer comes back from their own question bank (spec 2026-11-02 §9.3).
        restored = asyncio.run(PracticeSetService().with_my_answers(taken))
        assert restored["latest_results"][0]["answer"] == "B"
    from deeptutor.practice_sets import store as practice_store

    with practice_store.read() as conn:
        [stored] = conn.execute("SELECT results_json FROM practice_submissions").fetchall()
    assert "answer" not in stored["results_json"]
    with as_account("sara"):
        assert PracticeSetService().assigned()[0]["best_score"] == 1
    [entry] = _bank(as_account, set_id)
    assert entry["result"] == "correct" and entry["correct_answer"] == "B"


def test_answers_of_an_older_version_are_completed_after_republishing(after_due_set, as_account):
    set_id = after_due_set
    with as_account("sara"):
        qid = PracticeSetService().take(set_id)["questions"][0]["question_id"]
        asyncio.run(PracticeSetService().submit(set_id, {qid: "A"}))
    with as_account("tina"):
        PracticeSetService().update(set_id, questions=QUESTIONS * 2)
        PracticeSetService().publish(set_id)
    with as_account("sara"):
        asyncio.run(PracticeSetService().reveal_in_bank(set_id))
    [entry] = [e for e in _bank(as_account, set_id) if e.get("turn_id") == "rev1"]
    assert entry["result"] == "incorrect" and entry["correct_answer"] == "B"


def test_best_scores_keep_their_own_versions_maximum(as_account, deliver):
    with as_account("tina"):
        service = PracticeSetService()
        created = service.create("Grows")
        service.update(created["id"], questions=QUESTIONS)
        service.publish(created["id"])
    set_id = created["id"]
    deliver(PRACTICE_SET, set_id, "sara")
    with as_account("sara"):
        qid = PracticeSetService().take(set_id)["questions"][0]["question_id"]
        asyncio.run(PracticeSetService().submit(set_id, {qid: "B"}))  # 1 / 1
    with as_account("tina"):
        PracticeSetService().update(set_id, questions=QUESTIONS * 4)
        PracticeSetService().publish(set_id)
    with as_account("sara"):
        taken = PracticeSetService().take(set_id)
        answers = {q["question_id"]: "B" for q in taken["questions"][:2]}
        asyncio.run(PracticeSetService().submit(set_id, answers))  # 2 / 4
    with as_account("tina"):
        [sara] = [s for s in PracticeSetService().report(set_id)["students"] if s["username"] == "sara"]
    assert (sara["best_score"], sara["best_max_score"]) == (1, 1)
