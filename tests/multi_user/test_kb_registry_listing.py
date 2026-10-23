"""The unified KB list: registry fields, public KBs from others, lazy registration."""

from __future__ import annotations

import pytest

from deeptutor.knowledge import registry
from deeptutor.multi_user.knowledge_access import current_kb_manager, list_visible_knowledge_bases


def test_list_annotates_own_kbs_and_adds_public_ones(as_user, make_kb):
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        make_kb(manager, "physics")
        make_kb(manager, "drafts")
        rows = {row["name"]: row for row in list_visible_knowledge_bases()}
        physics = registry.find_by_location(manager.base_dir, "physics")
        assert rows["physics"]["kb_id"] == physics.kb_id
        assert rows["physics"]["ref"] == physics.ref
        assert rows["physics"]["owned"] is True
        assert rows["physics"]["visibility"] == "private"
        assert rows["physics"]["created_by"] == "t1"
        registry.set_visibility(physics.kb_id, "public")
        # My own public KB is not listed twice.
        assert [row["name"] for row in list_visible_knowledge_bases()].count("physics") == 1

    with as_user("s1", role="student"):
        rows = list_visible_knowledge_bases()
        assert [(row["id"], row["owned"], row["read_only"], row["assigned"]) for row in rows] == [
            (physics.ref, False, True, True)
        ]
        assert rows[0]["provenance_label"] == "Public knowledge base"
        assert rows[0]["visibility"] == "public"


def test_public_kbs_ignore_workspace_selection(as_user, make_kb):
    from deeptutor.services.workspace import ContentWorkspaceService
    from deeptutor.services.workspace.context import workspace_context

    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        make_kb(manager, "atlas")
        record = registry.register_kb(
            base_dir=manager.base_dir, name="atlas", kind="uploaded", created_by="t1", visibility="public"
        )
    with as_user("s1", role="student"):
        narrow = ContentWorkspaceService().create_workspace(
            "Narrow", resources={"knowledge_bases": []}
        )["workspace_id"]
        with workspace_context(narrow):
            assert [row["id"] for row in list_visible_knowledge_bases()] == [record.ref]


def test_workspace_selection_accepts_kb_refs(as_user, make_kb):
    from deeptutor.services.workspace import ContentWorkspaceService
    from deeptutor.services.workspace.context import workspace_context

    with as_user("t1", role="teacher"):
        make_kb(current_kb_manager(), "physics")
        [row] = list_visible_knowledge_bases()
        narrow = ContentWorkspaceService().create_workspace(
            "Narrow", resources={"knowledge_bases": [row["ref"]]}
        )["workspace_id"]
        with workspace_context(narrow):
            assert [item["kb_id"] for item in list_visible_knowledge_bases()] == [row["kb_id"]]


def test_lazy_registration_records_connectors_with_their_kind(as_user, tmp_path):
    with as_user("u1"):
        manager = current_kb_manager()
        manager.register_obsidian_vault("vault", str(tmp_path))
        rows = {row["name"]: row for row in list_visible_knowledge_bases()}
        assert rows["vault"]["kind"] == "obsidian"
        assert rows["vault"]["connector"] is True
        assert registry.find_by_location(manager.base_dir, "vault").created_by == "u1"


def test_grants_no_longer_list_admin_kbs(as_user, seed_user):
    from deeptutor.multi_user.grants import save_grant
    from deeptutor.multi_user.knowledge_access import admin_kb_base_dir

    seed_user("boss", role="admin")
    alice = seed_user("alice")
    save_grant(alice["id"], {"knowledge_bases": [{"resource_id": "admin:kb:secret"}]})
    registry.register_kb(
        base_dir=admin_kb_base_dir(), name="secret", kind="uploaded", created_by="local-admin"
    )
    with as_user(alice["id"], username="alice"):
        assert list_visible_knowledge_bases() == []


def test_router_list_returns_registry_fields_and_public_kbs(as_user, make_kb, monkeypatch):
    from deeptutor.services import config

    monkeypatch.setattr(config, "load_config_with_main", lambda *_args: {})
    from deeptutor.api.routers import knowledge

    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        make_kb(manager, "physics")
        rows = knowledge._list_knowledge_bases()
        own = next(row for row in rows if row.name == "physics")
        assert own.kb_id and own.owned is True and own.visibility == "private"
        registry.set_visibility(own.kb_id, "public")

    with as_user("s1", role="student"):
        rows = knowledge._list_knowledge_bases()
        assert [(row.id, row.assigned, row.read_only, row.owned) for row in rows] == [
            (f"kb:{own.kb_id}", True, True, False)
        ]
        assert rows[0].created_by == "t1"


@pytest.mark.parametrize("library", [True, False])
def test_router_resource_list_carries_registry_fields(as_user, make_kb, monkeypatch, library):
    from deeptutor.services import config

    monkeypatch.setattr(config, "load_config_with_main", lambda *_args: {})
    from deeptutor.api.routers import knowledge
    from deeptutor.services.workspace.knowledge import library_request

    with as_user("t1", role="teacher"):
        make_kb(current_kb_manager(), "physics")
        token = library_request.set(library)
        try:
            rows = knowledge._resource_knowledge_bases()
        finally:
            library_request.reset(token)
        assert rows[0].kb_id and rows[0].visibility == "private" and rows[0].owned is True
