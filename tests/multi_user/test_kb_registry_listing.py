"""The unified KB list: registry fields, public KBs from others, lazy registration."""

from __future__ import annotations

import pytest

from deeptutor.knowledge import registry
from deeptutor.multi_user.knowledge_access import current_kb_manager, list_visible_knowledge_bases


@pytest.fixture
def list_client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from deeptutor.services import config

    monkeypatch.setattr(config, "load_config_with_main", lambda *_args: {})
    from deeptutor.api.routers import knowledge

    app = FastAPI()
    app.include_router(knowledge.router, prefix="/api")
    with TestClient(app) as client:
        yield client


def test_admin_aliases_follow_identity_after_relocation_without_namesake_leak(
    as_user, make_kb, list_client, tmp_path,
):
    from deeptutor.multi_user.knowledge_access import admin_kb_manager
    from deeptutor.knowledge.manager import KnowledgeBaseManager

    manager = admin_kb_manager()
    make_kb(manager, "shared-name")
    original = registry.register_kb(
        base_dir=manager.base_dir, name="shared-name", kind="uploaded",
        created_by="local-admin", visibility="public",
    )
    with as_user("reader"):
        rows = list_client.get("/api/knowledge-bases/list").json()
        row = next(item for item in rows if item["kb_id"] == original.kb_id)
        assert row["legacy_refs"] == ["admin:kb:shared-name"]

    # Synthetic destination only: registry relocation must retain authoritative
    # admin identity even if another public library has the same display name.
    destination = KnowledgeBaseManager(base_dir=tmp_path / "relocated")
    make_kb(destination, "shared-name")
    registry.relocate_kb(original.kb_id, destination.base_dir)
    with as_user("other", role="teacher"):
        other = current_kb_manager()
        make_kb(other, "shared-name")
        namesake = registry.register_kb(
            base_dir=other.base_dir, name="shared-name", kind="uploaded",
            created_by="other", visibility="public",
        )
    with as_user("reader"):
        rows = list_client.get("/api/knowledge-bases/list").json()
        by_id = {item["kb_id"]: item for item in rows}
        assert by_id[original.kb_id]["legacy_refs"] == ["admin:kb:shared-name"]
        assert by_id[namesake.kb_id]["legacy_refs"] == []
        detail = list_client.get(f"/api/knowledge-bases/{original.ref}")
        assert detail.status_code == 200
        assert detail.json()["legacy_refs"] == ["admin:kb:shared-name"]
        assert detail.json()["kb_id"] == original.kb_id
        legacy = list_client.get("/api/knowledge-bases/admin:kb:shared-name")
        assert legacy.json()["kb_id"] == original.kb_id
    assert registry.admin_legacy_refs(namesake.kb_id) == []


def test_private_admin_aliases_are_never_exposed_to_foreign_viewers(
    as_user, make_kb, list_client,
):
    from deeptutor.multi_user.knowledge_access import admin_kb_manager

    manager = admin_kb_manager()
    make_kb(manager, "private-admin-alias")
    record = registry.register_kb(
        base_dir=manager.base_dir, name="private-admin-alias", kind="uploaded",
        created_by="local-admin",
    )
    with as_user("foreign"):
        response = list_client.get("/api/knowledge-bases/list")
        assert response.status_code == 200
        assert all(row["kb_id"] != record.kb_id for row in response.json())
        detail = list_client.get(f"/api/knowledge-bases/{record.ref}")
        assert detail.status_code == 404
        assert "legacy_refs" not in detail.json()
        assert "private-admin-alias" not in detail.text


@pytest.mark.parametrize("route", ["/api/knowledge-bases", "/api/knowledge-bases/list"])
@pytest.mark.parametrize("origin_kind", ["account", "workspace"])
@pytest.mark.parametrize("selection_format", ["registry", "legacy", "mixed"])
def test_selected_kb_is_available_through_list_route(
    as_user, make_kb, list_client, route, origin_kind, selection_format
):
    from fastapi import HTTPException
    from deeptutor.multi_user.knowledge_access import resolve_kb
    from deeptutor.services.workspace import ContentWorkspaceService
    from deeptutor.services.workspace.context import workspace_context
    from deeptutor.services.workspace.knowledge import qualified_kb_id

    with as_user("owner"):
        service = ContentWorkspaceService()
        origin = (
            service.create_workspace("Origin")["workspace_id"]
            if origin_kind == "workspace" else ""
        )
        with workspace_context(origin):
            make_kb(current_kb_manager(), "physics")
            [access] = list_visible_knowledge_bases()
        legacy = qualified_kb_id("physics", origin)
        selection = {
            "registry": [access["ref"]],
            "legacy": [legacy],
            "mixed": [legacy, access["ref"]],
        }[selection_format]
        consumer = service.create_workspace(
            "Consumer", resources={"knowledge_bases": selection}
        )["workspace_id"]
        with workspace_context(consumer):
            # A same-name local KB must not substitute for the selected source.
            make_kb(current_kb_manager(), "physics")
            local = registry.register_kb(
                base_dir=current_kb_manager().base_dir,
                name="physics", kind="uploaded", created_by="owner",
            )
            response = list_client.get(route)
            assert response.status_code == 200
            [row] = response.json()
            assert row["id"] == legacy
            assert row["kb_id"] == access["kb_id"]
            assert row["available"] is True
            assert row["statistics"]["raw_documents"] == 1
            assert row["owned"] is True and row["read_only"] is False
            with pytest.raises(HTTPException) as denied:
                resolve_kb(local.ref)
            assert denied.value.status_code == 403


@pytest.mark.parametrize("route", ["/api/knowledge-bases", "/api/knowledge-bases/list"])
@pytest.mark.parametrize("mode", ["ordinary", "library", "workspace"])
@pytest.mark.parametrize("failure", [None, "missing", "metadata"])
@pytest.mark.parametrize("publisher_role", ["teacher", "admin"])
def test_foreign_public_list_preserves_privacy_and_access_metadata(
    as_user, make_kb, list_client, monkeypatch, route, mode, failure, publisher_role
):
    from deeptutor.services.workspace import ContentWorkspaceService
    from deeptutor.services.workspace.context import workspace_context
    from deeptutor.services.workspace.knowledge import library_request

    with as_user("publisher", role=publisher_role):
        manager = current_kb_manager()
        if failure != "missing":
            make_kb(manager, "atlas")
        record = registry.register_kb(
            base_dir=manager.base_dir, name="atlas", kind="uploaded",
            created_by="publisher", visibility="public",
        )
        make_kb(manager, "secret")
        if failure == "metadata":
            def fail_info(*args, **kwargs):
                raise ValueError("Invalid metadata")

            monkeypatch.setattr(manager, "get_info", fail_info)
    with as_user("reader"):
        workspace = (
            ContentWorkspaceService().create_workspace(
                "Narrow", resources={"knowledge_bases": []}
            )["workspace_id"] if mode == "workspace" else ""
        )
        with workspace_context(workspace):
            token = library_request.set(mode == "library")
            try:
                response = list_client.get(route)
            finally:
                library_request.reset(token)
        assert response.status_code == 200
        [row] = response.json()
        assert row["ref"] == record.ref
        assert row["path"] is None
        assert record.base_dir not in response.text
        assert row["assigned"] is True
        assert row["source"] == ("admin" if publisher_role == "admin" else "user")
        assert row["read_only"] is True and row["owned"] is False
        assert row["available"] is (failure is None)
        assert row["created_by"] == "publisher" and row["kind"] == "uploaded"
        assert row["visibility"] == "public"


@pytest.mark.parametrize("route", ["/api/knowledge-bases", "/api/knowledge-bases/list"])
@pytest.mark.parametrize("kind", ["uploaded", "obsidian"])
def test_own_metadata_error_keeps_authoritative_registry_fields(
    as_user, make_kb, list_client, monkeypatch, tmp_path, route, kind
):
    with as_user("owner"):
        manager = current_kb_manager()
        if kind == "obsidian":
            manager.register_obsidian_vault("broken", str(tmp_path))
            (manager.base_dir / "broken").mkdir(exist_ok=True)
        else:
            make_kb(manager, "broken")
        [access] = list_visible_knowledge_bases()

        def fail_info(*args, **kwargs):
            raise ValueError("Invalid metadata")

        monkeypatch.setattr(manager, "get_info", fail_info)
        response = list_client.get(route)
        assert response.status_code == 200
        [row] = response.json()
        assert row["status"] == "error"
        for key in (
            "kb_id", "ref", "visibility", "created_by", "owned", "kind",
            "connector", "assigned", "source", "read_only", "provenance_label",
        ):
            assert row[key] == access[key]


@pytest.mark.parametrize("origin_kind", ["account", "workspace"])
@pytest.mark.parametrize("selection_format", ["registry", "legacy", "mixed"])
def test_migration_blockers_and_usage_match_both_kb_identities(
    as_user, make_kb, origin_kind, selection_format
):
    from deeptutor.services.workspace import ContentWorkspaceService
    from deeptutor.services.workspace.context import workspace_context
    from deeptutor.services.workspace.knowledge import qualified_kb_id
    from deeptutor.services.workspace.resources import knowledge_migration_blockers, resource_usage
    from deeptutor.services.workspace.data_migration import preview

    with as_user("owner"):
        service = ContentWorkspaceService()
        origin = (
            service.create_workspace("Origin")["workspace_id"]
            if origin_kind == "workspace" else ""
        )
        with workspace_context(origin):
            make_kb(current_kb_manager(), "physics")
            [access] = list_visible_knowledge_bases()
        other = service.create_workspace("Unrelated")["workspace_id"]
        with workspace_context(other):
            make_kb(current_kb_manager(), "physics")
            [other_access] = list_visible_knowledge_bases()
        legacy = qualified_kb_id("physics", origin)
        selection = {
            "registry": [access["ref"]],
            "legacy": [legacy],
            "mixed": [legacy, access["ref"]],
        }[selection_format]
        consumer = service.create_workspace(
            "Consumer", resources={"knowledge_bases": selection}
        )["workspace_id"]
        assert "Consumer" in knowledge_migration_blockers(origin)[0]
        assert any(
            "Consumer" in blocker
            for blocker in preview(origin, consumer, ["knowledge_bases"])["blockers"]
        )
        assert knowledge_migration_blockers(other) == []
        legacy_usage = resource_usage("knowledge_bases", legacy)
        assert resource_usage("knowledge_bases", access["ref"]) == legacy_usage
        assert consumer in {row["workspace_id"] for row in legacy_usage}
        assert other not in {row["workspace_id"] for row in legacy_usage}
        assert consumer not in {
            row["workspace_id"]
            for row in resource_usage("knowledge_bases", other_access["ref"])
        }
        service.update_workspace(consumer, resources={"knowledge_bases": []})
        assert knowledge_migration_blockers(origin) == []
    with as_user("outsider"):
        ContentWorkspaceService().create_workspace("Foreign")
        assert resource_usage("knowledge_bases", access["ref"]) == []
        assert knowledge_migration_blockers(origin) == []


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
