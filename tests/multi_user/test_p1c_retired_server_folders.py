"""P1c: KBs that point at a server folder (Obsidian vaults, linked indexes) are retired."""

from __future__ import annotations

import json

import pytest

from deeptutor.knowledge.kb_types import (
    RETIRED_SERVER_FOLDER_MESSAGE,
    is_retired_server_folder_kb,
)
from deeptutor.knowledge.manager import KnowledgeBaseManager


def _manager(tmp_path, entries):
    base = tmp_path / "kbs"
    base.mkdir()
    (base / "kb_config.json").write_text(
        json.dumps({"knowledge_bases": entries}), encoding="utf-8"
    )
    return KnowledgeBaseManager(base_dir=str(base))


def test_obsidian_and_linked_are_retired_and_others_are_not():
    assert is_retired_server_folder_kb({"type": "obsidian", "vault_path": "/x"})
    assert is_retired_server_folder_kb({"type": "linked", "external_path": "/x"})
    for entry in ({}, {"type": "ima"}, {"type": "marginnote4"}, {"type": "kiwix"}, None):
        assert not is_retired_server_folder_kb(entry)


def test_a_retired_kb_never_resolves_its_server_folder(tmp_path):
    outside = tmp_path / "someone-else"
    outside.mkdir()
    manager = _manager(tmp_path, {
        "vault": {"type": "obsidian", "vault_path": str(outside)},
        "mounted": {"type": "linked", "external_path": str(outside), "rag_provider": "llamaindex"},
    })
    for name in ("vault", "mounted"):
        with pytest.raises(ValueError, match="server folder"):
            manager.get_knowledge_base_path(name)


def test_a_retired_kb_is_listed_as_an_error_without_scanning_the_folder(tmp_path):
    outside = tmp_path / "someone-else"
    outside.mkdir()
    (outside / "secret.md").write_text("private", encoding="utf-8")
    manager = _manager(tmp_path, {
        "mounted": {"type": "linked", "external_path": str(outside), "rag_provider": "llamaindex"},
    })
    info = manager.get_info("mounted")
    assert info["status"] == "error"
    assert info["progress"]["message"] == RETIRED_SERVER_FOLDER_MESSAGE


def test_metadata_for_capabilities_drops_the_folder_pointer(as_user, make_kb):
    from deeptutor.knowledge import registry
    from deeptutor.multi_user.knowledge_access import current_kb_manager, resolve_kb_metadata

    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        folder = make_kb(manager, "vault")
        config_path = manager.base_dir / "kb_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["knowledge_bases"]["vault"].update({"type": "obsidian", "vault_path": str(folder)})
        config_path.write_text(json.dumps(config), encoding="utf-8")
        record = registry.ensure_registered(manager.base_dir, "vault", created_by="t1")
        meta = resolve_kb_metadata(record.ref)
    assert meta is not None and meta.get("type") == "obsidian"
    assert not meta.get("vault_path")
    assert not meta.get("external_path")
