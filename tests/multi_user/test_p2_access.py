"""P2: guardians retired, no personal models, no partner lending, admin-only workspaces."""

from __future__ import annotations

import asyncio

from fastapi import HTTPException
import pytest

from deeptutor.multi_user import admin_accounts, paths
from deeptutor.multi_user.grants import load_grant, save_grant
from deeptutor.multi_user.models import CurrentUser


def _refused(coro, status: int):
    with pytest.raises(HTTPException) as caught:
        asyncio.run(coro)
    assert caught.value.status_code == status
    return caught.value.detail


def test_guardian_routes_are_closed(seed_user):
    from deeptutor.api.routers import multi_user as router

    seed_user("boss", role="admin")
    detail = _refused(router.list_guardian_relationships(), 404)
    assert detail == router.GUARDIANS_RETIRED
    _refused(router.my_guardianships(current=None), 404)
    _refused(router.guardian_report("someone", current=None), 404)


def test_only_the_primary_admin_signs_in_to_codex(seed_user, as_user):
    from deeptutor.api.routers.settings import _require_codex_oauth_actor

    boss = seed_user("boss", role="admin")
    admin_accounts.primary_admin_id()
    ann = seed_user("ann", role="admin")
    with as_user("tina", role="teacher"):
        with pytest.raises(HTTPException) as caught:
            _require_codex_oauth_actor()
        assert caught.value.status_code == 403
    from deeptutor.multi_user.context import reset_current_user, set_current_user

    token = set_current_user(CurrentUser(
        ann["id"], "ann", "admin", paths.scope_for_user(ann["id"], is_admin=True),
    ))
    try:
        with pytest.raises(HTTPException):
            _require_codex_oauth_actor()
    finally:
        reset_current_user(token)
    token = set_current_user(CurrentUser(
        boss["id"], "boss", "admin", paths.scope_for_user(boss["id"], is_admin=True),
    ))
    try:
        _require_codex_oauth_actor()
    finally:
        reset_current_user(token)


def test_nobody_gets_personal_model_profiles(as_user):
    from deeptutor.multi_user.personal_models import personal_llm_rows

    with as_user("sam", role="student"):
        assert personal_llm_rows() == []


def test_lent_partners_no_longer_take_effect(seed_user):
    from deeptutor.multi_user.partner_access import assigned_partner_ids

    seed_user("boss", role="admin")
    sam = seed_user("sam", role="student")
    grant = load_grant(sam["id"])
    grant["partners"] = [{"partner_id": "ada", "name": "Ada", "source": "admin"}]
    save_grant(sam["id"], grant)
    assert assigned_partner_ids(sam["id"]) == set()


def test_workspace_management_is_admin_only(as_user):
    from deeptutor.api.routers import workspace

    payload = workspace.CreateWorkspacePayload(name="Algebra")
    with as_user("tina", role="teacher"):
        detail = _refused(workspace.create_workspace(payload), 403)
        assert detail == workspace.WORKSPACES_ADMIN_ONLY
        _refused(workspace.discover_workspace_data(), 403)


def test_a_learning_policy_alone_marks_the_restricted_experience(seed_user):
    from deeptutor.multi_user.learning_access import learning_policy_for_user

    seed_user("boss", role="admin")
    sam = seed_user("sam", role="student")
    assert learning_policy_for_user(sam["id"]) is None
    from deeptutor.multi_user.grants import learner_grant

    grant = load_grant(sam["id"])
    grant["learning_policy"] = learner_grant(sam["id"])["learning_policy"]
    save_grant(sam["id"], grant)
    assert learning_policy_for_user(sam["id"]) is not None
