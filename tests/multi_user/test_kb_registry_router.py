"""KB registration, guarded deletion, visibility and scoped configuration routes."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import threading

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from deeptutor.knowledge import registry
from deeptutor.multi_user.knowledge_access import current_kb_manager


@pytest.fixture
def knowledge():
    from deeptutor.api.routers import knowledge as module

    return module


async def _create(knowledge, monkeypatch, make_kb, name="physics", visibility="private", **kwargs):
    async def fake_create(*_args):
        make_kb(current_kb_manager(), name)
        return {"name": name}

    monkeypatch.setattr(knowledge, "_create_knowledge_base_owned", fake_create)
    return await knowledge.create_knowledge_base(
        BackgroundTasks(), name=name, files=[], rag_provider="llamaindex",
        pageindex_mode="", search_mode="", rel_paths=[], indexing_llm="",
        embedding_model="", storage_workspace_id=kwargs.get("storage_workspace_id"),
        visibility=visibility,
    )


@pytest.mark.asyncio
async def test_create_registers_and_replaces_stale_record(knowledge, as_user, make_kb, monkeypatch):
    with as_user("t1", role="teacher"):
        stale = registry.register_kb(
            base_dir=current_kb_manager().base_dir, name="physics", kind="uploaded",
            created_by="old",
        )
        registry.add_reference(
            stale.kb_id, referrer_kind="partner", referrer_id="old", created_by="old"
        )
        result = await _create(knowledge, monkeypatch, make_kb, visibility="public")
        private = await _create(knowledge, monkeypatch, make_kb, name="drafts")
    record = registry.get_kb(result["kb_id"])
    assert record.ref == result["ref"]
    assert record.created_by == "t1" and record.visibility == "public"
    assert registry.get_kb(private["kb_id"]).visibility == "private"
    assert registry.get_kb(stale.kb_id) is None
    assert registry.list_unavailable_references("partner", "old")[0].reason == "kb_deleted"


@pytest.mark.asyncio
@pytest.mark.parametrize(("visibility", "status"), [("public", 403), ("shared", 400)])
async def test_create_rejects_before_creating(
    knowledge, as_user, make_kb, monkeypatch, visibility, status
):
    with as_user("u1"):
        with pytest.raises(HTTPException) as error:
            await _create(knowledge, monkeypatch, make_kb, visibility=visibility)
        assert error.value.status_code == status
        assert "physics" not in current_kb_manager().list_knowledge_bases()
        assert registry.list_records() == []


@pytest.mark.asyncio
async def test_create_registers_in_destination_workspace(knowledge, as_user, make_kb, monkeypatch):
    from deeptutor.services.workspace import ContentWorkspaceService
    from deeptutor.services.workspace.context import workspace_context

    with as_user("t1", role="teacher"):
        home = current_kb_manager().base_dir
        wid = ContentWorkspaceService().create_workspace("Storage")["workspace_id"]
        result = await _create(
            knowledge, monkeypatch, make_kb, storage_workspace_id=wid
        )
        with workspace_context(wid):
            destination = current_kb_manager().base_dir
    record = registry.get_kb(result["kb_id"])
    assert Path(record.base_dir) == Path(destination)
    assert Path(record.base_dir) != Path(home)
    assert record.created_by == "t1"


@pytest.mark.asyncio
async def test_referenced_delete_requires_admin_force(knowledge, as_user, make_kb, monkeypatch):
    with as_user("t1", role="teacher"):
        result = await _create(knowledge, monkeypatch, make_kb, visibility="public")
        registry.add_reference(
            result["kb_id"], referrer_kind="partner", referrer_id="ada", created_by="t2"
        )
        with pytest.raises(HTTPException) as error:
            knowledge._delete_kb(result["ref"])
        assert error.value.status_code == 409 and "partner:ada" in error.value.detail
        with pytest.raises(HTTPException) as error:
            knowledge._delete_kb(result["ref"], force=True)
        assert error.value.status_code == 403
        assert "physics" in current_kb_manager().list_knowledge_bases()
    with as_user("root", role="admin"):
        knowledge._delete_kb(result["ref"], force=True)
    assert registry.get_kb(result["kb_id"]) is None
    [gone] = registry.list_unavailable_references("partner", "ada")
    assert gone.reason == "kb_deleted" and gone.kb_name == "physics"


@pytest.mark.asyncio
@pytest.mark.parametrize("registered", [True, False])
async def test_force_requires_admin_even_without_references(
    knowledge, as_user, make_kb, monkeypatch, registered
):
    with as_user("t1", role="teacher"):
        if registered:
            await _create(knowledge, monkeypatch, make_kb)
        else:
            make_kb(current_kb_manager(), "physics")
        with pytest.raises(HTTPException) as error:
            knowledge._delete_kb("physics", force=True)
        assert error.value.status_code == 403
        assert "physics" in current_kb_manager().list_knowledge_bases()


@pytest.mark.asyncio
@pytest.mark.parametrize("body_route", [True, False])
async def test_delete_routes_unregister(knowledge, as_user, make_kb, monkeypatch, body_route):
    with as_user("t1", role="teacher"):
        result = await _create(knowledge, monkeypatch, make_kb)
        if body_route:
            await knowledge.delete_knowledge_base_by_name(
                knowledge.DeleteKnowledgeBaseRequest(name=result["ref"])
            )
        else:
            await knowledge.delete_knowledge_base(result["ref"])
    assert registry.get_kb(result["kb_id"]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, RuntimeError("delete failed")])
async def test_failed_delete_keeps_registration_and_references(
    knowledge, as_user, make_kb, monkeypatch, failure
):
    with as_user("root", role="admin"):
        result = await _create(knowledge, monkeypatch, make_kb)
        registry.add_reference(
            result["kb_id"], referrer_kind="partner", referrer_id="ada", created_by="root"
        )

        def fail(*_args, **_kwargs):
            if isinstance(failure, Exception):
                raise failure
            return failure

        monkeypatch.setattr(current_kb_manager(), "delete_knowledge_base", fail)
        with pytest.raises(HTTPException) as error:
            knowledge._delete_kb(result["ref"], force=True)
        assert error.value.status_code == (500 if isinstance(failure, Exception) else 400)
    assert registry.get_kb(result["kb_id"]) is not None
    assert len(registry.list_references(result["kb_id"])) == 1
    assert registry.list_unavailable_references("partner", "ada") == []


@pytest.mark.asyncio
async def test_delete_serializes_concurrent_reference_writers(
    knowledge, as_user, make_kb, monkeypatch
):
    with as_user("t1", role="teacher"):
        result = await _create(knowledge, monkeypatch, make_kb)
        manager = current_kb_manager()
        original = manager.delete_knowledge_base
        started = threading.Event()

        def add_reference():
            started.set()
            registry.add_reference(
                result["kb_id"], referrer_kind="partner", referrer_id="racing", created_by="t1"
            )

        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = []

            def delete(*args, **kwargs):
                # A separate SQLite connection proves the DB lock, not only a
                # process-local mutex, covers physical deletion.
                with sqlite3.connect(registry.DB_PATH_OVERRIDE, timeout=0) as conn:
                    with pytest.raises(sqlite3.OperationalError, match="locked"):
                        conn.execute("BEGIN IMMEDIATE")
                pending.append(executor.submit(add_reference))
                assert started.wait(2)
                assert not pending[0].done()
                return original(*args, **kwargs)

            monkeypatch.setattr(manager, "delete_knowledge_base", delete)
            knowledge._delete_kb(result["ref"])
            with pytest.raises(registry.KbReferenceError):
                pending[0].result(timeout=5)
    assert registry.list_references(result["kb_id"]) == []


@pytest.mark.asyncio
async def test_visibility_and_reference_permissions(knowledge, as_user, make_kb, monkeypatch):
    with as_user("t1", role="teacher"):
        result = await _create(knowledge, monkeypatch, make_kb)
        body = await knowledge.set_kb_visibility(
            "physics", knowledge.KbVisibilityRequest(visibility="public")
        )
        assert body == {key: result[key] for key in ("kb_id", "ref")} | {"visibility": "public"}
        registry.add_reference(
            result["kb_id"], referrer_kind="partner", referrer_id="ada", created_by="t2"
        )
        refs = await knowledge.list_kb_references("physics")
        assert refs["kb_id"] == result["kb_id"] and refs["ref"] == result["ref"]
        assert refs["references"] == [r.to_dict() for r in registry.list_references(result["kb_id"])]
        with pytest.raises(HTTPException) as error:
            await knowledge.set_kb_visibility(
                "physics", knowledge.KbVisibilityRequest(visibility="private")
            )
        assert error.value.status_code == 409
        with pytest.raises(HTTPException) as error:
            await knowledge.set_kb_visibility(
                "physics", knowledge.KbVisibilityRequest(visibility="private", force=True)
            )
        assert error.value.status_code == 403
    with as_user("s1", role="student"):
        for operation in (
            knowledge.list_kb_references(result["ref"]),
            knowledge.set_kb_visibility(result["ref"], knowledge.KbVisibilityRequest(visibility="private")),
        ):
            with pytest.raises(HTTPException) as error:
                await operation
            assert error.value.status_code == 403
    with as_user("root", role="admin"):
        await knowledge.set_kb_visibility(
            result["ref"], knowledge.KbVisibilityRequest(visibility="private", force=True)
        )
    assert registry.list_unavailable_references("partner", "ada")[0].reason == "made_private"


@pytest.mark.asyncio
async def test_owner_references_allow_private_but_still_block_delete(
    knowledge, as_user, make_kb, monkeypatch
):
    with as_user("t1", role="teacher"):
        result = await _create(knowledge, monkeypatch, make_kb, visibility="public")
        registry.add_reference(
            result["kb_id"], referrer_kind="partner", referrer_id="own", created_by="t1"
        )
        await knowledge.set_kb_visibility(
            result["ref"], knowledge.KbVisibilityRequest(visibility="private")
        )
        with pytest.raises(HTTPException) as error:
            knowledge._delete_kb(result["ref"])
        assert error.value.status_code == 409
        assert len(registry.list_references(result["kb_id"])) == 1


@pytest.mark.asyncio
async def test_plain_users_and_connectors_cannot_publish(knowledge, as_user, make_kb, monkeypatch):
    with as_user("u1"):
        await _create(knowledge, monkeypatch, make_kb)
        with pytest.raises(HTTPException) as error:
            await knowledge.set_kb_visibility("physics", knowledge.KbVisibilityRequest(visibility="public"))
        assert error.value.status_code == 403
    with as_user("t1", role="teacher"):
        make_kb(current_kb_manager(), "vault", {"type": "obsidian"})
        with pytest.raises(HTTPException) as error:
            await knowledge.set_kb_visibility("vault", knowledge.KbVisibilityRequest(visibility="public"))
        assert error.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("admin_library", [True, False])
async def test_legacy_read_only_resources_never_become_callers_property(
    knowledge, as_user, make_kb, monkeypatch, admin_library
):
    from deeptutor.multi_user import knowledge_access
    from deeptutor.multi_user.models import LOCAL_ADMIN_ID, KnowledgeResource

    with as_user("owner", role="admin" if admin_library else "teacher"):
        make_kb(current_kb_manager(), "legacy")
        base_dir = current_kb_manager().base_dir
    resource = KnowledgeResource(
        id="legacy", name="legacy", base_dir=base_dir,
        source="admin" if admin_library else "user", assigned=True, read_only=True,
    )
    # An upstream legacy reader has granted only read access, as in the
    # registry's ownership regression tests. It must never confer ownership.
    monkeypatch.setattr(knowledge, "resolve_kb", lambda *_args, **_kwargs: resource)
    monkeypatch.setattr(knowledge_access, "resolve_kb", lambda *_args, **_kwargs: resource)
    with as_user("t1", role="teacher"):
        with pytest.raises(HTTPException) as error:
            await knowledge.set_kb_visibility(
                "legacy", knowledge.KbVisibilityRequest(visibility="public")
            )
        assert error.value.status_code == 403
    record = registry.find_by_location(base_dir, "legacy")
    if admin_library:
        assert record.created_by == LOCAL_ADMIN_ID
    else:
        assert record is None


@pytest.mark.asyncio
async def test_config_refs_preserve_permissions_and_target_catalog(
    knowledge, as_user, make_kb, monkeypatch
):
    with as_user("t1", role="teacher"):
        result = await _create(knowledge, monkeypatch, make_kb, visibility="public")
        await knowledge.update_kb_config(result["ref"], {"search_mode": "naive"})
        with pytest.raises(HTTPException) as error:
            await knowledge.update_kb_config(result["ref"], {"embedding_model": "replacement"})
        assert error.value.status_code == 409
    with as_user("s1", role="student"):
        make_kb(current_kb_manager(), "physics")
        await knowledge.update_kb_config("physics", {"search_mode": "local"})
        assert (await knowledge.get_kb_config(result["ref"]))["config"]["search_mode"] == "naive"
        assert (await knowledge.get_kb_config("physics"))["config"]["search_mode"] == "local"
        with pytest.raises(HTTPException) as error:
            await knowledge.update_kb_config(result["ref"], {"search_mode": "global"})
        assert error.value.status_code == 403
    with as_user("t1", role="teacher"):
        await knowledge.set_kb_visibility("physics", knowledge.KbVisibilityRequest(visibility="private"))
    with as_user("s1", role="student"):
        with pytest.raises(HTTPException) as error:
            await knowledge.get_kb_config(result["ref"])
        assert error.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("reference_kind", ["registry", "legacy", "bare"])
async def test_config_cannot_bypass_workspace_selection(
    knowledge, as_user, make_kb, monkeypatch, reference_kind
):
    from deeptutor.services.workspace import ContentWorkspaceService
    from deeptutor.services.workspace.context import workspace_context

    with as_user("t1", role="teacher"):
        result = await _create(knowledge, monkeypatch, make_kb)
        ref = {"registry": result["ref"], "legacy": "account:kb:physics", "bare": "physics"}[reference_kind]
        wid = ContentWorkspaceService().create_workspace(
            "Empty", resources={"knowledge_bases": []}
        )["workspace_id"]
        with workspace_context(wid):
            for operation in (
                knowledge.get_kb_config(ref),
                knowledge.update_kb_config(ref, {"search_mode": "naive"}),
            ):
                with pytest.raises(HTTPException) as error:
                    await operation
                assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_config_provider_guard_uses_referenced_location(
    knowledge, as_user, make_kb, monkeypatch
):
    from deeptutor.services.rag import index_probe

    with as_user("t1", role="teacher"):
        result = await _create(knowledge, monkeypatch, make_kb)
        expected = Path(registry.get_kb(result["kb_id"]).base_dir) / "physics"
    inspected = []

    def ready_index(kb_dir, provider):
        inspected.append((kb_dir, provider))
        return kb_dir == expected

    monkeypatch.setattr(index_probe, "has_ready_provider_index", ready_index)
    with as_user("root", role="admin"):
        with pytest.raises(HTTPException) as error:
            await knowledge.update_kb_config(result["ref"], {"rag_provider": "pageindex"})
        assert error.value.status_code == 409
        assert "ready llamaindex index" in error.value.detail
        assert inspected and set(inspected) == {(expected, "llamaindex")}
        assert (await knowledge.get_kb_config(result["ref"]))["config"]["rag_provider"] == "llamaindex"


@pytest.mark.asyncio
async def test_unknown_references_keep_404(knowledge, as_user):
    with as_user("t1", role="teacher"):
        for operation in (
            knowledge.get_kb_config("kb:missing"),
            knowledge.update_kb_config("kb:missing", {"search_mode": "naive"}),
            knowledge.delete_knowledge_base("kb:missing"),
            knowledge.list_kb_references("kb:missing"),
            knowledge.set_kb_visibility("kb:missing", knowledge.KbVisibilityRequest(visibility="public")),
        ):
            with pytest.raises(HTTPException) as error:
                await operation
            assert error.value.status_code == 404


def test_http_validation_and_force_forwarding(knowledge, as_user, monkeypatch):
    app = FastAPI()
    app.include_router(knowledge.router, prefix="/api")
    calls = []

    def delete(name, *, force=False):
        calls.append((name, force))
        return {"message": "deleted"}

    monkeypatch.setattr(knowledge, "_delete_kb", delete)
    with as_user("root", role="admin"), TestClient(app) as client:
        assert client.delete("/api/knowledge-bases/kb:abc?force=true").status_code == 200
        assert client.post("/api/knowledge-bases/delete", json={"name": "a/b", "force": True}).status_code == 200
        assert client.put("/api/knowledge-bases/a/visibility", json={"visibility": "shared"}).status_code == 422
    assert calls == [("kb:abc", True), ("a/b", True)]
