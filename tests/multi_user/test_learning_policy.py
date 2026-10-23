"""Server-side policy resolution for learning accounts."""

from __future__ import annotations

import pytest

from deeptutor.knowledge.kb_types import CONNECTED_KB_TYPES
from deeptutor.multi_user.grants import normalize_grant, save_grant, validate_grant
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


def test_policy_saved_before_mastery_path_default_still_allows_it(
    mu_isolated_root, seed_user, as_user
):
    learner = _seed_learner(seed_user, "student")
    # Stored the way accounts created before the default changed were.
    save_grant(learner["id"], _policy_grant())

    policy = learning_policy_for_user(learner["id"])
    assert policy is not None
    assert policy["allowed_capabilities"] == ["chat", "immersive_reading", "mastery_path"]

    with as_user(learner["id"], username="student"):
        result = apply_learning_policy({"capability": "mastery_path", "tools": ["web_search"]})

    assert result["capability"] == "mastery_path"
    assert result["tools"] == []
    assert result["persona"] == "teacher"


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
        with pytest.raises(PermissionError, match="books surface"):
            assert_learning_surface("books")
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
    assert policy["allowed_surfaces"] == ["chat", "reading"]
    assert policy["reading"] == {
        "allow_upload": False,
        "material_ids": [],
        "extensions": [],
    }


def test_apply_policy_keeps_public_and_own_kbs(
    mu_isolated_root, seed_user, as_user, make_kb
):
    from deeptutor.knowledge import registry
    from deeptutor.multi_user.knowledge_access import admin_kb_manager, current_kb_manager

    learner = _seed_learner(seed_user, "student")
    save_grant(learner["id"], _policy_grant())
    manager = admin_kb_manager()
    records = {}
    for name in ("atlas", "answers", "course-only"):
        make_kb(manager, name)
        records[name] = registry.register_kb(
            base_dir=manager.base_dir, name=name, kind="uploaded",
            created_by="local-admin", visibility="public" if name == "atlas" else "private",
        )
    registry.add_reference(
        records["course-only"].kb_id,
        referrer_kind="mastery_template", referrer_id="tpl1", created_by="t1",
    )
    registry.register_reference_reader("mastery_template", lambda _ref, _user: True)

    with as_user(learner["id"], username="student"):
        manager = current_kb_manager()
        make_kb(manager, "mine")
        manager.register_obsidian_vault("vault", str(mu_isolated_root))
        vault = registry.resolve_ref("user:kb:vault")
        mine = registry.resolve_ref("user:kb:mine")
        assert vault is not None and mine is not None
        result = apply_learning_policy({
            "capability": "chat",
            "knowledge_bases": [
                "admin:kb:atlas", "admin:kb:answers", records["answers"].ref,
                records["course-only"].ref, "admin:kb:course-only",
                "user:kb:mine", mine.ref, "user:kb:vault", vault.ref,
                "admin:kb:atlas", records["atlas"].ref, records["atlas"].ref,
                None, {}, 42, "", "kb:missing", "user:kb:missing", "kb:\x00",
            ],
        })

    assert result["knowledge_bases"] == [
        "admin:kb:atlas", "user:kb:mine", mine.ref, records["atlas"].ref,
    ]
    assert result["kb_name"] == ""
    assert result["enable_rag"] is False


@pytest.mark.parametrize("refs", [None, "", "kb:one", {"kb:one": True}, 42])
def test_chat_kb_filter_rejects_malformed_collections(mu_isolated_root, refs):
    from deeptutor.multi_user.knowledge_access import chat_readable_kb_refs

    assert chat_readable_kb_refs(refs) == []


@pytest.mark.parametrize("selection_format", ["registry", "legacy", "empty"])
def test_chat_kb_filter_preserves_private_workspace_allowlist(
    mu_isolated_root, seed_user, as_user, make_kb, selection_format
):
    from deeptutor.knowledge import registry
    from deeptutor.multi_user.knowledge_access import current_kb_manager
    from deeptutor.services.workspace import ContentWorkspaceService
    from deeptutor.services.workspace.context import workspace_context
    from deeptutor.services.workspace.knowledge import qualified_kb_id

    learner = _seed_learner(seed_user, "student")
    save_grant(learner["id"], _policy_grant())
    with as_user(learner["id"], username="student"):
        manager = current_kb_manager()
        records = {}
        for name in ("allowed", "blocked", "public"):
            make_kb(manager, name)
            records[name] = registry.register_kb(
                base_dir=manager.base_dir, name=name, kind="uploaded",
                created_by=learner["id"],
                visibility="public" if name == "public" else "private",
            )
        legacy = qualified_kb_id("allowed", "")
        selected = records["allowed"].ref if selection_format == "registry" else legacy
        workspace = ContentWorkspaceService().create_workspace(
            "Restricted",
            resources={"knowledge_bases": [] if selection_format == "empty" else [selected]},
        )["workspace_id"]
        missing = registry.register_kb(
            base_dir=manager.base_dir, name="gone", kind="uploaded",
            created_by="teacher", visibility="public",
        )
        with workspace_context(workspace):
            result = apply_learning_policy({
                "capability": "chat",
                "knowledge_bases": [
                    records["blocked"].ref, qualified_kb_id("blocked", ""),
                    records["public"].ref, records["allowed"].ref, legacy, missing.ref,
                ],
            })
        expected = [records["public"].ref]
        if selection_format != "empty":
            expected.append(records["allowed"].ref)
        # The existing resolver accepts qualified legacy refs only when that
        # form is selected; registry IDs also accept a legacy workspace selection.
        if selection_format == "legacy":
            expected.append(legacy)
        assert result["knowledge_bases"] == expected


@pytest.mark.parametrize("kind", sorted(CONNECTED_KB_TYPES))
def test_chat_kb_filter_excludes_every_personal_connector(
    mu_isolated_root, as_user, make_kb, kind
):
    from deeptutor.knowledge import registry
    from deeptutor.multi_user.knowledge_access import chat_readable_kb_refs, current_kb_manager

    with as_user("owner"):
        manager = current_kb_manager()
        make_kb(manager, "connector", entry={"type": kind})
        record = registry.register_kb(
            base_dir=manager.base_dir, name="connector", kind=kind, created_by="owner",
        )
        assert chat_readable_kb_refs([record.ref, "user:kb:connector"]) == []
