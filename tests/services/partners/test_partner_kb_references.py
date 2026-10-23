"""Partners reference KBs without copying or deleting independent resources."""

from pathlib import Path

import pytest

from deeptutor.knowledge import registry
from deeptutor.multi_user.knowledge_access import resolve_kb
from deeptutor.multi_user.models import LOCAL_ADMIN_ID, CurrentUser
from deeptutor.multi_user.paths import scope_for_user, user_context
from deeptutor.partners.bus.queue import MessageBus
from deeptutor.services.partners.manager import PartnerConfig, PartnerManager
from deeptutor.services.partners.runtime import PartnerRunner
from deeptutor.services.partners.scope import partner_user
from deeptutor.services.partners.workspace import list_assets, provision_assets, remove_asset
from tests.services.partners.test_partner_workspace import (
    _seed_admin_connected_kb,
    _seed_admin_kb,
)


def test_partner_turn_reads_referenced_kbs_by_ref(partners_root):
    _seed_admin_kb(partners_root.parent)
    assert provision_assets("ada", knowledge_bases=["physics"])["errors"] == []
    [record] = registry.list_referenced("partner", "ada")
    runner = PartnerRunner("ada", PartnerConfig(name="Ada"), MessageBus())
    with user_context(partner_user("ada", name="Ada")):
        assert runner._list_kb_names() == [record.ref]
        resource = resolve_kb(record.ref)
        assert resource.read_only is True
        assert resource.base_dir == Path(record.base_dir)
    with user_context(partner_user("other")):
        assert not registry.can_read(record, partner_user("other"))


@pytest.mark.parametrize("selector", ["name", "ref", "kb_id"])
def test_assets_list_and_remove_references(partners_root, selector):
    source = _seed_admin_kb(partners_root.parent)
    provision_assets("ada", knowledge_bases=["physics"])
    [record] = registry.list_referenced("partner", "ada")
    assert list_assets("ada")["knowledge_bases"] == [
        {"name": "physics", "ref": record.ref, "documents": 1}
    ]
    assert remove_asset("ada", "knowledge_base", getattr(record, selector))
    assert registry.list_referenced("partner", "ada") == []
    assert (source / "raw" / "doc.pdf").read_bytes() == b"%PDF-fake"


@pytest.mark.parametrize("selector", ["name", "ref", "kb_id"])
def test_removing_adopted_reference_preserves_private_kb(partners_root, selector):
    root = partners_root / "ada" / "workspace"
    source = _seed_admin_kb(root)
    record = registry.ensure_registered(
        root / "knowledge_bases", "physics", created_by=LOCAL_ADMIN_ID
    )
    registry.add_reference(
        record.kb_id, referrer_kind="partner", referrer_id="ada", created_by=LOCAL_ADMIN_ID
    )
    assert list_assets("ada")["knowledge_bases"][0]["adopted"] is True
    assert remove_asset("ada", "knowledge_base", getattr(record, selector))
    assert not remove_asset("ada", "knowledge_base", getattr(record, selector))
    assert (source / "raw" / "doc.pdf").exists()
    assert registry.get_kb(record.ref) == record
    assert list_assets("ada")["knowledge_bases"] == []
    with user_context(partner_user("ada")):
        assert PartnerRunner("ada", PartnerConfig(name="Ada"), MessageBus())._list_kb_names() == []


def test_same_name_references_and_legacy_copy_are_distinct(partners_root):
    records = []
    for root in [partners_root.parent, partners_root.parent / "users" / "alice"]:
        _seed_admin_kb(root)
        records.append(
            registry.ensure_registered(
                root / "knowledge_bases", "physics", created_by=LOCAL_ADMIN_ID
            )
        )
    legacy = _seed_admin_kb(partners_root / "ada" / "workspace")
    assert provision_assets("ada", knowledge_bases=[r.ref for r in records])["errors"] == []
    items = list_assets("ada")["knowledge_bases"]
    assert len(items) == 3
    assert {i["ref"] for i in items if "ref" in i} == {r.ref for r in records}
    assert [i for i in items if i.get("legacy")] == [
        {"name": "physics", "documents": 1, "legacy": True}
    ]
    with pytest.raises(ValueError, match="ambiguous"):
        remove_asset("ada", "knowledge_base", "physics")
    assert remove_asset("ada", "knowledge_base", records[0].ref)
    assert (legacy / "raw" / "doc.pdf").exists()
    assert registry.list_referenced("partner", "ada") == [records[1]]


def test_unregistered_legacy_kb_removal(partners_root):
    source = _seed_admin_kb(partners_root / "ada" / "workspace")
    assert list_assets("ada")["knowledge_bases"] == [
        {"name": "physics", "documents": 1, "legacy": True}
    ]
    assert remove_asset("ada", "knowledge_base", "physics")
    assert not source.exists()
    assert list_assets("ada")["knowledge_bases"] == []


def test_runtime_mixes_stable_refs_and_retrievable_legacy_kbs(partners_root):
    from deeptutor.knowledge.manager import KnowledgeBaseManager

    _seed_admin_connected_kb(partners_root.parent)
    provision_assets("ada", knowledge_bases=["wiki"])
    [record] = registry.list_referenced("partner", "ada")
    root = partners_root / "ada" / "workspace"
    _seed_admin_kb(root, name="legacy")
    KnowledgeBaseManager(base_dir=str(root / "knowledge_bases")).register_connected_entry(
        "exclusive", {"type": "obsidian", "vault_path": str(root / "vault")}
    )
    with user_context(partner_user("ada")):
        runner = PartnerRunner("ada", PartnerConfig(name="Ada"), MessageBus())
        assert runner._list_kb_names() == [record.ref, "legacy"]
    registry.unregister_kb(record.kb_id)
    with user_context(partner_user("ada")):
        assert runner._list_kb_names() == ["legacy"]


def test_runtime_skips_missing_source(partners_root):
    _seed_admin_connected_kb(partners_root.parent)
    provision_assets("ada", knowledge_bases=["wiki"])
    (partners_root.parent / "knowledge_bases" / "kb_config.json").unlink()
    with user_context(partner_user("ada")):
        runner = PartnerRunner("ada", PartnerConfig(name="Ada"), MessageBus())
        assert runner._list_kb_names() == []


def test_legacy_connector_removal(partners_root):
    root = partners_root / "ada" / "workspace"
    _seed_admin_connected_kb(root)
    assert list_assets("ada")["knowledge_bases"] == [
        {"name": "wiki", "type": "weknora", "documents": 0, "legacy": True}
    ]
    assert remove_asset("ada", "knowledge_base", "wiki")
    assert list_assets("ada")["knowledge_bases"] == []


@pytest.mark.parametrize("partner_owner,allowed", [(LOCAL_ADMIN_ID, True), ("alice", False)])
def test_connectors_require_same_creator_even_for_admin(
    partners_root, monkeypatch, partner_owner, allowed
):
    from deeptutor.multi_user import partner_access

    _seed_admin_connected_kb(partners_root.parent)
    monkeypatch.setattr(partner_access, "partner_owner_id", lambda _: partner_owner)
    report = provision_assets("ada", knowledge_bases=["wiki"])
    assert bool(report["errors"]) is not allowed
    assert bool(registry.list_referenced("partner", "ada")) is allowed
    assert not (partners_root / "ada" / "workspace" / "knowledge_bases" / "kb_config.json").exists()


def test_nonowner_cannot_add_reference(partners_root, monkeypatch):
    from deeptutor.multi_user import partner_access

    _seed_admin_kb(partners_root.parent)
    record = registry.ensure_registered(
        partners_root.parent / "knowledge_bases", "physics", created_by=LOCAL_ADMIN_ID
    )
    registry.set_visibility(record.kb_id, "public")
    monkeypatch.setattr(partner_access, "partner_owner_id", lambda _: "bob")
    actor = CurrentUser("alice", "Alice", "user", scope_for_user("alice", is_admin=False))
    with user_context(actor):
        report = provision_assets("ada", knowledge_bases=[record.ref])
    assert report["errors"]
    assert registry.list_referenced("partner", "ada") == []


def test_legacy_readable_resource_does_not_transfer_ownership(partners_root, monkeypatch):
    from deeptutor.multi_user import knowledge_access, partner_access
    from deeptutor.multi_user.models import KnowledgeResource

    source = _seed_admin_kb(partners_root.parent)
    monkeypatch.setattr(partner_access, "partner_owner_id", lambda _: "alice")
    monkeypatch.setattr(
        knowledge_access,
        "resolve_kb",
        lambda _: KnowledgeResource(
            id="admin:kb:physics", name="physics", base_dir=source.parent,
            source="admin", assigned=True, read_only=True,
        ),
    )
    actor = CurrentUser("alice", "Alice", "user", scope_for_user("alice", is_admin=False))
    with user_context(actor):
        assert provision_assets("ada", knowledge_bases=["admin:kb:physics"])["errors"] == []
    [record] = registry.list_referenced("partner", "ada")
    assert record.created_by == LOCAL_ADMIN_ID
    assert registry.list_references(record.kb_id)[0].created_by == "alice"


def test_unregistered_foreign_readonly_resource_is_not_claimed(partners_root, monkeypatch):
    from deeptutor.multi_user import knowledge_access, partner_access
    from deeptutor.multi_user.models import KnowledgeResource

    source = _seed_admin_kb(partners_root.parent / "users" / "bob")
    monkeypatch.setattr(partner_access, "partner_owner_id", lambda _: "alice")
    monkeypatch.setattr(
        knowledge_access,
        "resolve_kb",
        lambda _: KnowledgeResource(
            id="foreign", name="physics", base_dir=source.parent,
            source="user", assigned=True, read_only=True,
        ),
    )
    actor = CurrentUser("alice", "Alice", "user", scope_for_user("alice", is_admin=False))
    with user_context(actor):
        report = provision_assets("ada", knowledge_bases=["foreign"])
    assert report["errors"]
    assert registry.find_by_location(source.parent, "physics") is None
    assert registry.list_referenced("partner", "ada") == []


def test_creator_can_reference_own_private_connector(partners_root, monkeypatch):
    from deeptutor.multi_user import partner_access

    actor = CurrentUser("alice", "Alice", "user", scope_for_user("alice", is_admin=False))
    _seed_admin_connected_kb(actor.scope.root)
    monkeypatch.setattr(partner_access, "partner_owner_id", lambda _: actor.id)
    with user_context(actor):
        assert provision_assets("ada", knowledge_bases=["wiki"])["errors"] == []
    [record] = registry.list_referenced("partner", "ada")
    assert record.created_by == actor.id
    with user_context(partner_user("ada")):
        assert resolve_kb(record.ref).read_only


@pytest.mark.asyncio
async def test_destroying_partner_drops_references_not_source(partners_root):
    source = _seed_admin_kb(partners_root.parent)
    manager = PartnerManager()
    manager.save_config("ada", PartnerConfig(name="Ada"), auto_start=False)
    provision_assets("ada", knowledge_bases=["physics"])
    [record] = registry.list_referenced("partner", "ada")
    assert await manager.destroy_partner("ada")
    assert registry.list_referenced("partner", "ada") == []
    assert registry.get_kb(record.ref) == record
    assert (source / "raw" / "doc.pdf").exists()


@pytest.mark.asyncio
async def test_destroy_preserves_adopted_kb_and_other_references(partners_root):
    manager = PartnerManager()
    manager.save_config("ada", PartnerConfig(name="Ada"), auto_start=False)
    root = partners_root / "ada" / "workspace"
    source = _seed_admin_kb(root)
    record = registry.ensure_registered(source.parent, "physics", created_by=LOCAL_ADMIN_ID)
    for partner in ["ada", "other"]:
        registry.add_reference(
            record.kb_id, referrer_kind="partner", referrer_id=partner, created_by=LOCAL_ADMIN_ID
        )
    assert await manager.destroy_partner("ada")
    assert manager.load_config("ada") is None
    assert registry.list_referenced("partner", "ada") == []
    assert registry.list_referenced("partner", "other") == [record]
    assert (source / "raw" / "doc.pdf").exists()
    with user_context(partner_user("other")):
        assert resolve_kb(record.ref).read_only
    # Reusing the partner ID must not implicitly reattach the independent KB.
    manager.save_config("ada", PartnerConfig(name="New Ada"), auto_start=False)
    assert list_assets("ada")["knowledge_bases"] == []
    with user_context(partner_user("ada")):
        runner = PartnerRunner("ada", PartnerConfig(name="New Ada"), MessageBus())
        assert runner._list_kb_names() == []
    assert await manager.destroy_partner("ada")
    assert registry.get_kb(record.ref) == record
    assert (source / "raw" / "doc.pdf").exists()


@pytest.mark.asyncio
async def test_destroy_unregisters_partner_owned_local_kb(partners_root):
    manager = PartnerManager()
    manager.save_config("ada", PartnerConfig(name="Ada"), auto_start=False)
    source = _seed_admin_kb(partners_root / "ada" / "workspace")
    record = registry.ensure_registered(source.parent, "physics", created_by="partner_ada")
    registry.add_reference(
        record.kb_id, referrer_kind="partner", referrer_id="ada", created_by=LOCAL_ADMIN_ID
    )
    assert await manager.destroy_partner("ada")
    assert registry.get_kb(record.ref) is None
    assert registry.list_referenced("partner", "ada") == []
    assert not source.exists()


@pytest.mark.asyncio
async def test_destroy_mixed_local_kbs_does_not_revive_removed_records(partners_root):
    manager = PartnerManager()
    manager.save_config("ada", PartnerConfig(name="Ada"), auto_start=False)
    root = partners_root / "ada" / "workspace"
    adopted = _seed_admin_kb(root, name="adopted")
    owned = _seed_admin_kb(root, name="owned")
    legacy = _seed_admin_kb(root, name="legacy")
    retained = registry.ensure_registered(adopted.parent, "adopted", created_by=LOCAL_ADMIN_ID)
    removed = registry.ensure_registered(owned.parent, "owned", created_by="partner_ada")
    assert await manager.destroy_partner("ada")
    assert registry.get_kb(retained.ref) == retained
    assert registry.get_kb(removed.ref) is None
    assert (adopted / "raw" / "doc.pdf").exists()
    assert not owned.exists()
    assert not legacy.exists()
    assert resolve_kb(retained.ref).base_dir == adopted.parent
    manager.save_config("ada", PartnerConfig(name="New Ada"), auto_start=False)
    assert list_assets("ada")["knowledge_bases"] == []


@pytest.mark.asyncio
async def test_destroy_missing_partner_still_clears_references(partners_root):
    _seed_admin_kb(partners_root.parent)
    record = registry.ensure_registered(
        partners_root.parent / "knowledge_bases", "physics", created_by=LOCAL_ADMIN_ID
    )
    registry.add_reference(
        record.kb_id, referrer_kind="partner", referrer_id="missing", created_by=LOCAL_ADMIN_ID
    )
    assert await PartnerManager().destroy_partner("missing") is False
    assert registry.list_referenced("partner", "missing") == []
    assert registry.get_kb(record.ref) == record


@pytest.mark.asyncio
async def test_destroy_fails_closed_on_registry_error(partners_root, monkeypatch):
    manager = PartnerManager()
    manager.save_config("ada", PartnerConfig(name="Ada"), auto_start=False)
    source = _seed_admin_kb(partners_root / "ada" / "workspace")

    def fail(*args):
        raise OSError("registry unavailable")

    monkeypatch.setattr(registry, "remove_references", fail)
    with pytest.raises(OSError, match="registry unavailable"):
        await manager.destroy_partner("ada")
    assert (source / "raw" / "doc.pdf").exists()
    assert manager.load_config("ada") is not None
