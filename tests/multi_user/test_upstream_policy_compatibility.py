"""Compatibility for grants written by the retired develop policy extension."""

from copy import deepcopy
import json

import pytest

from deeptutor.multi_user.grants import grant_path, load_grant, save_grant


def legacy_policy():
    return {
        "learning_policy": {
            "policy_version": 2,
            "age_band": "9-12",
            "locked_persona": "peer",
            "allowed_capabilities": ["chat", "immersive_reading", "mastery_path"],
            "default_capability": "mastery_path",
            "allowed_surfaces": ["chat", "reading", "books", "settings", "knowledge"],
            "reading": {"allow_upload": False, "material_ids": ["assigned"], "extensions": []},
        },
        "exec_enabled": False,
        "enabled_tools": [],
    }


def test_legacy_projection_preserves_file_and_persists_on_save(mu_isolated_root, seed_user):
    seed_user("admin", role="admin")
    user = seed_user("learner")
    raw = legacy_policy()
    path = grant_path(user["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    original = json.dumps(raw)
    path.write_text(original)
    grant = load_grant(user["id"])
    policy = grant["learning_policy"]
    assert policy["allowed_capabilities"] == ["chat", "immersive_reading"]
    assert policy["allowed_surfaces"] == ["chat", "reading"]
    assert policy["locked_persona"] == "teacher"
    assert policy["default_capability"] == "chat"
    assert policy["reading"] == raw["learning_policy"]["reading"]
    assert path.read_text() == original
    save_grant(user["id"], grant)
    assert load_grant(user["id"]) == grant
    assert "policy_version" not in json.loads(path.read_text())["learning_policy"]


def test_removed_only_surfaces_do_not_grant_default_access(monkeypatch):
    from deeptutor.multi_user import learning_access
    from deeptutor.multi_user.grants import normalize_grant

    raw = legacy_policy()
    raw["learning_policy"]["allowed_surfaces"] = ["books"]
    before = deepcopy(raw)
    policy = normalize_grant("test", raw)["learning_policy"]
    assert raw == before
    assert policy["allowed_surfaces"] == []
    monkeypatch.setattr(learning_access, "current_learning_policy", lambda: policy)
    with pytest.raises(PermissionError):
        learning_access.assert_learning_surface("chat")


def test_admin_persona_resolution_uses_upstream_overlay(monkeypatch):
    from deeptutor.multi_user import learning_access
    from deeptutor.services import persona

    monkeypatch.setattr(learning_access, "current_learning_policy", lambda: None)
    monkeypatch.setattr(persona, "get_persona_service", lambda: object())
    monkeypatch.setattr(persona, "load_visible_for_context", lambda name, **kw: "admin preset")
    assert learning_access.resolve_persona_context("teacher", is_admin=True) == (
        "admin preset", "teacher",
    )


def test_removed_only_grant_requires_admin_review(mu_isolated_root, seed_user):
    from deeptutor.multi_user.grants import GrantStorageError

    seed_user("admin", role="admin")
    user = seed_user("custom_learner")
    raw = legacy_policy()
    raw["learning_policy"]["allowed_surfaces"] = ["books"]
    path = grant_path(user["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    original = json.dumps(raw)
    path.write_text(original)
    with pytest.raises(GrantStorageError, match="is invalid") as error:
        load_grant(user["id"])
    assert "allowed_surfaces cannot be empty" in str(error.value.__cause__)
    assert path.read_text() == original


def test_workspace_resolution_failure_refuses_write(monkeypatch):
    from deeptutor.multi_user import learning_access
    from deeptutor.services import path_service

    monkeypatch.setattr(learning_access, "current_learning_policy", lambda: {})

    def broken():
        raise RuntimeError("unavailable")

    monkeypatch.setattr(path_service, "get_path_service", broken)
    with pytest.raises(PermissionError, match="Workspace isolation failed"):
        learning_access.assert_workspace_isolated()
