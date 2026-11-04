"""Batch 0 of the 2026-11-02 plan: first publication, provenance, withdrawal, hand-over.

Spec: docs/superpowers/specs/2026-11-02-batch0-design.md
"""

from __future__ import annotations

import asyncio

import pytest

from deeptutor.practice_sets.service import PracticeNotFound, PracticeSetService
from deeptutor.publishing import scope as scopes
from deeptutor.publishing.scope import KNOWLEDGE_BASE, MASTERY_PATH, PRACTICE_SET, Scope, get_scope

QUESTIONS = [
    {"question": "2 + 2 = ?", "question_type": "choice", "options": {"A": "3", "B": "4"},
     "correct_answer": "B", "explanation": "Add."},
]


def _kb(teaching_users, tmp_path, owner: str, name: str = "notes"):
    from deeptutor.knowledge import registry

    return registry.register_kb(
        base_dir=tmp_path / f"kbs-{owner}", name=name, kind="uploaded",
        created_by=teaching_users[owner]["id"],
    )


def _draft_set(as_account, name: str, title: str = "Week 1") -> str:
    with as_account(name):
        service = PracticeSetService()
        created = service.create(title)
        service.update(created["id"], questions=QUESTIONS)
    return created["id"]


# ── 2. a first publication names its receivers, or nobody (people join) ──


@pytest.mark.first_publish_rule
def test_without_receivers_a_publication_reaches_nobody_until_they_join(
    teaching_users, as_account, tmp_path
):
    from deeptutor.knowledge import registry

    kb = _kb(teaching_users, tmp_path, "adam")
    with as_account("adam"):
        registry.set_visibility(kb.kb_id, "public")
        assert registry.get_kb(kb.kb_id).visibility == "public"
    scope = get_scope(KNOWLEDGE_BASE, kb.kb_id)
    assert scope is not None and scope.empty
    other = _kb(teaching_users, tmp_path, "adam", name="other")
    with as_account("adam"):
        # "Everyone" is reached through the market only; nothing is published.
        with scopes.chosen_scope(Scope(everyone=True)):
            with pytest.raises(scopes.ScopeError):
                registry.set_visibility(other.kb_id, "public")
        assert registry.get_kb(other.kb_id).visibility == "private"
    assert get_scope(KNOWLEDGE_BASE, other.kb_id) is None


@pytest.mark.first_publish_rule
def test_a_chosen_scope_is_checked_and_a_later_publication_keeps_it(
    teaching_users, as_account, tmp_path, make_learners
):
    from deeptutor.knowledge import registry

    kb = _kb(teaching_users, tmp_path, "tina")
    sara = teaching_users["sara"]["id"]
    make_learners("tina", "sara")
    with as_account("tina"):
        with scopes.chosen_scope(Scope(users=[teaching_users["uma"]["id"]])):
            with pytest.raises(scopes.ScopeError):  # uma is neither a student nor a teacher
                registry.set_visibility(kb.kb_id, "public")
        with scopes.chosen_scope(Scope(users=[teaching_users["sam"]["id"]])):
            with pytest.raises(scopes.ScopeError):  # Sam is no learner of Tina's
                registry.set_visibility(kb.kb_id, "public")
        with scopes.chosen_scope(Scope(users=[sara])):
            registry.set_visibility(kb.kb_id, "public")
        registry.set_visibility(kb.kb_id, "private")
        registry.set_visibility(kb.kb_id, "public")
    assert get_scope(KNOWLEDGE_BASE, kb.kb_id).users == [sara]


@pytest.mark.first_publish_rule
def test_practice_sets_and_paths_publish_to_nobody_until_they_join(
    teaching_users, as_account, teacher_draft, publish_template
):
    from deeptutor.teaching.store import TeachingStore

    set_id = _draft_set(as_account, "adam")
    with as_account("adam"):
        assert PracticeSetService().publish(set_id)["status"] == "published"
    assert get_scope(PRACTICE_SET, set_id).empty
    with as_account("sara"):
        assert PracticeSetService().assigned() == []

    teacher_draft("tom", path_id="topic_tom")
    template_id = publish_template("tom", path_id="topic_tom")["template"]["id"]
    assert get_scope(MASTERY_PATH, template_id).empty
    assert [a for a in TeachingStore().list_assignments(template_id) if a.status == "active"] == []


# ── 4. cited materials some students cannot read ───────────


def test_cited_materials_out_of_the_students_reach_are_flagged(
    teaching_users, as_account, tmp_path, teacher_draft, publish_template, make_learners
):
    from deeptutor.knowledge import registry
    from deeptutor.publishing.service import PublishService
    from deeptutor.teaching.service import TeachingService
    from deeptutor.teaching.snapshot import SourcePlan

    teacher_draft()
    template = TeachingService().store.get_template(
        publish_template(students=("sara", "sam"))["template"]["id"]
    )
    make_learners("tom", "sara")
    kb = _kb(teaching_users, tmp_path, "tom")
    with as_account("tom"):
        registry.set_visibility(kb.kb_id, "public")
        PublishService().set_scope(KNOWLEDGE_BASE, kb.kb_id, everyone=False,
                                   users=[teaching_users["sara"]["id"]], confirm=True)
    own = _kb(teaching_users, tmp_path, "tina", name="mine")
    plan = SourcePlan(kb_refs={kb.kb_id: None, own.kb_id: None})
    with as_account("tina"):
        warnings = TeachingService()._material_reach(template, plan)
    # Sam cannot read Tom's notes; Tina's own knowledge base is no concern (D8).
    assert warnings == [{"kind": KNOWLEDGE_BASE, "id": kb.kb_id, "name": "notes", "missing": 1}]


# ── 5. provenance ──────────────────────────────────────────


def test_provenance_follows_the_publishers_live_role(teaching_users, as_account, seed_user):
    from deeptutor.multi_user.context import get_current_user
    from deeptutor.multi_user.identity import set_role
    from deeptutor.multi_user.models import LOCAL_ADMIN_ID
    from deeptutor.publishing.provenance import provenance

    stu = seed_user("stu", role="student")["id"]
    with as_account("sara"):
        me = get_current_user()
        assert provenance(me.id, me) == {"kind": "own", "name": ""}
        assert provenance(teaching_users["adam"]["id"], me)["kind"] == "school"
        assert provenance(LOCAL_ADMIN_ID, me)["kind"] == "school"
        assert provenance(teaching_users["tina"]["id"], me) == {"kind": "teacher", "name": "tina"}
        assert provenance(stu, me) == {"kind": "shared", "name": "stu"}
    set_role("tina", "admin")
    with as_account("sara"):
        assert provenance(teaching_users["tina"]["id"], get_current_user())["kind"] == "school"


def test_assigned_practice_sets_say_where_they_come_from(as_account, deliver):
    set_id = _draft_set(as_account, "tina")
    with as_account("tina"):
        PracticeSetService().publish(set_id)
    deliver(PRACTICE_SET, set_id, "sara")
    with as_account("sara"):
        [row] = PracticeSetService().assigned()
    assert row["provenance"] == {"kind": "teacher", "name": "tina"}


# ── 6. administrators withdraw others' practice sets ───────


def test_admins_withdraw_but_never_open_others_practice_sets(as_account, deliver):
    set_id = _draft_set(as_account, "tina")
    with as_account("adam"):
        assert PracticeSetService().withdrawable() == []  # a draft is nobody else's business
    with as_account("tina"):
        PracticeSetService().publish(set_id)
    deliver(PRACTICE_SET, set_id, "sara")
    with as_account("sara"):
        assert [row["id"] for row in PracticeSetService().assigned()] == [set_id]
    with as_account("adam"):
        service = PracticeSetService()
        assert [row["id"] for row in service.withdrawable()] == [set_id]
        for action in (lambda: service.get(set_id), lambda: service.report(set_id),
                       lambda: service.update(set_id, title="Mine now")):
            with pytest.raises(PracticeNotFound):
                action()
        service.delete(set_id)  # an administrator's "delete" withdraws
    with as_account("sara"):
        assert PracticeSetService().assigned() == []
    with as_account("tina"):
        assert PracticeSetService().get(set_id)["status"] == "archived"


# ── 7. a teacher who becomes a student hands over what they published ──


def test_a_demoted_teacher_hands_over_only_what_they_published(
    teaching_users, as_account, tmp_path, teacher_draft, publish_template, monkeypatch, deliver
):
    from deeptutor.api.routers import auth as auth_router
    from deeptutor.knowledge import registry
    from deeptutor.teaching.store import TeachingStore

    adam, tina = teaching_users["adam"]["id"], teaching_users["tina"]["id"]
    teacher_draft()
    template_id = publish_template()["template"]["id"]
    published_set = _draft_set(as_account, "tina")
    draft_set = _draft_set(as_account, "tina", title="Unfinished")
    public_kb = _kb(teaching_users, tmp_path, "tina", name="shared")
    private_kb = _kb(teaching_users, tmp_path, "tina", name="private")
    with as_account("tina"):
        PracticeSetService().publish(published_set)
        registry.set_visibility(public_kb.kb_id, "public")
    deliver(PRACTICE_SET, published_set, "sara")

    asyncio.run(auth_router.update_user_role(
        "tina", auth_router.SetRoleRequest(role="student"),
        current=auth_router.TokenPayload(username="adam", role="admin", user_id=adam),
    ))
    assert TeachingStore().get_template(template_id).owner_user_id == adam
    assert registry.get_kb(public_kb.kb_id).created_by == adam
    assert registry.get_kb(private_kb.kb_id).created_by == tina
    with as_account("adam"):
        assert PracticeSetService().get(published_set)["status"] == "published"
    from deeptutor.practice_sets import store as practice_store

    with practice_store.read() as conn:
        owner = conn.execute("SELECT owner_user_id FROM practice_sets WHERE id = ?", (draft_set,)).fetchone()
    assert owner["owner_user_id"] == tina
    # Students keep learning what was published.
    with as_account("sara"):
        assert [row["id"] for row in PracticeSetService().assigned()] == [published_set]


def test_a_failed_hand_over_leaves_the_role_unchanged(teaching_users, monkeypatch):
    from fastapi import HTTPException

    from deeptutor.api.routers import auth as auth_router
    from deeptutor.multi_user import content_lifecycle as lifecycle
    from deeptutor.multi_user.identity import get_user

    def broken(*_args, **_kwargs):
        raise RuntimeError("store is read-only")

    monkeypatch.setattr(lifecycle, "hand_over_published", broken)
    with pytest.raises(HTTPException):
        asyncio.run(auth_router.update_user_role(
            "tina", auth_router.SetRoleRequest(role="student"),
            current=auth_router.TokenPayload(username="adam", role="admin",
                                              user_id=teaching_users["adam"]["id"]),
        ))
    assert get_user("tina")["role"] == "teacher"
