"""Exercise the shipped schema with an in-memory client, never a PB service."""

from copy import deepcopy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


class _Collections:
    def __init__(self, records):
        self.records = records
        self.created = []
        self.updated = []

    def get_full_list(self):
        return list(self.records.values())

    def create(self, schema):
        self.created.append(deepcopy(schema))
        self.records[schema["name"]] = SimpleNamespace(
            id=schema["name"], **deepcopy(schema),
        )

    def update(self, record_id, payload):
        self.updated.append((record_id, deepcopy(payload)))
        for key, value in deepcopy(payload).items():
            setattr(self.records[record_id], key, value)


@pytest.mark.parametrize("existing", [False, True])
def test_setup_supplies_optional_kb_identity_additively_and_idempotently(monkeypatch, existing):
    from deeptutor.services import config

    monkeypatch.setattr(config, "load_integrations_settings", lambda: {
        "pocketbase_url": "https://unused.invalid",
        "pocketbase_admin_email": "fake",
        "pocketbase_admin_password": "fake",
    })
    spec = importlib.util.spec_from_file_location(
        "isolated_pb_setup", Path(__file__).resolve().parents[1] / "scripts" / "pb_setup.py",
    )
    module = importlib.util.module_from_spec(spec)
    # The script adds its directory to sys.path when imported.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    spec.loader.exec_module(module)
    legacy_fields = [
        {"name": "kb_name", "type": "text", "required": True},
        {"name": "custom_field", "type": "text", "required": False},
    ]
    legacy_rows = [
        {"id": "old-a", "kb_name": "physics", "user_id": "alice"},
        {"id": "old-b", "kb_name": "physics", "user_id": "bob"},
    ]
    rows_before = deepcopy(legacy_rows)
    records = {}
    if existing:
        records["knowledge_bases"] = SimpleNamespace(
            id="knowledge_bases", name="knowledge_bases",
            schema=deepcopy(legacy_fields), indexes=["legacy_index"],
        )
    collections = _Collections(records)

    def collection(name):
        # Setup must never inspect or rewrite legacy knowledge-base rows.
        assert name == "turns"
        return SimpleNamespace(get_full_list=lambda: [])

    pb = SimpleNamespace(collections=collections, collection=collection)
    monkeypatch.setattr(module, "_get_client", lambda: pb)
    module.main()
    fields = records["knowledge_bases"].schema
    assert [field for field in fields if field["name"] == "kb_id"] == [
        {"name": "kb_id", "type": "text", "required": False},
    ]
    if existing:
        assert fields[:len(legacy_fields)] == legacy_fields
        assert records["knowledge_bases"].indexes == ["legacy_index"]
        assert [name for name, _ in collections.updated] == ["knowledge_bases"]
    else:
        assert not collections.updated
    schema_before = deepcopy(vars(records["knowledge_bases"]))
    operations_before = deepcopy((collections.created, collections.updated))
    module.main()
    assert vars(records["knowledge_bases"]) == schema_before
    assert (collections.created, collections.updated) == operations_before
    assert legacy_rows == rows_before
