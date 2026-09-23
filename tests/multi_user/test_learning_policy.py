"""Server-side policy resolution for learning accounts."""

from __future__ import annotations

import pytest

from deeptutor.multi_user.grants import (
    grant_path,
    migrate_learner_grant,
    normalize_grant,
    save_grant,
    validate_grant,
)
from deeptutor.multi_user.identity import set_preset
from deeptutor.multi_user.learning_access import (
    allowed_reading_extensions,
    apply_learning_policy,
    assert_learning_material,
    assert_learning_surface,
    learning_policy_for_user,
)


def _policy_grant(material_ids: list[str] | None = None, extensions: list[str] | None = None):
    return {
        "enabled_tools": [],
        "mcp_tools": [],
        "cli_apps": [],
        "exec_enabled": False,
        "learning_policy": {
            "age_band": "9-12",
            "locked_persona": "teacher",
            "allowed_capabilities": ["chat", "immersive_reading"],
            "default_capability": "immersive_reading",
            "allowed_surfaces": ["chat", "reading"],
            "reading": {
                "allow_upload": False,
                "material_ids": material_ids or [],
                "extensions": extensions or [],
            },
        },
    }


def _seed_learner(seed_user, name: str):
    seed_user("admin", role="admin")
    return seed_user(name)


def test_legacy_grants_remain_unrestricted(mu_isolated_root):
    grant = normalize_grant("u_legacy", {"version": 1})
    assert grant["learning_policy"] is None
    validate_grant(grant)


def test_learning_policy_validation_rejects_unsafe_values(mu_isolated_root):
    grant = normalize_grant(
        "u_student",
        _policy_grant(extensions=["not valid"]),
    )
    grant["learning_policy"]["allowed_capabilities"] = ["deep_research"]

    with pytest.raises(ValueError, match="unsupported values"):
        validate_grant(grant)


def test_validate_grant_allows_capability_without_its_surface(mu_isolated_root):
    """Capabilities and surfaces are independent dimensions.

    A capability enabled without its surface must validate: the surface guard
    runs as a request-level dependency ahead of the route handler, so the page
    is already unreachable. Coupling the two in validate_grant would break the
    guardian endpoint, which may only edit surfaces — never capabilities.
    """
    grant = normalize_grant(
        "u_student",
        {
            "learning_policy": {
                "age_band": "9-12",
                "locked_persona": "teacher",
                "allowed_capabilities": ["chat", "immersive_reading", "mastery_path"],
                "default_capability": "immersive_reading",
                "allowed_surfaces": ["reading"],
                "reading": {"allow_upload": False, "material_ids": [], "extensions": []},
            }
        },
    )

    validate_grant(grant)


def test_surface_guard_blocks_a_capability_whose_surface_is_off(
    mu_isolated_root, seed_user, as_user
):
    """The runtime defence for the decoupled state: surface off => denied.

    This is what makes the relaxed validate_grant safe — it is asserted here so
    removing the surface guard cannot silently widen learner access.
    """
    learner = _seed_learner(seed_user, "student_decoupled")
    save_grant(
        learner["id"],
        {
            "enabled_tools": [],
            "mcp_tools": [],
            "cli_apps": [],
            "exec_enabled": False,
            "learning_policy": {
                "age_band": "9-12",
                "locked_persona": "teacher",
                "allowed_capabilities": ["chat", "immersive_reading"],
                "default_capability": "immersive_reading",
                "allowed_surfaces": ["reading"],
                "reading": {"allow_upload": False, "material_ids": [], "extensions": []},
            },
        },
    )

    with as_user(learner["id"], username="student_decoupled"):
        # chat capability is granted, but its surface is not — the page is
        # unreachable even though the capability check itself would pass.
        with pytest.raises(PermissionError, match="chat surface"):
            assert_learning_surface("chat")
        assert_learning_surface("reading")


def test_validate_grant_accepts_capability_with_surface(mu_isolated_root):
    """validate_grant accepts mastery_path when mastery surface is present."""
    grant = normalize_grant(
        "u_student",
        {
            "learning_policy": {
                "age_band": "9-12",
                "locked_persona": "teacher",
                "allowed_capabilities": ["chat", "immersive_reading", "mastery_path"],
                "default_capability": "immersive_reading",
                "allowed_surfaces": ["chat", "reading", "mastery"],
                "reading": {"allow_upload": False, "material_ids": [], "extensions": []},
            }
        },
    )
    validate_grant(grant)


def test_apply_policy_keeps_allowed_modes_and_strips_the_turn_surface(
    mu_isolated_root, seed_user, as_user
):
    learner = _seed_learner(seed_user, "student")
    save_grant(learner["id"], _policy_grant())

    with as_user(learner["id"], username="student"):
        result = apply_learning_policy(
            {
                "capability": "immersive_reading",
                "persona": "friend",
                "tools": [{"name": "web_search"}],
                "enabled_tools": ["web_search"],
                "knowledge_bases": ["admin:kb:private"],
                "kb_name": "private",
                "enable_rag": True,
                "enable_web_search": True,
                "partner_id": "partner",
                "bot_id": "bot",
            }
        )

    assert result["persona"] == "teacher"
    assert result["tools"] == []
    assert result["enabled_tools"] == []
    assert result["knowledge_bases"] == []
    assert result["kb_name"] == ""
    assert result["enable_rag"] is False
    assert result["enable_web_search"] is False
    assert result["partner_id"] is None
    assert result["bot_id"] is None


def test_apply_policy_rejects_unallowed_capabilities(mu_isolated_root, seed_user, as_user):
    learner = _seed_learner(seed_user, "student")
    save_grant(learner["id"], _policy_grant())

    with as_user(learner["id"], username="student"):
        with pytest.raises(PermissionError, match="cannot use this mode"):
            apply_learning_policy({"capability": "deep_research"})


def test_surface_material_upload_and_extension_guards(mu_isolated_root, seed_user, as_user):
    learner = _seed_learner(seed_user, "student")
    grant = save_grant(
        learner["id"],
        _policy_grant(
            material_ids=["rm_allowed"],
            extensions=["read_aloud"],
        ),
    )

    with as_user(learner["id"], username="student"):
        assert_learning_surface("chat")
        assert_learning_surface("reading")
        with pytest.raises(PermissionError, match="knowledge surface"):
            assert_learning_surface("knowledge")

        assert_learning_material("rm_allowed")
        with pytest.raises(PermissionError, match="not assigned"):
            assert_learning_material("rm_private")
        with pytest.raises(PermissionError, match="cannot upload"):
            assert_learning_material("", upload=True)

        assert allowed_reading_extensions() == {"read_aloud"}


def test_policy_without_explicit_reading_keeps_legacy_access(mu_isolated_root, seed_user, as_user):
    learner = _seed_learner(seed_user, "legacy-learner")
    grant = save_grant(
        learner["id"],
        {
            "learning_policy": {
                "age_band": "9-12",
                "locked_persona": "teacher",
                "allowed_capabilities": ["chat", "immersive_reading"],
                "default_capability": "immersive_reading",
            }
        },
    )

    assert grant["learning_policy"]["reading"] == {
        "allow_upload": True,
        "material_ids": ["*"],
        "extensions": [
            "read_aloud",
            "guided_learning",
            "vocabulary",
            "quiz",
            "translation",
        ],
    }
    with as_user(learner["id"], username="legacy-learner"):
        assert_learning_material("rm_anything")
        assert_learning_material("", upload=True)
        assert_learning_surface("reading")
        assert allowed_reading_extensions() == {
            "read_aloud",
            "guided_learning",
            "vocabulary",
            "quiz",
            "translation",
        }


def test_learner_preset_falls_back_to_the_conservative_policy(mu_isolated_root, seed_user):
    learner = _seed_learner(seed_user, "student")
    assert set_preset("student", "learner")

    policy = learning_policy_for_user(learner["id"], is_admin=False)

    assert policy is not None
    assert policy["default_capability"] == "immersive_reading"
    assert policy["reading"] == {
        "allow_upload": False,
        "material_ids": [],
        "extensions": [],
    }


_OLD_LEARNER_POLICY_INPUT = {
    "age_band": "9-12",
    "locked_persona": "teacher",
    "allowed_capabilities": ["chat", "immersive_reading"],
    "default_capability": "immersive_reading",
    "allowed_surfaces": ["chat", "reading"],
    "reading": {
        "allow_upload": False,
        "material_ids": [],
        "extensions": [],
    },
}


def test_old_default_learner_grant_migrated_with_mastery(mu_isolated_root, seed_user):
    """Learner-preset accounts with the pre-mastery default policy gain mastery."""
    learner = _seed_learner(seed_user, "student")
    set_preset("student", "learner")
    grant = normalize_grant(
        learner["id"],
        {"learning_policy": dict(_OLD_LEARNER_POLICY_INPUT)},
    )
    policy = grant["learning_policy"]
    assert "mastery_path" in policy["allowed_capabilities"]
    assert "mastery" in policy["allowed_surfaces"]
    assert policy["policy_version"] == 2
    validate_grant(grant)


def test_standard_account_with_old_defaults_not_migrated(mu_isolated_root, seed_user):
    """Standard accounts are never migrated even when policy matches old defaults."""
    user = seed_user("standard-user")
    grant = normalize_grant(
        user["id"],
        {"learning_policy": dict(_OLD_LEARNER_POLICY_INPUT)},
    )
    policy = grant["learning_policy"]
    assert policy["allowed_capabilities"] == ["chat", "immersive_reading"]
    assert policy["allowed_surfaces"] == ["chat", "reading"]


def test_custom_account_with_old_defaults_not_migrated(mu_isolated_root, seed_user):
    """Custom accounts are never migrated even when policy matches old defaults."""
    user = _seed_learner(seed_user, "custom-student")
    set_preset("custom-student", "custom")
    grant = normalize_grant(
        user["id"],
        {"learning_policy": dict(_OLD_LEARNER_POLICY_INPUT)},
    )
    policy = grant["learning_policy"]
    assert policy["allowed_capabilities"] == ["chat", "immersive_reading"]
    assert policy["allowed_surfaces"] == ["chat", "reading"]


def test_learner_with_custom_policy_not_migrated(mu_isolated_root, seed_user):
    """Learner-preset accounts with non-default policy are not migrated."""
    learner = _seed_learner(seed_user, "student")
    set_preset("student", "learner")
    grant = normalize_grant(
        learner["id"],
        {
            "learning_policy": {
                "age_band": "9-12",
                "locked_persona": "teacher",
                "allowed_capabilities": ["chat"],
                "default_capability": "chat",
                "allowed_surfaces": ["chat"],
                "reading": {
                    "allow_upload": False,
                    "material_ids": [],
                    "extensions": [],
                },
            }
        },
    )
    policy = grant["learning_policy"]
    assert "mastery_path" not in policy["allowed_capabilities"]
    assert "mastery" not in policy["allowed_surfaces"]
    validate_grant(grant)


def test_learner_with_custom_reading_not_migrated(mu_isolated_root, seed_user):
    """Learner with default top-level but custom reading config is not migrated."""
    learner = _seed_learner(seed_user, "student")
    set_preset("student", "learner")
    grant = normalize_grant(
        learner["id"],
        {
            "learning_policy": {
                "age_band": "9-12",
                "locked_persona": "teacher",
                "allowed_capabilities": ["chat", "immersive_reading"],
                "default_capability": "immersive_reading",
                "allowed_surfaces": ["chat", "reading"],
                "reading": {
                    "allow_upload": True,
                    "material_ids": ["rm_specific"],
                    "extensions": ["read_aloud"],
                },
            }
        },
    )
    policy = grant["learning_policy"]
    assert policy["allowed_capabilities"] == ["chat", "immersive_reading"]
    assert policy["allowed_surfaces"] == ["chat", "reading"]


def test_unmapped_surface_still_denied(mu_isolated_root, seed_user, as_user):
    """Paths not in the surface mapping return '' and are denied."""
    learner = _seed_learner(seed_user, "student")
    save_grant(learner["id"], _policy_grant())

    with as_user(learner["id"], username="student"):
        with pytest.raises(PermissionError, match="requested server surface"):
            assert_learning_surface("")


def test_mastery_surface_allowed_for_learner_with_grant(mu_isolated_root, seed_user, as_user):
    """Learner with mastery in allowed_surfaces can access the mastery surface."""
    learner = _seed_learner(seed_user, "student")
    grant_data = _policy_grant()
    grant_data["learning_policy"]["allowed_surfaces"] = ["chat", "reading", "mastery"]
    grant_data["learning_policy"]["allowed_capabilities"] = [
        "chat", "immersive_reading", "mastery_path",
    ]
    save_grant(learner["id"], grant_data)

    with as_user(learner["id"], username="student"):
        assert_learning_surface("mastery")


def test_mastery_surface_denied_without_grant(mu_isolated_root, seed_user, as_user):
    """Learner without mastery in allowed_surfaces is denied the mastery surface."""
    learner = _seed_learner(seed_user, "student")
    grant_data = {
        "learning_policy": {
            "age_band": "13-15",
            "locked_persona": "teacher",
            "allowed_capabilities": ["chat", "immersive_reading"],
            "default_capability": "chat",
            "allowed_surfaces": ["chat", "reading"],
            "reading": {"allow_upload": False, "material_ids": [], "extensions": []},
        }
    }
    save_grant(learner["id"], grant_data)

    with as_user(learner["id"], username="student"):
        with pytest.raises(PermissionError, match="mastery surface"):
            assert_learning_surface("mastery")


def test_mastery_websocket_accepts_when_surface_allowed(mu_isolated_root, seed_user, monkeypatch):
    """The /ws/mastery-paths route accepts when the learner has the mastery surface."""
    from contextvars import Token as _CtxToken

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import deeptutor.api.routers.auth as auth_mod
    from deeptutor.api.routers.mastery_path import ws_router
    from deeptutor.multi_user.context import set_current_user, user_from_token_payload
    from deeptutor.services.auth import TokenPayload

    learner = _seed_learner(seed_user, "student")
    set_preset("student", "learner")
    grant_data = _policy_grant()
    grant_data["learning_policy"]["allowed_surfaces"] = ["chat", "reading", "mastery"]
    grant_data["learning_policy"]["allowed_capabilities"] = [
        "chat", "immersive_reading", "mastery_path",
    ]
    save_grant(learner["id"], grant_data)

    payload = TokenPayload(username="student", role="user", user_id=learner["id"])

    async def fake_ws_require_auth(ws):
        return set_current_user(user_from_token_payload(payload))

    monkeypatch.setattr(auth_mod, "ws_require_auth", fake_ws_require_auth)

    app = FastAPI()
    app.include_router(ws_router, prefix="/ws")
    client = TestClient(app)

    with client.websocket_connect("/ws/mastery-paths") as ws:
        ws.send_json({"type": "ping"})
        response = ws.receive_json()
        assert response["type"] == "error"
        assert "subscribe" in response["content"].lower()


def test_mastery_websocket_rejects_when_surface_missing(mu_isolated_root, seed_user, monkeypatch):
    """The /ws/mastery-paths route closes with 4403 when mastery is not allowed."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    import deeptutor.api.routers.auth as auth_mod
    from deeptutor.api.routers.mastery_path import ws_router
    from deeptutor.multi_user.context import set_current_user, user_from_token_payload
    from deeptutor.services.auth import TokenPayload

    learner = _seed_learner(seed_user, "student")
    set_preset("student", "learner")
    grant_data = {
        "learning_policy": {
            "age_band": "13-15",
            "locked_persona": "teacher",
            "allowed_capabilities": ["chat", "immersive_reading"],
            "default_capability": "chat",
            "allowed_surfaces": ["chat", "reading"],
            "reading": {"allow_upload": False, "material_ids": [], "extensions": []},
        }
    }
    save_grant(learner["id"], grant_data)

    payload = TokenPayload(username="student", role="user", user_id=learner["id"])

    async def fake_ws_require_auth(ws):
        return set_current_user(user_from_token_payload(payload))

    monkeypatch.setattr(auth_mod, "ws_require_auth", fake_ws_require_auth)

    app = FastAPI()
    app.include_router(ws_router, prefix="/ws")
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/mastery-paths"):
            pass
    assert exc_info.value.code == 4403


class _StubLearningStore:
    def list_topic_snapshots(self, status=None):
        return []


def _mastery_http_client(monkeypatch, learner_id: str):
    """Build a TestClient with the real mastery router behind require_learning_surface."""
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    import deeptutor.api.routers.auth as auth_mod
    import deeptutor.api.routers.mastery_path as mastery_mod
    from deeptutor.api.routers.auth import require_learning_surface
    from deeptutor.services.auth import TokenPayload

    payload = TokenPayload(username="student", role="user", user_id=learner_id)
    monkeypatch.setattr(auth_mod, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_mod, "decode_token", lambda token: payload if token else None)
    monkeypatch.setattr(mastery_mod, "LearningStore", _StubLearningStore)

    app = FastAPI()
    app.include_router(
        mastery_mod.router,
        prefix="/api/mastery-paths",
        dependencies=[Depends(require_learning_surface)],
    )
    return TestClient(app)


def test_http_mastery_topics_allowed_for_learner(mu_isolated_root, seed_user, monkeypatch):
    """GET /api/mastery-paths/topics returns 200 when learner has mastery surface."""
    learner = _seed_learner(seed_user, "student")
    set_preset("student", "learner")
    grant_data = _policy_grant()
    grant_data["learning_policy"]["allowed_surfaces"] = ["chat", "reading", "mastery"]
    grant_data["learning_policy"]["allowed_capabilities"] = [
        "chat", "immersive_reading", "mastery_path",
    ]
    save_grant(learner["id"], grant_data)

    client = _mastery_http_client(monkeypatch, learner["id"])
    response = client.get(
        "/api/mastery-paths/topics",
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    assert response.json() == {"topics": []}


def test_http_mastery_topics_denied_without_surface(mu_isolated_root, seed_user, monkeypatch):
    """GET /api/mastery-paths/topics returns 403 when learner lacks mastery surface."""
    learner = _seed_learner(seed_user, "student")
    set_preset("student", "learner")
    grant_data = {
        "learning_policy": {
            "age_band": "13-15",
            "locked_persona": "teacher",
            "allowed_capabilities": ["chat", "immersive_reading"],
            "default_capability": "chat",
            "allowed_surfaces": ["chat", "reading"],
            "reading": {"allow_upload": False, "material_ids": [], "extensions": []},
        }
    }
    save_grant(learner["id"], grant_data)

    client = _mastery_http_client(monkeypatch, learner["id"])
    response = client.get(
        "/api/mastery-paths/topics",
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 403
    assert "mastery surface" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Boundary: settings admin-protected endpoint rejects learner
# ---------------------------------------------------------------------------


def _settings_http_client(monkeypatch, learner_id: str):
    """TestClient with real settings router behind require_learning_surface."""
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    import deeptutor.api.routers.auth as auth_mod
    import deeptutor.api.routers.settings as settings_mod
    from deeptutor.api.routers.auth import require_learning_surface
    from deeptutor.services.auth import TokenPayload

    payload = TokenPayload(username="student", role="user", user_id=learner_id)
    monkeypatch.setattr(auth_mod, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_mod, "decode_token", lambda token: payload if token else None)

    app = FastAPI()
    app.include_router(
        settings_mod.router,
        prefix="/api/settings",
        dependencies=[Depends(require_learning_surface)],
    )
    return TestClient(app)


def test_settings_catalog_rejected_for_learner_with_surface(
    mu_isolated_root, seed_user, monkeypatch
):
    """PUT /api/settings/catalog returns 403 even when learner has settings surface."""
    learner = _seed_learner(seed_user, "student")
    grant_data = {
        "learning_policy": {
            "age_band": "13-15",
            "locked_persona": "teacher",
            "allowed_capabilities": ["chat", "immersive_reading"],
            "default_capability": "chat",
            "allowed_surfaces": ["chat", "reading", "settings"],
            "reading": {"allow_upload": False, "material_ids": [], "extensions": []},
        }
    }
    save_grant(learner["id"], grant_data)

    client = _settings_http_client(monkeypatch, learner["id"])
    response = client.put(
        "/api/settings/catalog",
        headers={"Authorization": "Bearer test-token"},
        json={"catalog": {}},
    )
    assert response.status_code == 403
    assert "administrator" in response.json()["detail"].lower()


def test_settings_ui_allowed_for_learner_with_surface(
    mu_isolated_root, seed_user, monkeypatch
):
    """PUT /api/settings/ui succeeds for learner with settings surface (user preference)."""
    learner = _seed_learner(seed_user, "student")
    grant_data = {
        "learning_policy": {
            "age_band": "13-15",
            "locked_persona": "teacher",
            "allowed_capabilities": ["chat", "immersive_reading"],
            "default_capability": "chat",
            "allowed_surfaces": ["chat", "reading", "settings"],
            "reading": {"allow_upload": False, "material_ids": [], "extensions": []},
        }
    }
    save_grant(learner["id"], grant_data)

    client = _settings_http_client(monkeypatch, learner["id"])
    response = client.put(
        "/api/settings/ui",
        headers={"Authorization": "Bearer test-token"},
        json={"theme": "snow"},
    )
    assert response.status_code == 200


def test_settings_surface_denied_blocks_all_endpoints(
    mu_isolated_root, seed_user, monkeypatch
):
    """Learner without settings surface gets 403 on /api/settings/ui."""
    learner = _seed_learner(seed_user, "student")
    grant_data = {
        "learning_policy": {
            "age_band": "13-15",
            "locked_persona": "teacher",
            "allowed_capabilities": ["chat", "immersive_reading"],
            "default_capability": "chat",
            "allowed_surfaces": ["chat", "reading"],
            "reading": {"allow_upload": False, "material_ids": [], "extensions": []},
        }
    }
    save_grant(learner["id"], grant_data)

    client = _settings_http_client(monkeypatch, learner["id"])
    response = client.put(
        "/api/settings/ui",
        headers={"Authorization": "Bearer test-token"},
        json={"theme": "snow"},
    )
    assert response.status_code == 403
    assert "settings surface" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Boundary: apply_learning_policy clears partner_id and bot_id
# ---------------------------------------------------------------------------


def test_apply_policy_preserves_assigned_partner(
    mu_isolated_root, seed_user, as_user, monkeypatch
):
    """apply_learning_policy preserves partner_id when can_use_partner approves."""
    import deeptutor.multi_user.learning_access as la_mod

    learner = _seed_learner(seed_user, "student")
    save_grant(
        learner["id"],
        {
            "partners": [{"partner_id": "partner-abc", "name": "Math Tutor", "source": "admin"}],
            "learning_policy": {
                "age_band": "13-15",
                "locked_persona": "teacher",
                "allowed_capabilities": ["chat"],
                "default_capability": "chat",
                "allowed_surfaces": ["chat", "partners"],
                "reading": {"allow_upload": False, "material_ids": [], "extensions": []},
            },
        },
    )

    import deeptutor.multi_user.partner_access as pa_mod
    monkeypatch.setattr(pa_mod, "can_use_partner", lambda pid, user=None: pid == "partner-abc")

    with as_user(learner["id"], username="student"):
        result = apply_learning_policy(
            {
                "capability": "chat",
                "partner_id": "partner-abc",
                "bot_id": "bot-xyz",
            }
        )

    assert result["partner_id"] == "partner-abc"
    assert result["bot_id"] is None


def test_apply_policy_rejects_unassigned_partner(
    mu_isolated_root, seed_user, as_user, monkeypatch
):
    """apply_learning_policy clears partner_id when can_use_partner rejects."""
    import deeptutor.multi_user.learning_access as la_mod

    learner = _seed_learner(seed_user, "student")
    save_grant(
        learner["id"],
        {
            "partners": [{"partner_id": "partner-abc", "name": "Math Tutor", "source": "admin"}],
            "learning_policy": {
                "age_band": "13-15",
                "locked_persona": "teacher",
                "allowed_capabilities": ["chat"],
                "default_capability": "chat",
                "allowed_surfaces": ["chat", "partners"],
                "reading": {"allow_upload": False, "material_ids": [], "extensions": []},
            },
        },
    )

    import deeptutor.multi_user.partner_access as pa_mod
    monkeypatch.setattr(pa_mod, "can_use_partner", lambda pid, user=None: pid == "partner-abc")

    with as_user(learner["id"], username="student"):
        result = apply_learning_policy(
            {
                "capability": "chat",
                "partner_id": "partner-HACKED",
                "bot_id": "bot-xyz",
            }
        )

    assert result["partner_id"] is None
    assert result["bot_id"] is None


def test_apply_policy_preserves_assigned_kb(mu_isolated_root, seed_user, as_user, monkeypatch):
    """apply_learning_policy preserves knowledge_bases that pass resolve_kb."""
    from fastapi import HTTPException

    import deeptutor.multi_user.learning_access as la_mod

    learner = _seed_learner(seed_user, "student")
    save_grant(
        learner["id"],
        {
            "knowledge_bases": [
                {"resource_id": "admin:kb:math", "name": "math", "access": "read", "source": "admin"},
            ],
            "learning_policy": {
                "age_band": "13-15",
                "locked_persona": "teacher",
                "allowed_capabilities": ["chat"],
                "default_capability": "chat",
                "allowed_surfaces": ["chat", "knowledge"],
                "reading": {"allow_upload": False, "material_ids": [], "extensions": []},
            },
        },
    )

    def mock_resolve_kb(ref, *, require_write=False):
        if ref == "admin:kb:math":
            return True
        raise HTTPException(status_code=403, detail="not assigned")

    import deeptutor.multi_user.knowledge_access as ka_mod
    monkeypatch.setattr(ka_mod, "resolve_kb", mock_resolve_kb)

    with as_user(learner["id"], username="student"):
        result = apply_learning_policy(
            {
                "capability": "chat",
                "knowledge_bases": ["admin:kb:math", "admin:kb:hacked", 123],
                "kb_name": "admin:kb:math",
                "enable_rag": True,
            }
        )

    assert result["knowledge_bases"] == ["admin:kb:math"]
    assert result["kb_name"] == "admin:kb:math"
    assert result["enable_rag"] is True


# ---------------------------------------------------------------------------
# Boundary: get_path_service fallback writes to isolated path
# ---------------------------------------------------------------------------


def test_settings_write_uses_isolated_path(mu_isolated_root, seed_user, as_user):
    """UI settings write goes to the user's workspace, not the shared default."""
    from deeptutor.services.path_service import PathService, get_path_service

    learner = _seed_learner(seed_user, "student")

    with as_user(learner["id"], username="student"):
        service = get_path_service()
        settings_path = service.get_settings_file("interface")
        default_path = PathService.get_instance().get_settings_file("interface")

    assert settings_path != default_path
    assert learner["id"] in str(settings_path) or "student" in str(settings_path)


_SETTINGS_WRITE_ENDPOINTS = [
    ("PUT", "/api/settings/ui", {"theme": "dark"}),
    ("POST", "/api/settings/reset", None),
    ("PUT", "/api/settings/theme", {"theme": "dark"}),
    ("PUT", "/api/settings/language", {"language": "zh"}),
    ("PUT", "/api/settings/voice-autoplay", {"voice_autoplay": True}),
    ("PUT", "/api/settings/chat-response-timeout", {"chat_response_timeout": 120}),
    ("PUT", "/api/settings/sidebar/description", {"description": "test"}),
    ("PUT", "/api/settings/sidebar/nav-order", {"nav_order": {"start": [], "learnResearch": []}}),
    ("PUT", "/api/settings/enabled-tools", {"enabled_tools": []}),
]


@pytest.mark.parametrize(
    "method,path,body",
    _SETTINGS_WRITE_ENDPOINTS,
    ids=[p for _, p, _ in _SETTINGS_WRITE_ENDPOINTS],
)
def test_settings_write_refused_when_workspace_isolation_fails(
    mu_isolated_root, seed_user, monkeypatch, method, path, body
):
    """Settings write endpoints return 503 and leave shared interface.json
    unchanged when get_current_path_service raises for a learner account.
    """
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    import deeptutor.api.routers.auth as auth_mod
    import deeptutor.api.routers.settings as settings_mod
    import deeptutor.multi_user.paths as paths_mod
    from deeptutor.api.routers.auth import require_learning_surface
    from deeptutor.services.auth import TokenPayload
    from deeptutor.services.path_service import PathService

    learner = _seed_learner(seed_user, "student")
    grant_data = {
        "learning_policy": {
            "age_band": "13-15",
            "locked_persona": "teacher",
            "allowed_capabilities": ["chat", "immersive_reading"],
            "default_capability": "chat",
            "allowed_surfaces": ["chat", "reading", "settings"],
            "reading": {"allow_upload": False, "material_ids": [], "extensions": []},
        }
    }
    save_grant(learner["id"], grant_data)

    payload = TokenPayload(username="student", role="user", user_id=learner["id"])
    monkeypatch.setattr(auth_mod, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_mod, "decode_token", lambda token: payload if token else None)

    shared_path = PathService.get_instance().get_settings_file("interface")
    shared_path.parent.mkdir(parents=True, exist_ok=True)
    original_content = b'{"theme": "snow"}'
    shared_path.write_bytes(original_content)

    def broken_path_service():
        raise RuntimeError("simulated resolution failure")

    monkeypatch.setattr(paths_mod, "get_current_path_service", broken_path_service)

    app = FastAPI()
    app.include_router(
        settings_mod.router,
        prefix="/api/settings",
        dependencies=[Depends(require_learning_surface)],
    )
    client = TestClient(app)

    kwargs = {"headers": {"Authorization": "Bearer test-token"}}
    if body is not None:
        kwargs["json"] = body
    response = getattr(client, method.lower())(path, **kwargs)

    assert response.status_code == 503, f"{method} {path} returned {response.status_code}"
    assert "isolation" in response.json()["detail"].lower()
    assert shared_path.read_bytes() == original_content


# ---------------------------------------------------------------------------
# Filesystem: migrate_learner_grant persists policy_version atomically
# ---------------------------------------------------------------------------


def test_migrate_learner_grant_persists_version(mu_isolated_root, seed_user):
    """migrate_learner_grant rewrites the JSON file with policy_version=2."""
    import json as _json

    learner = _seed_learner(seed_user, "student")
    set_preset("student", "learner")
    v1_grant = {
        "version": 2,
        "user_id": learner["id"],
        "models": {"llm": []},
        "knowledge_bases": [],
        "skills": [],
        "partners": [],
        "enabled_tools": [],
        "mcp_tools": [],
        "cli_apps": [],
        "exec_enabled": False,
        "learning_policy": {
            "age_band": "9-12",
            "locked_persona": "teacher",
            "allowed_capabilities": ["chat", "immersive_reading"],
            "default_capability": "immersive_reading",
            "allowed_surfaces": ["chat", "reading"],
            "reading": {
                "allow_upload": False,
                "material_ids": [],
                "extensions": [],
            },
        },
    }
    path = grant_path(learner["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(v1_grant, indent=2), encoding="utf-8")

    assert migrate_learner_grant(learner["id"]) is True

    on_disk = _json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["learning_policy"]["policy_version"] == 2
    assert "mastery" in on_disk["learning_policy"]["allowed_surfaces"]
    assert "mastery_path" in on_disk["learning_policy"]["allowed_capabilities"]


def test_migrate_learner_grant_skips_custom_policy(mu_isolated_root, seed_user):
    """A custom learner policy file remains byte-for-byte unchanged."""
    import json as _json

    learner = _seed_learner(seed_user, "student")
    set_preset("student", "learner")
    custom_grant = {
        "version": 2,
        "user_id": learner["id"],
        "models": {"llm": []},
        "knowledge_bases": [],
        "skills": [],
        "partners": [],
        "enabled_tools": [],
        "mcp_tools": [],
        "cli_apps": [],
        "exec_enabled": False,
        "learning_policy": {
            "age_band": "13-15",
            "locked_persona": "teacher",
            "allowed_capabilities": ["chat"],
            "default_capability": "chat",
            "allowed_surfaces": ["chat"],
            "reading": {
                "allow_upload": True,
                "material_ids": ["rm_special"],
                "extensions": ["read_aloud"],
            },
        },
    }
    path = grant_path(learner["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    original_bytes = _json.dumps(custom_grant, indent=2).encode("utf-8")
    path.write_bytes(original_bytes)

    assert migrate_learner_grant(learner["id"]) is False
    assert path.read_bytes() == original_bytes


def test_migrate_and_save_grant_serialize(mu_isolated_root, seed_user):
    """migrate_learner_grant and save_grant do not race on the same file."""
    import json as _json
    import threading

    learner = _seed_learner(seed_user, "student")
    set_preset("student", "learner")
    v1_grant = {
        "version": 2,
        "user_id": learner["id"],
        "models": {"llm": []},
        "knowledge_bases": [],
        "skills": [],
        "partners": [],
        "enabled_tools": [],
        "mcp_tools": [],
        "cli_apps": [],
        "exec_enabled": False,
        "learning_policy": {
            "age_band": "9-12",
            "locked_persona": "teacher",
            "allowed_capabilities": ["chat", "immersive_reading"],
            "default_capability": "immersive_reading",
            "allowed_surfaces": ["chat", "reading"],
            "reading": {
                "allow_upload": False,
                "material_ids": [],
                "extensions": [],
            },
        },
    }
    path = grant_path(learner["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(v1_grant, indent=2), encoding="utf-8")

    admin_update = dict(v1_grant)
    admin_update["learning_policy"] = dict(v1_grant["learning_policy"])
    admin_update["learning_policy"]["age_band"] = "13-15"

    results = {"migrate": None, "save": None, "save_error": None}
    barrier = threading.Barrier(2)

    def do_migrate():
        barrier.wait()
        results["migrate"] = migrate_learner_grant(learner["id"])

    def do_save():
        barrier.wait()
        try:
            save_grant(learner["id"], admin_update)
            results["save"] = True
        except Exception as exc:
            results["save_error"] = str(exc)

    t1 = threading.Thread(target=do_migrate)
    t2 = threading.Thread(target=do_save)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert not t1.is_alive(), "migrate thread did not terminate"
    assert not t2.is_alive(), "save thread did not terminate"
    assert results["save_error"] is None
    assert results["save"] is True

    on_disk = _json.loads(path.read_text(encoding="utf-8"))
    policy = on_disk.get("learning_policy", {})
    assert policy.get("age_band") == "13-15"


@pytest.mark.parametrize("persona", ["teacher", "peer", "research-assistant"])
def test_validate_grant_accepts_all_learning_personas(mu_isolated_root, persona):
    """validate_grant accepts all three built-in learning personas."""
    grant = normalize_grant(
        "u_test_persona",
        {
            "enabled_tools": [],
            "mcp_tools": [],
            "cli_apps": [],
            "exec_enabled": False,
            "learning_policy": {
                "age_band": "9-12",
                "locked_persona": persona,
                "allowed_capabilities": ["chat"],
                "default_capability": "chat",
                "allowed_surfaces": ["chat"],
                "reading": {"allow_upload": False, "material_ids": [], "extensions": []},
            },
        },
    )
    validate_grant(grant)


def test_validate_grant_rejects_unknown_persona(mu_isolated_root):
    """validate_grant rejects a persona not in LEARNING_PERSONAS."""
    grant = normalize_grant(
        "u_test_bad_persona",
        {
            "enabled_tools": [],
            "mcp_tools": [],
            "cli_apps": [],
            "exec_enabled": False,
            "learning_policy": {
                "age_band": "9-12",
                "locked_persona": "hacker",
                "allowed_capabilities": ["chat"],
                "default_capability": "chat",
                "allowed_surfaces": ["chat"],
                "reading": {"allow_upload": False, "material_ids": [], "extensions": []},
            },
        },
    )
    with pytest.raises(ValueError, match="locked_persona"):
        validate_grant(grant)


def test_apply_policy_uses_locked_persona_peer(mu_isolated_root, seed_user, as_user):
    """apply_learning_policy respects a non-teacher locked_persona."""
    learner = _seed_learner(seed_user, "learner_peer")
    save_grant(
        learner["id"],
        {
            "enabled_tools": [],
            "mcp_tools": [],
            "cli_apps": [],
            "exec_enabled": False,
            "learning_policy": {
                "age_band": "9-12",
                "locked_persona": "peer",
                "allowed_capabilities": ["chat"],
                "default_capability": "chat",
                "allowed_surfaces": ["chat"],
                "reading": {"allow_upload": False, "material_ids": [], "extensions": []},
            },
        },
    )

    with as_user(learner["id"], username="learner_peer"):
        result = apply_learning_policy({"capability": "chat", "persona": "teacher"})

    assert result["persona"] == "peer"


def test_resolve_persona_context_learner_uses_admin_not_user_override(
    mu_isolated_root, seed_user, as_user, monkeypatch
):
    """Learning-policy accounts must load persona from admin dir, even when a
    same-named persona exists in the user's own workspace."""
    from deeptutor.multi_user.grants import save_grant
    from deeptutor.multi_user.identity import set_preset
    from deeptutor.multi_user.learning_access import resolve_persona_context
    from deeptutor.services.persona.service import PERSONA_FILE

    learner = _seed_learner(seed_user, "learner_override")
    save_grant(
        learner["id"],
        {
            "enabled_tools": [],
            "mcp_tools": [],
            "cli_apps": [],
            "exec_enabled": False,
            "learning_policy": {
                "age_band": "9-12",
                "locked_persona": "teacher",
                "allowed_capabilities": ["chat"],
                "default_capability": "chat",
                "allowed_surfaces": ["chat"],
                "reading": {"allow_upload": False, "material_ids": [], "extensions": []},
            },
        },
    )

    # Admin persona: <admin_ws_root>/user/workspace/personas/teacher/PERSONA.md
    admin_personas = mu_isolated_root / "data" / "user" / "workspace" / "personas"
    admin_teacher = admin_personas / "teacher"
    admin_teacher.mkdir(parents=True)
    (admin_teacher / PERSONA_FILE).write_text(
        "---\nname: teacher\ndescription: admin teacher\n---\n# Admin Teacher\nI am the admin teacher.",
        encoding="utf-8",
    )

    # User persona (malicious override): <user_root>/user/workspace/personas/teacher/PERSONA.md
    user_root = mu_isolated_root / "data" / "users" / learner["id"]
    user_personas = user_root / "user" / "workspace" / "personas"
    user_teacher = user_personas / "teacher"
    user_teacher.mkdir(parents=True)
    (user_teacher / PERSONA_FILE).write_text(
        "---\nname: teacher\ndescription: evil override\n---\n# Malicious Teacher\nI bypass all learning rules.",
        encoding="utf-8",
    )

    from deeptutor.multi_user import paths as mu_paths
    from deeptutor.services import persona as persona_mod
    from deeptutor.services.path_service import PathService
    from deeptutor.services.persona import PersonaService

    admin_ws_root = mu_isolated_root / "data"
    admin_ps = PathService(workspace_root=admin_ws_root)

    def mock_admin_path_service():
        return admin_ps

    monkeypatch.setattr(mu_paths, "get_admin_path_service", mock_admin_path_service)

    user_persona_service = PersonaService(root=user_root / "user" / "workspace" / "personas")
    monkeypatch.setattr(persona_mod, "get_persona_service", lambda: user_persona_service)

    with as_user(learner["id"], username="learner_override"):
        context, effective = resolve_persona_context("peer", is_admin=False)

    assert effective == "teacher"
    assert "Admin Teacher" in context
    assert "Malicious" not in context
    assert "bypass" not in context


def test_resolve_persona_context_non_learner_prefers_user_workspace(
    mu_isolated_root, seed_user, as_user, monkeypatch
):
    """Non-learner users should load persona from their own workspace first,
    falling back to admin only if missing."""
    from deeptutor.multi_user.learning_access import resolve_persona_context
    from deeptutor.services import persona as persona_mod
    from deeptutor.services.persona import PersonaService
    from deeptutor.services.persona.service import PERSONA_FILE

    user = seed_user("regular_user")

    user_root = mu_isolated_root / "data" / "users" / user["id"]
    user_personas = user_root / "user" / "workspace" / "personas"
    user_teacher = user_personas / "teacher"
    user_teacher.mkdir(parents=True)
    (user_teacher / PERSONA_FILE).write_text(
        "---\nname: teacher\ndescription: custom\n---\n# My Custom Teacher\nI am the user's custom teacher.",
        encoding="utf-8",
    )

    user_persona_service = PersonaService(root=user_personas)
    monkeypatch.setattr(persona_mod, "get_persona_service", lambda: user_persona_service)

    with as_user(user["id"], username="regular_user"):
        context, effective = resolve_persona_context("teacher", is_admin=False)

    assert effective == "teacher"
    assert "My Custom Teacher" in context


def test_resolve_persona_context_fails_closed_when_admin_persona_missing(
    mu_isolated_root, seed_user, as_user, monkeypatch
):
    """When the admin persona directory lacks the locked persona file,
    resolve_persona_context must raise rather than silently return empty."""
    from deeptutor.multi_user.grants import save_grant
    from deeptutor.multi_user.learning_access import resolve_persona_context
    from deeptutor.services.path_service import PathService

    learner = _seed_learner(seed_user, "learner_missing")
    save_grant(
        learner["id"],
        {
            "enabled_tools": [],
            "mcp_tools": [],
            "cli_apps": [],
            "exec_enabled": False,
            "learning_policy": {
                "age_band": "9-12",
                "locked_persona": "teacher",
                "allowed_capabilities": ["chat"],
                "default_capability": "chat",
                "allowed_surfaces": ["chat"],
                "reading": {"allow_upload": False, "material_ids": [], "extensions": []},
            },
        },
    )

    from deeptutor.multi_user import paths as mu_paths

    # Admin persona directory exists but is empty — no teacher persona file
    admin_ws_root = mu_isolated_root / "data"
    admin_personas = admin_ws_root / "user" / "workspace" / "personas"
    admin_personas.mkdir(parents=True)
    admin_ps = PathService(workspace_root=admin_ws_root)

    def mock_admin_path_service():
        return admin_ps

    monkeypatch.setattr(mu_paths, "get_admin_path_service", mock_admin_path_service)

    with as_user(learner["id"], username="learner_missing"):
        with pytest.raises(RuntimeError, match="missing from the admin persona directory"):
            resolve_persona_context("anything", is_admin=False)


def test_resolve_persona_missing_error_is_not_retryable(
    mu_isolated_root, seed_user, as_user, monkeypatch
):
    """LearningPersonaMissingError must carry error_code and retryable=False
    so the executor does not retry a configuration problem."""
    from deeptutor.multi_user.grants import save_grant
    from deeptutor.multi_user.learning_access import (
        LearningPersonaMissingError,
        resolve_persona_context,
    )
    from deeptutor.services.path_service import PathService

    learner = _seed_learner(seed_user, "learner_err_attr")
    save_grant(
        learner["id"],
        {
            "enabled_tools": [],
            "mcp_tools": [],
            "cli_apps": [],
            "exec_enabled": False,
            "learning_policy": {
                "age_band": "9-12",
                "locked_persona": "teacher",
                "allowed_capabilities": ["chat"],
                "default_capability": "chat",
                "allowed_surfaces": ["chat"],
                "reading": {"allow_upload": False, "material_ids": [], "extensions": []},
            },
        },
    )

    from deeptutor.multi_user import paths as mu_paths

    admin_ws_root = mu_isolated_root / "data"
    (admin_ws_root / "user" / "workspace" / "personas").mkdir(parents=True)
    admin_ps = PathService(workspace_root=admin_ws_root)
    monkeypatch.setattr(mu_paths, "get_admin_path_service", lambda: admin_ps)

    with as_user(learner["id"], username="learner_err_attr"):
        with pytest.raises(LearningPersonaMissingError) as exc_info:
            resolve_persona_context("anything", is_admin=False)

    assert exc_info.value.error_code == "learning_persona_missing"
    assert exc_info.value.retryable is False
