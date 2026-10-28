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


# ── B3-14: dead code and hard-coded referrer kinds ──


def test_relocate_base_dir_has_no_product_callers_and_is_gone():
    assert not hasattr(registry, "relocate_base_dir")


def test_referrer_kind_constants_follow_their_owners():
    from deeptutor.book.registry import KB_REFERRER_KIND
    from deeptutor.teaching.kb import REFERRER_KIND

    assert registry.ENTITY_TRANSFERRED_REFERRER_KINDS == frozenset(
        {KB_REFERRER_KIND, REFERRER_KIND}
    )
    assert registry.CONFIRM_REFERRER_KINDS == frozenset({REFERRER_KIND})


def test_transfer_ownership_leaves_entity_transferred_references(tmp_path):
    record = registry.register_kb(
        base_dir=tmp_path, name="shared", kind="uploaded", created_by="gone", visibility="public"
    )
    for kind in ("partner", *sorted(registry.ENTITY_TRANSFERRED_REFERRER_KINDS)):
        registry.add_reference(
            record.kb_id, referrer_kind=kind, referrer_id="r1", created_by="gone"
        )
    registry.transfer_ownership("gone", "heir")
    owners = {ref.referrer_kind: ref.created_by for ref in registry.list_references(record.kb_id)}
    assert owners == {"partner": "heir", "book": "gone", "mastery_template": "gone"}


# ── B3-14: shared migration-item writer and owner normalization ──


def test_migration_items_share_one_writer():
    with registry._write() as conn:
        registry._add_migration_item(conn, "kb_dir_missing_skipped", "k1", {"n": 1})
    registry.add_migration_item("kb_dir_missing_skipped", "k1", {"n": 2})
    registry.add_migration_item("kb_made_public", "k2", {"n": 3})
    items = {(item.kind, item.key): item.data for item in registry.list_migration_items()}
    assert items == {
        ("kb_dir_missing_skipped", "k1"): {"n": 2},
        ("kb_made_public", "k2"): {"n": 3},
    }


def test_admin_root_owner_normalization_lives_in_the_registry(tmp_path, mu_isolated_root):
    from deeptutor.multi_user.paths import get_admin_path_service

    admin_root = get_admin_path_service().get_knowledge_bases_root()
    stray = registry.register_kb(
        base_dir=admin_root, name="a", kind="uploaded", created_by="admin-7"
    )
    other = registry.register_kb(
        base_dir=tmp_path / "teacher", name="b", kind="uploaded", created_by="t1"
    )
    assert registry.normalize_location_created_by(admin_root, LOCAL_ADMIN_ID) == 1
    assert registry.normalize_location_created_by(admin_root, LOCAL_ADMIN_ID) == 0
    assert registry.get_kb(stray.kb_id).created_by == LOCAL_ADMIN_ID
    assert registry.get_kb(other.kb_id).created_by == "t1"
