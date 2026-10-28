"""B3-14 (knowledge part) registry cleanups and B3 ledger minors."""

from __future__ import annotations

import json

import pytest

from deeptutor.knowledge import registry, registry_backfill
from deeptutor.multi_user.models import LOCAL_ADMIN_ID

# ── ledger: kind_of with an unhashable type ──


@pytest.mark.parametrize("kind", [["x"], {"a": 1}, 3, None])
def test_kind_of_tolerates_non_string_types(kind):
    assert registry.kind_of({"type": kind}) == registry.UPLOADED_KIND


def test_kind_of_still_recognizes_connectors():
    kind = sorted(registry.CONNECTOR_KINDS)[0]
    assert registry.kind_of({"type": kind}) == kind


# ── ledger: KB move journal must carry the kb_id key ──


def _journal_row(**overrides) -> dict:
    row = {
        "id": "0" * 32,
        "plan": {"name": "atlas", "source_workspace_id": "ws_a", "target_workspace_id": "ws_b"},
        "source_before": {"knowledge_bases": {"atlas": {"path": "atlas"}}},
        "target_before": {"knowledge_bases": {}},
        "aliases_before": {},
        "assignment_before": {},
        "source_config_existed": True,
        "target_config_existed": False,
        "aliases_existed": False,
        "publish_pending": False,
        "kb_id": None,
    }
    row.update(overrides)
    return row


def test_kb_move_journal_without_kb_id_key_is_invalid():
    from deeptutor.services.workspace.kb_move import _validate_kb_move_journal

    _validate_kb_move_journal(_journal_row())  # None is a valid value
    _validate_kb_move_journal(_journal_row(kb_id="a" * 32))
    row = _journal_row()
    del row["kb_id"]
    with pytest.raises(ValueError):
        _validate_kb_move_journal(row)


# ── ledger: grant census role comparison ──


def test_grant_census_skips_only_normalized_admins(mu_isolated_root, monkeypatch):
    """``normalize_role`` keeps only exact roles: "Admin" is no admin, before or after."""
    from deeptutor.multi_user import grants
    from deeptutor.multi_user.paths import get_admin_path_service

    admin_root = get_admin_path_service().get_knowledge_bases_root()
    admin_root.mkdir(parents=True, exist_ok=True)
    registry_backfill._begin_inventory(admin_root)
    monkeypatch.setattr(
        grants, "load_grant", lambda _uid: {"knowledge_bases": [{"name": "physics"}]}
    )
    registry_backfill._freeze_grants([
        {"id": "a1", "username": "boss", "role": "admin"},
        {"id": "a2", "username": "Cap", "role": "Admin"},
        {"id": "u1", "username": "ann", "role": "student"},
        {"id": "u2", "username": "bob"},
    ])
    snapshot = json.loads(registry.get_meta(registry_backfill._INVENTORY_KEY))
    assert snapshot["grants"] == {"physics": ["Cap", "ann", "bob"]}
