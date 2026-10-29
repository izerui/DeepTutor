"""Synthetic-only regressions for generic configuration and R2 classification."""

import json
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from deeptutor.knowledge import registry
from deeptutor.knowledge.kb_types import CONNECTED_KB_TYPES, CONNECTOR_CONFIG_FIELDS
from deeptutor.multi_user.knowledge_access import (
    chat_readable_kb_refs,
    current_kb_manager,
    list_visible_knowledge_bases,
)
from deeptutor.services.config.knowledge_base_config import KnowledgeBaseConfigService


def _seed(as_user, make_kb, *, kind="uploaded", visibility="public"):
    with as_user("owner", role="admin"):
        manager = current_kb_manager()
        make_kb(manager, "atlas", entry=None if kind == "uploaded" else {
            "path": "atlas", "type": kind, "status": "ready",
            "server_url": "https://synthetic.example.invalid",
            "api_key": "synthetic-marker",
        })
        record = registry.register_kb(
            base_dir=manager.base_dir, name="atlas", kind=kind,
            created_by="owner", visibility=visibility,
        )
    return record


def _replace_entry(record: registry.KbRecord, entry: dict) -> None:
    path = Path(record.base_dir) / "kb_config.json"
    payload = json.loads(path.read_text())
    payload["knowledge_bases"][record.dir_name] = entry
    path.write_text(json.dumps(payload))


@pytest.mark.asyncio
@pytest.mark.parametrize("ref_kind", ["stable", "legacy", "bare"])
@pytest.mark.parametrize("provider", [None, "llamaindex", "lightrag-server"])
@pytest.mark.parametrize("ready", [False, True])
async def test_route_rejects_conversion_without_persistence(
    as_user, make_kb, monkeypatch, ref_kind, provider, ready,
):
    from deeptutor.api.routers import knowledge
    from deeptutor.services.rag import index_probe

    record = _seed(as_user, make_kb)
    path = Path(record.base_dir) / "kb_config.json"
    before = path.read_bytes()
    monkeypatch.setattr(index_probe, "has_ready_provider_index", lambda *_args: ready)
    update = {
        "type": "lightrag_server", "server_url": "https://synthetic.example.invalid",
        "api_key": "synthetic-marker",
    }
    if provider is not None:
        update["rag_provider"] = provider
    ref = {"stable": record.ref, "legacy": "admin:kb:atlas", "bare": "atlas"}[ref_kind]
    with as_user("owner", role="admin"):
        with pytest.raises(HTTPException) as rejected:
            await knowledge.update_kb_config(ref, update)
        assert rejected.value.status_code == 409
    assert path.read_bytes() == before
    assert registry.get_kb(record.ref) == record


@pytest.mark.parametrize("field", sorted(CONNECTOR_CONFIG_FIELDS | {"type", "path", "rag_provider"}))
def test_direct_write_rejects_identity_and_pointer_additions(tmp_path, field):
    service = KnowledgeBaseConfigService(tmp_path / "kb_config.json")
    service.set_search_mode("atlas", "hybrid")
    before = service.config_path.read_bytes()
    value = "ima" if field == "rag_provider" else "synthetic-marker"
    with pytest.raises(ValueError):
        service.set_kb_config("atlas", {field: value})
    assert service.config_path.read_bytes() == before
    assert service.get_kb_config("atlas").get(field) != value
    with pytest.raises(ValueError):
        service.set_kb_config("new", {field: value})
    assert "new" not in service.get_all_configs()["knowledge_bases"]
    assert service.config_path.read_bytes() == before


def test_direct_provider_and_metadata_sync_cannot_install_remote_provider(tmp_path):
    service = KnowledgeBaseConfigService(tmp_path / "kb_config.json")
    service.set_search_mode("atlas", "hybrid")
    before = service.config_path.read_bytes()
    with pytest.raises(ValueError):
        service.set_rag_provider("atlas", "weknora")
    folder = tmp_path / "atlas"
    folder.mkdir()
    (folder / "metadata.json").write_text(json.dumps({"rag_provider": "ima"}))
    with pytest.raises(ValueError):
        service.sync_from_metadata("atlas", tmp_path)
    assert service.config_path.read_bytes() == before


@pytest.mark.asyncio
async def test_legacy_roundtrip_preserves_knobs_and_does_not_delete_fields(as_user, make_kb):
    from deeptutor.api.routers import knowledge

    record = _seed(as_user, make_kb)
    _replace_entry(record, {
        "path": "atlas", "type": "legacy-indexed", "rag_provider": "llamaindex",
        "api_key": "", "server_url": None, "embedding_model": "synthetic-model",
    })
    with as_user("owner", role="admin"):
        current = (await knowledge.get_kb_config(record.ref))["config"]
        result = await knowledge.update_kb_config(
            record.ref, {**current, "search_mode": "naive", "description": "Updated"},
        )
        assert result["config"]["search_mode"] == "naive"
        assert result["config"]["api_key"] == ""
        assert result["config"]["embedding_model"] == "synthetic-model"
        before = (Path(record.base_dir) / "kb_config.json").read_bytes()
        with pytest.raises(HTTPException) as rejected:
            await knowledge.update_kb_config(record.ref, {"embedding_model": "other"})
        assert rejected.value.status_code == 409
        assert (Path(record.base_dir) / "kb_config.json").read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", sorted(CONNECTED_KB_TYPES))
async def test_private_connector_maintenance_keeps_classification(as_user, make_kb, kind):
    from deeptutor.api.routers import knowledge

    record = _seed(as_user, make_kb, kind=kind, visibility="private")
    with as_user("owner", role="admin"):
        current = (await knowledge.get_kb_config(record.ref))["config"]
        result = await knowledge.update_kb_config(record.ref, {
            **current, "api_key": "synthetic-rotated", "search_mode": "naive",
        })
        assert result["config"]["api_key"] == "synthetic-rotated"
        assert registry.classification_is_consistent(record)
        assert registry.is_connector(record)
        with pytest.raises(HTTPException):
            await knowledge.update_kb_config(record.ref, {"type": "uploaded"})


@pytest.mark.parametrize("entry", [
    {"type": kind, "api_key": "synthetic-marker"}
    for kind in sorted(CONNECTED_KB_TYPES)
] + [
    {"server_url": "https://synthetic.example.invalid"},
    {"rag_provider": "ima"}, {"rag_provider": " IMA "},
])
def test_stale_uploaded_kind_closes_direct_permissions_and_selection(
    as_user, make_user, make_kb, entry,
):
    record = _seed(as_user, make_kb)
    registry.add_reference(
        record.ref, referrer_kind="course", referrer_id="old", created_by="owner",
    )
    registry.register_reference_reader("course", lambda *_args: True)
    _replace_entry(record, {"path": "atlas", **entry})
    assert registry.is_connector(record)
    assert not registry.classification_is_consistent(record)
    assert not registry.can_read(record, make_user("stranger"))
    assert registry.can_read(record, make_user("owner"))
    # spec 2026-10-29 D2: an admin does not bypass another account's private KB.
    assert not registry.can_read(record, make_user("admin", role="admin"))
    for kind in ("course", "mastery_template", "book", "partner"):
        with pytest.raises(registry.KbReferenceError):
            registry.add_reference(
                record.ref, referrer_kind=kind, referrer_id="new", created_by="owner",
            )
    with pytest.raises(registry.KbVisibilityError):
        registry.set_visibility(record.ref, "public")
    for uid in ("owner", "stranger"):
        with as_user(uid, role="admin" if uid == "owner" else "user"):
            assert chat_readable_kb_refs([record.ref, "admin:kb:atlas", "atlas"]) == []
    with as_user("stranger"):
        assert not any(row.get("kb_id") == record.kb_id for row in list_visible_knowledge_bases())
    private = registry.set_visibility(record.ref, "private")
    assert not registry.can_read(private, make_user("stranger"))


@pytest.mark.parametrize("kind", ["uploaded", "lightrag_server"])
@pytest.mark.parametrize("payload", [
    "[]", "null", "{", '{"knowledge_bases":[]}', '{"knowledge_bases":null}',
    '{"knowledge_bases":{"atlas":[]}}',
    '{"knowledge_bases":{"atlas":{"type":[]}}}',
    '{"knowledge_bases":{"atlas":{"rag_provider":[]}}}',
])
def test_malformed_catalog_fails_closed_without_raising(
    as_user, make_user, make_kb, kind, payload,
):
    record = _seed(as_user, make_kb, kind=kind, visibility="private")
    (Path(record.base_dir) / "kb_config.json").write_text(payload)
    assert registry.is_connector(record)
    assert not registry.classification_is_consistent(record)
    assert not registry.can_read(record, make_user("stranger"))
    assert registry.can_read(record, make_user("owner"))


def test_registry_connector_never_downgraded_by_missing_or_uploaded_config(
    as_user, make_kb,
):
    record = _seed(as_user, make_kb, kind="lightrag_server", visibility="private")
    path = Path(record.base_dir) / "kb_config.json"
    path.unlink()
    assert registry.is_connector(record)
    with pytest.raises(registry.KbVisibilityError):
        registry.set_visibility(record.ref, "public")
    path.write_text(json.dumps({"knowledge_bases": {"atlas": {"path": "atlas"}}}))
    assert registry.is_connector(record)
    assert not registry.classification_is_consistent(record)


@pytest.mark.parametrize("learner", [False, True])
def test_http_mismatch_hides_config_and_detail_with_actual_learner_surface(
    as_user, make_kb, seed_user, learner,
):
    from deeptutor.api.routers import auth, knowledge
    from deeptutor.multi_user.grants import save_grant
    from deeptutor.multi_user.learning_access import apply_learning_policy

    record = _seed(as_user, make_kb)
    seed_user("admin", role="admin")
    reader = seed_user("reader")
    if learner:
        save_grant(reader["id"], {"learning_policy": {
            "age_band": "9-12", "locked_persona": "teacher",
            "allowed_surfaces": ["chat", "reading"],
            "allowed_capabilities": ["chat"], "default_capability": "chat",
        }})
    app = FastAPI()
    app.dependency_overrides[auth.require_auth] = lambda: None
    app.include_router(
        knowledge.router, prefix="/api", dependencies=[Depends(auth.require_learning_surface)],
    )
    _replace_entry(record, {
        "path": "atlas", "type": "lightrag_server", "rag_provider": "llamaindex",
        "server_url": "https://synthetic.example.invalid", "api_key": "synthetic-marker",
    })
    with as_user(reader["id"], username="reader"), TestClient(app) as client:
        for ref in (record.ref, "admin:kb:atlas", "atlas"):
            detail = client.get(f"/api/knowledge-bases/{ref}")
            config = client.get(f"/api/knowledge-bases/{ref}/config")
            assert detail.status_code in (403, 404)
            if learner:
                assert config.status_code == 403
            else:
                assert config.status_code in (403, 404)
            assert "synthetic" not in detail.text + config.text
        if learner:
            assert apply_learning_policy({
                "capability": "chat", "knowledge_bases": [record.ref, "admin:kb:atlas"],
            })["knowledge_bases"] == []
    with as_user("owner", role="admin"), TestClient(app) as client:
        assert client.get(f"/api/knowledge-bases/{record.ref}/config").json()["config"]["api_key"] == "synthetic-marker"
        assert client.get(f"/api/knowledge-bases/{record.ref}").status_code == 200
        assert client.put(f"/api/knowledge-bases/{record.ref}/config", json={"description": "Diagnosed"}).status_code == 200
        assert client.put(f"/api/knowledge-bases/{record.ref}/config", json={"api_key": "changed"}).status_code == 409
