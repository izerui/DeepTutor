"""Partners reference KBs without copying or deleting independent resources."""

from pathlib import Path

import pytest
from fastapi import HTTPException

from deeptutor.knowledge import registry
from deeptutor.knowledge.manager import KnowledgeBaseManager
from deeptutor.multi_user.knowledge_access import resolve_kb
from deeptutor.multi_user.models import LOCAL_ADMIN_ID, CurrentUser
from deeptutor.multi_user.paths import scope_for_user, user_context
from deeptutor.partners.bus.queue import MessageBus
from deeptutor.services.partners.manager import PartnerConfig, PartnerManager
from deeptutor.services.partners.runtime import PartnerRunner
from deeptutor.services.partners.scope import partner_user
from deeptutor.services.partners.workspace import list_assets, provision_assets, remove_asset
from deeptutor.services.partners.workspace_binding import partner_content_context
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


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["referenced", "detached", "recreated"])
@pytest.mark.parametrize("alias", ["physics", "user:kb:physics", "default", "user:kb:default"])
@pytest.mark.parametrize("require_write", [False, True])
async def test_adopted_legacy_alias_uses_registry_authority(
    partners_root, stage, alias, require_write
):
    manager = PartnerManager()
    config = PartnerConfig(name="Ada", owner_id="alice")
    other_config = PartnerConfig(name="Other", owner_id="alice")
    manager.save_config("ada", config, auto_start=False)
    manager.save_config("other", other_config, auto_start=False)
    source = _seed_admin_kb(partners_root / "ada" / "workspace")
    with partner_content_context("ada", config):
        KnowledgeBaseManager(base_dir=str(source.parent)).set_default("physics")
    record = registry.ensure_registered(source.parent, "physics", created_by="alice")
    for partner_id in ["ada", "other"]:
        registry.add_reference(
            record.kb_id, referrer_kind="partner", referrer_id=partner_id, created_by="alice"
        )
    original_files = {
        path.relative_to(source.parent): path.read_bytes()
        for path in source.parent.rglob("*") if path.is_file()
    }
    if stage == "detached":
        assert remove_asset("ada", "knowledge_base", record.ref)
    elif stage == "recreated":
        assert await manager.destroy_partner("ada")
        assert manager.load_config("ada") is None
        config = PartnerConfig(name="Replacement", owner_id="bob")
        manager.save_config("ada", config, auto_start=False)

    with partner_content_context("ada", config):
        if stage == "referenced" and not require_write:
            resource = resolve_kb(alias)
            assert resource.read_only is True
            assert resource.assigned is True
            assert resource.base_dir == source.parent
            assert resource.name == record.dir_name
        else:
            with pytest.raises(HTTPException) as denied:
                resolve_kb(alias, require_write=require_write)
            assert denied.value.status_code == (403 if stage == "referenced" else 404)
        if stage != "referenced":
            assert PartnerRunner("ada", config, MessageBus())._list_kb_names() == []

    owner = CurrentUser("alice", "Alice", "user", scope_for_user("alice", is_admin=False))
    with user_context(owner):
        assert resolve_kb(record.ref).read_only is False
        assert resolve_kb(record.ref, require_write=True).base_dir == source.parent
    with partner_content_context("other", other_config):
        assert resolve_kb(record.ref).read_only is True
        with pytest.raises(HTTPException) as denied:
            resolve_kb(record.ref, require_write=True)
        assert denied.value.status_code == 403
    assert registry.get_kb(record.ref) == record
    assert registry.list_referenced("partner", "other") == [record]
    assert registry.list_referenced("partner", "ada") == (
        [record] if stage == "referenced" else []
    )
    assert {
        path.relative_to(source.parent): path.read_bytes()
        for path in source.parent.rglob("*") if path.is_file()
    } == original_files


@pytest.mark.parametrize("alias", ["physics", "user:kb:physics", "default", "user:kb:default"])
@pytest.mark.parametrize("require_write", [False, True])
def test_unregistered_legacy_alias_remains_available(partners_root, alias, require_write):
    source = _seed_admin_kb(partners_root / "ada" / "workspace")
    with partner_content_context("ada", PartnerConfig(name="Ada")):
        KnowledgeBaseManager(base_dir=str(source.parent)).set_default("physics")
        resource = resolve_kb(alias, require_write=require_write)
        assert resource.base_dir == source.parent
        assert resource.name == "physics"
        assert resource.read_only is False
        assert resource.assigned is False
    assert registry.find_by_location(source.parent, "physics") is None
    assert (source / "raw" / "doc.pdf").read_bytes() == b"%PDF-fake"


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
        assert provision_assets("ada", knowledge_bases=["admin:kb:physics"])["errors"]
    record = registry.find_by_location(source.parent, "physics")
    assert record.created_by == LOCAL_ADMIN_ID
    assert registry.list_referenced("partner", "ada") == []


@pytest.mark.parametrize("scope_kind", ["book", "mastery_template"])
def test_scoped_private_read_cannot_be_delegated(partners_root, monkeypatch, scope_kind):
    from deeptutor.multi_user import partner_access

    manager = PartnerManager()
    manager.save_config("ada", PartnerConfig(name="Ada", owner_id="alice"), auto_start=False)
    monkeypatch.setattr(partner_access, "_manager", lambda: manager)
    source = _seed_admin_kb(partners_root.parent)
    record = registry.ensure_registered(source.parent, "physics", created_by=LOCAL_ADMIN_ID)
    registry.add_reference(
        record.kb_id, referrer_kind=scope_kind, referrer_id="active", created_by=LOCAL_ADMIN_ID
    )
    active = True
    registry.register_reference_reader(
        scope_kind, lambda referrer_id, user_id: active and referrer_id == "active" and user_id == "alice"
    )
    actor = CurrentUser("alice", "Alice", "user", scope_for_user("alice", is_admin=False))
    with user_context(actor):
        assert resolve_kb(record.ref).read_only
        assert provision_assets("ada", knowledge_bases=[record.ref])["errors"]
        active = False
        assert not registry.can_read(record, actor)
    assert registry.list_referenced("partner", "ada") == []
    assert not registry.can_read(record, partner_user("ada"))
    assert (source / "raw" / "doc.pdf").exists()


@pytest.mark.parametrize("visibility,source_owner", [("public", LOCAL_ADMIN_ID), ("private", "alice")])
def test_creator_can_delegate_public_or_own_private_kb(
    partners_root, monkeypatch, visibility, source_owner
):
    from deeptutor.multi_user import partner_access

    manager = PartnerManager()
    manager.save_config("ada", PartnerConfig(name="Ada", owner_id="alice"), auto_start=False)
    monkeypatch.setattr(partner_access, "_manager", lambda: manager)
    source = _seed_admin_kb(partners_root.parent)
    record = registry.ensure_registered(source.parent, "physics", created_by=source_owner)
    registry.set_visibility(record.kb_id, visibility)
    actor = CurrentUser("alice", "Alice", "user", scope_for_user("alice", is_admin=False))
    with user_context(actor):
        assert provision_assets("ada", knowledge_bases=[record.ref])["errors"] == []
    assert registry.list_references(record.kb_id)[0].created_by == "alice"
    with user_context(partner_user("ada")):
        assert resolve_kb(record.ref).read_only


@pytest.mark.parametrize("target_owner", ["alice", ""])
def test_admin_delegation_accounts_for_actual_partner_creator(
    partners_root, monkeypatch, target_owner
):
    from deeptutor.multi_user import partner_access

    manager = PartnerManager()
    manager.save_config("ada", PartnerConfig(name="Ada", owner_id=target_owner), auto_start=False)
    monkeypatch.setattr(partner_access, "_manager", lambda: manager)
    source = _seed_admin_kb(partners_root.parent)
    record = registry.ensure_registered(source.parent, "physics", created_by="admin2")
    registry.set_visibility(record.kb_id, "public")
    actor = CurrentUser("admin2", "Admin", "admin", scope_for_user("admin2", is_admin=True))
    with user_context(actor):
        assert provision_assets("ada", knowledge_bases=[record.ref])["errors"] == []
    assert registry.list_references(record.kb_id)[0].created_by == (target_owner or LOCAL_ADMIN_ID)
    # Rule R1: the owner withdraws without force; the partner's reference is revoked.
    registry.set_visibility(record.kb_id, "private")
    assert registry.list_referenced("partner", "ada") == []
    assert len(registry.list_unavailable_references("partner", "ada")) == 1
    assert not registry.can_read(registry.get_kb(record.ref), partner_user("ada"))


@pytest.mark.parametrize(
    "source_owner,target_owner,allowed",
    # spec 2026-10-29 D2: an admin cannot hand alice's private connector to anyone,
    # not even alice's own partner.
    [("alice", "alice", False), ("admin2", "alice", False),
     ("admin2", "", False), (LOCAL_ADMIN_ID, "", True)],
)
def test_admin_connector_delegation_uses_target_owner(
    partners_root, monkeypatch, source_owner, target_owner, allowed
):
    from deeptutor.multi_user import partner_access

    manager = PartnerManager()
    manager.save_config("ada", PartnerConfig(name="Ada", owner_id=target_owner), auto_start=False)
    monkeypatch.setattr(partner_access, "_manager", lambda: manager)
    _seed_admin_connected_kb(partners_root.parent)
    record = registry.ensure_registered(
        partners_root.parent / "knowledge_bases", "wiki", created_by=source_owner
    )
    actor = CurrentUser("admin2", "Admin", "admin", scope_for_user("admin2", is_admin=True))
    with user_context(actor):
        report = provision_assets("ada", knowledge_bases=[record.ref])
    assert bool(report["errors"]) is not allowed
    refs = registry.list_references(record.kb_id)
    assert bool(refs) is allowed
    if allowed:
        assert refs[0].created_by == (target_owner or LOCAL_ADMIN_ID)


def test_withdrawal_before_reference_write_cannot_be_laundered(partners_root, monkeypatch):
    from deeptutor.multi_user import partner_access

    manager = PartnerManager()
    manager.save_config("ada", PartnerConfig(name="Ada", owner_id="alice"), auto_start=False)
    monkeypatch.setattr(partner_access, "_manager", lambda: manager)
    source = _seed_admin_kb(partners_root.parent)
    record = registry.ensure_registered(source.parent, "physics", created_by=LOCAL_ADMIN_ID)
    registry.set_visibility(record.kb_id, "public")
    # A previous delegation has been withdrawn; a rejected retry must preserve its tombstone.
    registry.add_reference(
        record.kb_id, referrer_kind="partner", referrer_id="ada", created_by="alice"
    )
    original = registry.add_reference

    def withdraw_then_write(*args, **kwargs):
        registry.set_visibility(record.kb_id, "private", force=True)
        return original(*args, **kwargs)

    monkeypatch.setattr(registry, "add_reference", withdraw_then_write)
    actor = CurrentUser("alice", "Alice", "user", scope_for_user("alice", is_admin=False))
    with user_context(actor):
        assert provision_assets("ada", knowledge_bases=[record.ref])["errors"]
    assert registry.list_referenced("partner", "ada") == []
    assert len(registry.list_unavailable_references("partner", "ada")) == 1
    assert not registry.can_read(registry.get_kb(record.ref), partner_user("ada"))


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


def _migration_partner(partners_root, *, partner_id="ada", owner_id=""):
    PartnerManager().save_config(
        partner_id, PartnerConfig(name=partner_id, owner_id=owner_id), auto_start=False
    )
    return _seed_admin_kb(partners_root / partner_id / "workspace")


def test_migration_live_source_retires_without_losing_files(partners_root):
    import json

    from deeptutor.services.partners.kb_migration import migrate_partner_kb_copies

    source = _seed_admin_kb(partners_root.parent)
    copy = _migration_partner(partners_root)
    original = {p.relative_to(copy): p.read_bytes() for p in copy.rglob("*") if p.is_file()}
    report = migrate_partner_kb_copies()
    [record] = registry.list_referenced("partner", "ada")
    assert record.base_dir == str(source.parent)
    assert report == {
        "referenced": [{"partner_id": "ada", "name": "physics", "kb_id": record.kb_id}],
        "adopted": [],
    }
    retired = copy.parent / ".retired" / "physics"
    assert {p.relative_to(retired): p.read_bytes() for p in retired.rglob("*") if p.is_file()} == original
    assert not copy.exists()
    assert "physics" not in json.loads((copy.parent / "kb_config.json").read_text())["knowledge_bases"]
    assert registry.get_meta("partner_copies_migrated_at")
    assert migrate_partner_kb_copies(force=True) == {"referenced": [], "adopted": []}
    assert migrate_partner_kb_copies() == {"referenced": [], "adopted": []}


@pytest.mark.parametrize("damage", ["json", "shape", "permission"])
def test_migration_unavailable_source_is_retryable(partners_root, monkeypatch, damage):
    from deeptutor.services.partners.kb_migration import migrate_partner_kb_copies

    source = _seed_admin_kb(partners_root.parent)
    copy = _migration_partner(partners_root)
    catalog = source.parent / "kb_config.json"
    original = catalog.read_bytes()
    read_text = Path.read_text
    with monkeypatch.context() as patch:
        if damage == "permission":
            def denied(path, *args, **kwargs):
                if path == catalog:
                    raise PermissionError("synthetic catalog failure")
                return read_text(path, *args, **kwargs)
            patch.setattr(Path, "read_text", denied)
        else:
            catalog.write_text("{" if damage == "json" else '{"knowledge_bases": []}')
        assert migrate_partner_kb_copies() == {"referenced": [], "adopted": []}
    assert registry.get_meta("partner_copies_migrated_at") is None
    assert registry.list_referenced("partner", "ada") == []
    assert registry.find_by_location(copy.parent, copy.name) is None
    assert (copy / "raw" / "doc.pdf").read_bytes() == b"%PDF-fake"
    catalog.write_bytes(original)
    assert len(migrate_partner_kb_copies()["referenced"]) == 1
    assert registry.get_meta("partner_copies_migrated_at")


@pytest.mark.parametrize(
    "failure", ["register", "reference", "notice", "rename", "retire_config", "completion", "retire_completion"]
)
def test_migration_resumes_partial_work(partners_root, monkeypatch, failure):
    from deeptutor.services import file_io
    from deeptutor.services.partners.kb_migration import migrate_partner_kb_copies

    copy = _migration_partner(partners_root)
    retiring = failure in {"rename", "retire_config", "retire_completion"}
    if retiring:
        _seed_admin_kb(partners_root.parent)
    original_add = registry.add_reference
    original_write = file_io.atomic_write_json
    original_meta = registry.set_meta
    original_register = registry.register_kb

    def fail(*args, **kwargs):
        raise OSError("synthetic partial failure")

    def reference_then_fail(*args, **kwargs):
        original_add(*args, **kwargs)
        fail()

    def register_then_fail(*args, **kwargs):
        original_register(*args, **kwargs)
        fail()

    def config_fail(path, value, *args, **kwargs):
        if path == copy.parent / "kb_config.json":
            fail()
        return original_write(path, value, *args, **kwargs)

    def completion_fail(key, value):
        if key.startswith("partner_copy_progress:") and '"done": true' in value:
            fail()
        return original_meta(key, value)

    with monkeypatch.context() as patch:
        if failure == "register":
            patch.setattr(registry, "register_kb", register_then_fail)
        elif failure == "reference":
            patch.setattr(registry, "add_reference", reference_then_fail)
        elif failure == "notice":
            patch.setattr(registry, "add_migration_item", fail)
        elif failure in {"completion", "retire_completion"}:
            patch.setattr(registry, "set_meta", completion_fail)
        elif failure == "rename":
            patch.setattr(Path, "rename", fail)
        else:
            patch.setattr(file_io, "atomic_write_json", config_fail)
        migrate_partner_kb_copies()
    assert registry.get_meta("partner_copies_migrated_at") is None
    [record] = registry.list_records()
    report = migrate_partner_kb_copies()
    assert registry.list_referenced("partner", "ada") == [record]
    assert len(report["referenced" if retiring else "adopted"]) == 1
    assert registry.get_meta("partner_copies_migrated_at")
    assert migrate_partner_kb_copies(force=True) == {"referenced": [], "adopted": []}
    location = copy.parent / ".retired" / copy.name if retiring else copy
    assert (location / "raw" / "doc.pdf").read_bytes() == b"%PDF-fake"


@pytest.mark.asyncio
@pytest.mark.parametrize("preregistered", [False, True])
@pytest.mark.parametrize("unavailable", ["none", "active", "archived"])
async def test_migration_adopted_owner_catalog_read_and_partner_lifecycle(
    partners_root, preregistered, unavailable
):
    from deeptutor.api.routers.knowledge import _list_knowledge_bases
    from deeptutor.multi_user.knowledge_access import (
        list_visible_knowledge_bases,
        resolve_kb_document_path,
    )
    from deeptutor.services.partners.kb_migration import migrate_partner_kb_copies
    from deeptutor.services.workspace import ContentWorkspaceService
    from deeptutor.services.workspace.context import workspace_context
    from deeptutor.services.workspace.knowledge import knowledge_catalog, library_request

    copy = _migration_partner(partners_root, owner_id="alice")
    if preregistered:
        with partner_content_context("ada", PartnerManager().load_config("ada")):
            list_visible_knowledge_bases()
        before = registry.find_by_location(copy.parent, copy.name)
        assert before.created_by == "partner_ada"
    report = migrate_partner_kb_copies()
    [record] = registry.list_referenced("partner", "ada")
    if preregistered:
        assert record.kb_id == before.kb_id
    assert report["adopted"] == [{"partner_id": "ada", "name": "physics", "kb_id": record.kb_id}]
    assert record.created_by == "alice" and record.visibility == "private"
    assert record.base_dir == str(copy.parent)
    [notice] = [i for i in registry.list_migration_items() if i.kind == "partner_copy_adopted"]
    assert notice.key == "ada:physics"
    assert notice.data == {**report["adopted"][0], "owner_id": "alice"}
    assert list_assets("ada")["knowledge_bases"][0]["adopted"] is True
    manager = PartnerManager()
    manager.save_config("other", PartnerConfig(name="Other", owner_id="alice"), auto_start=False)
    registry.add_reference(
        record.kb_id, referrer_kind="partner", referrer_id="other", created_by="alice"
    )
    alice = CurrentUser("alice", "Alice", "user", scope_for_user("alice", is_admin=False))
    if unavailable != "none":
        with user_context(alice):
            service = ContentWorkspaceService()
            missing = service.create_workspace("Unavailable")
            storage = Path(missing["path"]) / ".deeptutor" / "data"
            excluded = _seed_admin_kb(storage, name="excluded")
            registry.ensure_registered(excluded.parent, excluded.name, created_by="alice")
            if unavailable == "archived":
                service.update_workspace(missing["workspace_id"], archived=True)
            Path(missing["path"]).rename(Path(missing["path"]).with_name("offline"))
    for stage in ["adopted", "detached", "destroyed", "recreated"]:
        if stage == "detached":
            assert remove_asset("ada", "knowledge_base", record.ref)
        elif stage == "destroyed":
            assert await manager.destroy_partner("ada")
        elif stage == "recreated":
            manager.save_config("ada", PartnerConfig(name="New", owner_id="bob"), auto_start=False)
        alice = CurrentUser("alice", "Alice", "user", scope_for_user("alice", is_admin=False))
        with user_context(alice):
            assert [r["ref"] for r in knowledge_catalog()] == [record.ref]
            token = library_request.set(True)
            try:
                [info] = _list_knowledge_bases()
                assert info.ref == record.ref and info.available is not False
                assert not info.read_only
                assert resolve_kb_document_path(info.ref, "doc.pdf").read_bytes() == b"%PDF-fake"
                assert resolve_kb(info.ref, require_write=True).base_dir == copy.parent
            finally:
                library_request.reset(token)
            workspace = ContentWorkspaceService().create_workspace(
                stage, resources={"knowledge_bases": []}
            )["workspace_id"]
            with workspace_context(workspace):
                assert list_visible_knowledge_bases() == []
                for write in [False, True]:
                    with pytest.raises(HTTPException) as denied:
                        resolve_kb(record.ref, require_write=write)
                    assert denied.value.status_code == 403
            selected = ContentWorkspaceService().create_workspace(
                stage + " selected", resources={"knowledge_bases": [record.ref]}
            )["workspace_id"]
            with workspace_context(selected):
                assert [r["ref"] for r in list_visible_knowledge_bases()] == [record.ref]
                assert resolve_kb_document_path(record.ref, "doc.pdf").read_bytes() == b"%PDF-fake"
        bob = CurrentUser("bob", "Bob", "user", scope_for_user("bob", is_admin=False))
        with user_context(bob):
            assert knowledge_catalog() == []
            assert resolve_kb_document_path(record.ref, "doc.pdf") is None
            with pytest.raises(HTTPException) as denied:
                resolve_kb(record.ref)
            assert denied.value.status_code == 404
        with partner_content_context("other", manager.load_config("other")):
            assert resolve_kb(record.ref).read_only
        if stage != "adopted":
            with partner_content_context("ada", manager.load_config("ada") or PartnerConfig(name="Ada")):
                with pytest.raises(HTTPException):
                    resolve_kb("physics")
                with pytest.raises(HTTPException):
                    resolve_kb(record.ref)
        assert migrate_partner_kb_copies(force=True) == {"referenced": [], "adopted": []}
        assert registry.get_kb(record.ref) == record


@pytest.mark.parametrize("owner_role", ["user", "admin", "unknown", "local"])
def test_migration_uses_creator_source_root(partners_root, owner_role):
    import json

    from deeptutor.multi_user import identity
    from deeptutor.services.partners.kb_migration import migrate_partner_kb_copies

    owner_id = "" if owner_role == "local" else "alice"
    identity.USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    identity.USERS_FILE.write_text(json.dumps(
        {} if owner_role in {"unknown", "local"} else
        {"Alice": {"id": "alice", "hash": "test-hash", "role": owner_role}}
    ))
    root = (
        scope_for_user("alice", is_admin=False).root
        if owner_role == "user" else partners_root.parent
    )
    source = _seed_admin_kb(root)
    # Backfill registers shared admin sources under LOCAL_ADMIN_ID first.
    registry.register_kb(
        base_dir=source.parent, name="physics", kind="uploaded",
        created_by="alice" if owner_role == "user" else LOCAL_ADMIN_ID,
    )
    copy = _migration_partner(partners_root, owner_id=owner_id)
    assert len(migrate_partner_kb_copies()["referenced"]) == 1
    [record] = registry.list_referenced("partner", "ada")
    assert record.base_dir == str(source.parent)
    assert not copy.exists()


@pytest.mark.parametrize("live", [False, True])
def test_migration_folder_without_catalog_is_readable(partners_root, live):
    from deeptutor.multi_user.knowledge_access import resolve_kb_document_path
    from deeptutor.services.partners.kb_migration import migrate_partner_kb_copies

    copy = _migration_partner(partners_root)
    (copy.parent / "kb_config.json").unlink()
    if live:
        source = _seed_admin_kb(partners_root.parent)
        (source.parent / "kb_config.json").unlink()
    report = migrate_partner_kb_copies()
    assert len(report["referenced" if live else "adopted"]) == 1
    [record] = registry.list_referenced("partner", "ada")
    assert resolve_kb_document_path(record.ref, "doc.pdf").read_bytes() == b"%PDF-fake"


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("preregistered", [False, True])
def test_migration_connector_rows(partners_root, live, preregistered):
    import json

    from deeptutor.services.partners.kb_migration import migrate_partner_kb_copies

    PartnerManager().save_config("ada", PartnerConfig(name="Ada"), auto_start=False)
    root = partners_root / "ada" / "workspace"
    entry = _seed_admin_connected_kb(root)
    if preregistered:
        old = registry.ensure_registered(root / "knowledge_bases", "wiki", created_by="partner_ada")
    if live:
        _seed_admin_connected_kb(partners_root.parent)
    report = migrate_partner_kb_copies()
    assert len(report["referenced" if live else "adopted"]) == 1
    [record] = registry.list_referenced("partner", "ada")
    assert record.kind == "weknora" and record.visibility == "private"
    catalog = json.loads((root / "knowledge_bases" / "kb_config.json").read_text())
    assert catalog["knowledge_bases"] == ({} if live else {"wiki": entry})
    assert resolve_kb(record.ref).name == "wiki"
    if preregistered:
        kept = registry.get_kb(old.ref)
        assert kept.created_by == LOCAL_ADMIN_ID
        assert KnowledgeBaseManager(base_dir=kept.base_dir).get_kb_entry("wiki") == entry
        assert resolve_kb(old.ref).name == "wiki"
    assert migrate_partner_kb_copies(force=True) == {"referenced": [], "adopted": []}


def test_migration_foreign_connector_does_not_retire_copy(partners_root):
    from deeptutor.services.partners.kb_migration import migrate_partner_kb_copies

    PartnerManager().save_config(
        "ada", PartnerConfig(name="Ada", owner_id="alice"), auto_start=False
    )
    _seed_admin_connected_kb(partners_root / "ada" / "workspace")
    _seed_admin_connected_kb(partners_root.parent)
    registry.ensure_registered(
        partners_root.parent / "knowledge_bases", "wiki", created_by=LOCAL_ADMIN_ID
    )
    catalog = partners_root / "ada" / "workspace" / "knowledge_bases" / "kb_config.json"
    before = catalog.read_bytes()
    assert migrate_partner_kb_copies() == {"referenced": [], "adopted": []}
    assert registry.get_meta("partner_copies_migrated_at") is None
    assert catalog.read_bytes() == before
    assert registry.list_referenced("partner", "ada") == []


def test_migration_partner_failure_does_not_block_others_or_complete(partners_root):
    from deeptutor.services.partners.kb_migration import migrate_partner_kb_copies

    bad = _migration_partner(partners_root, partner_id="bad")
    _migration_partner(partners_root, partner_id="good")
    config = partners_root / "bad" / "config.yaml"
    original = config.read_bytes()
    config.write_text("[broken")
    assert len(migrate_partner_kb_copies()["adopted"]) == 1
    assert registry.get_meta("partner_copies_migrated_at") is None
    assert (bad / "raw" / "doc.pdf").exists()
    assert registry.list_referenced("partner", "bad") == []
    config.write_bytes(original)
    assert migrate_partner_kb_copies()["adopted"][0]["partner_id"] == "bad"
    assert registry.get_meta("partner_copies_migrated_at")


def test_migration_skips_shared_workspace_and_retries_failed_force(partners_root):
    from deeptutor.services.partners.kb_migration import migrate_partner_kb_copies

    copy = _migration_partner(partners_root)
    manager = PartnerManager()
    manager.save_config(
        "ada", PartnerConfig(name="Ada", workspace_id="shared"), auto_start=False
    )
    assert migrate_partner_kb_copies() == {"referenced": [], "adopted": []}
    assert copy.exists()
    manager.save_config("ada", PartnerConfig(name="Ada"), auto_start=False)
    catalog = copy.parent / "kb_config.json"
    before = catalog.read_bytes()
    catalog.write_text("{")
    assert migrate_partner_kb_copies(force=True) == {"referenced": [], "adopted": []}
    assert not registry.get_meta("partner_copies_migrated_at")
    catalog.write_bytes(before)
    assert len(migrate_partner_kb_copies()["adopted"]) == 1


@pytest.mark.parametrize("damage", ["copy_catalog", "identity", "traversal", "symlink"])
def test_migration_unsafe_or_unavailable_inputs_preserve_data(partners_root, damage):
    from deeptutor.multi_user import identity
    from deeptutor.services.partners.kb_migration import migrate_partner_kb_copies

    copy = _migration_partner(partners_root, owner_id="alice")
    if damage == "identity":
        identity.USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        identity.USERS_FILE.write_text("{")
    elif damage == "traversal":
        (copy.parent / "kb_config.json").write_text('{"knowledge_bases": {"../escape": {}}}')
    elif damage == "symlink":
        (copy.parent / "alias").symlink_to(copy, target_is_directory=True)
    else:
        (copy.parent / "kb_config.json").write_text("{")
    assert migrate_partner_kb_copies() == {"referenced": [], "adopted": []}
    assert registry.get_meta("partner_copies_migrated_at") is None
    assert registry.list_referenced("partner", "ada") == []
    assert (copy / "raw" / "doc.pdf").read_bytes() == b"%PDF-fake"


def test_migration_preserves_retired_collision_and_backfill_reports_count(partners_root):
    from deeptutor.knowledge.registry_backfill import backfill_registry

    copy = _migration_partner(partners_root)
    _seed_admin_kb(partners_root.parent)
    retired = copy.parent / ".retired" / "physics"
    retired.mkdir(parents=True)
    (retired / "old.txt").write_text("old")
    assert backfill_registry()["partner_copies"] == 1
    assert (retired / "old.txt").read_text() == "old"
    [new] = list(retired.parent.glob("physics-*"))
    assert (new / "raw" / "doc.pdf").read_bytes() == b"%PDF-fake"
    assert backfill_registry()["partner_copies"] == 0


@pytest.mark.parametrize("content", ["{", '{"knowledge_bases": []}', '{"knowledge_bases": {"physics": null}}'])
def test_migration_backfill_does_not_hide_damaged_source(partners_root, content):
    from deeptutor.knowledge.registry_backfill import _sync_root_unguarded, backfill_registry

    source = _seed_admin_kb(partners_root.parent)
    copy = _migration_partner(partners_root)
    catalog = source.parent / "kb_config.json"
    catalog.write_text(content)
    with pytest.raises(ValueError):
        _sync_root_unguarded(source.parent, created_by=LOCAL_ADMIN_ID)
    assert backfill_registry()["partner_copies"] == 0
    assert catalog.read_text() == content
    assert registry.get_meta("partner_copies_migrated_at") is None
    assert registry.list_referenced("partner", "ada") == []
    assert (copy / "raw" / "doc.pdf").read_bytes() == b"%PDF-fake"


def test_migration_pending_retirement_never_switches_to_adoption(partners_root, monkeypatch):
    from deeptutor.services.partners.kb_migration import migrate_partner_kb_copies

    source = _seed_admin_kb(partners_root.parent)
    copy = _migration_partner(partners_root)
    original = Path.rename

    def fail(*args, **kwargs):
        raise OSError("synthetic rename failure")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "rename", fail)
        assert migrate_partner_kb_copies() == {"referenced": [], "adopted": []}
    hidden = source.parent / ".offline"
    original(source, hidden)
    assert migrate_partner_kb_copies() == {"referenced": [], "adopted": []}
    assert registry.find_by_location(copy.parent, copy.name) is None
    assert registry.get_meta("partner_copies_migrated_at") is None
    assert (copy / "raw" / "doc.pdf").exists()
    original(hidden, source)
    assert len(migrate_partner_kb_copies()["referenced"]) == 1


@pytest.mark.parametrize("entrypoint", ["direct", "backfill"])
@pytest.mark.parametrize("same_name_admin", [False, True])
@pytest.mark.parametrize("store", ["current", "legacy"])
@pytest.mark.parametrize("damage", ["missing_hash", "invalid_row", "duplicate_id"])
def test_migration_identity_snapshot_damage_and_repair(
    partners_root, entrypoint, same_name_admin, store, damage
):
    import json

    from deeptutor.knowledge.registry_backfill import backfill_registry
    from deeptutor.multi_user import identity
    from deeptutor.services.partners.kb_migration import migrate_partner_kb_copies

    copy = _migration_partner(partners_root, owner_id="alice")
    source = _seed_admin_kb(scope_for_user("alice", is_admin=False).root)
    if same_name_admin:
        _seed_admin_kb(partners_root.parent)
    valid = {"Alice": {"id": "alice", "password_hash": "test-hash", "role": "user"}}
    broken = json.loads(json.dumps(valid))
    if damage == "missing_hash":
        del broken["Alice"]["password_hash"]
    elif damage == "invalid_row":
        broken["Other"] = None
    else:
        broken["Other"] = dict(broken["Alice"])
    path = identity.USERS_FILE if store == "current" else identity.LEGACY_USERS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(broken))
    identity_bytes = path.read_bytes()
    copy_bytes = {p.relative_to(copy.parent): p.read_bytes()
                  for p in copy.parent.rglob("*") if p.is_file()}
    run = migrate_partner_kb_copies if entrypoint == "direct" else backfill_registry
    run()
    assert path.read_bytes() == identity_bytes
    if store == "legacy":
        assert not identity.USERS_FILE.exists()
    assert {p.relative_to(copy.parent): p.read_bytes()
            for p in copy.parent.rglob("*") if p.is_file()} == copy_bytes
    assert registry.list_referenced("partner", "ada") == []
    assert registry.find_by_location(copy.parent, copy.name) is None
    assert not registry.get_meta("partner_copies_migrated_at")
    assert not registry.get_meta("partner_copy_progress:ada")
    assert registry.list_migration_items() == []
    path.write_text(json.dumps(valid))
    repaired = path.read_bytes()
    run()
    [record] = registry.list_referenced("partner", "ada")
    assert record.base_dir == str(source.parent)
    assert registry.get_meta("partner_copies_migrated_at")
    assert not copy.exists()
    if entrypoint == "direct":
        assert path.read_bytes() == repaired
        if store == "legacy":
            assert not identity.USERS_FILE.exists()


@pytest.mark.parametrize("entrypoint", ["direct", "backfill"])
@pytest.mark.parametrize("store", ["current", "legacy"])
def test_migration_accepts_valid_legacy_identity_formats(partners_root, entrypoint, store):
    import json

    from deeptutor.knowledge.registry_backfill import backfill_registry
    from deeptutor.multi_user import identity
    from deeptutor.services.partners.kb_migration import migrate_partner_kb_copies

    _migration_partner(partners_root, owner_id="alice")
    source = _seed_admin_kb(scope_for_user("alice", is_admin=False).root)
    _seed_admin_kb(partners_root.parent)
    path = identity.USERS_FILE if store == "current" else identity.LEGACY_USERS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "Old admin": "legacy-hash",
        "Alice": {"id": "alice", "password_hash": "legacy-hash"},
        "No ID": {"hash": "legacy-hash"},
    }))
    original = path.read_bytes()
    (migrate_partner_kb_copies if entrypoint == "direct" else backfill_registry)()
    [record] = registry.list_referenced("partner", "ada")
    assert record.base_dir == str(source.parent)
    if entrypoint == "direct":
        assert path.read_bytes() == original


@pytest.fixture
def pending_legacy_tree(partners_root, monkeypatch):
    from deeptutor.multi_user import identity, paths

    legacy = partners_root.parent.parent / "multi-user"
    monkeypatch.setattr(paths, "LEGACY_MULTI_USER_ROOT", legacy)
    monkeypatch.setattr(paths, "_legacy_migration_done", False)
    monkeypatch.setattr(
        identity, "LEGACY_USERS_FILE", partners_root.parent / "user" / "auth_users.json"
    )
    return legacy


@pytest.mark.parametrize("entrypoint", ["direct", "backfill"])
@pytest.mark.parametrize("same_name_admin", [False, True])
@pytest.mark.parametrize("damaged", [False, True])
def test_migration_pending_legacy_tree_validates_before_moving(
    partners_root, pending_legacy_tree, entrypoint, same_name_admin, damaged
):
    import json

    from deeptutor.knowledge.registry_backfill import backfill_registry
    from deeptutor.multi_user import identity, paths
    from deeptutor.services.partners.kb_migration import (
        identity_snapshot,
        migrate_partner_kb_copies,
    )

    copy = _migration_partner(partners_root, owner_id="alice")
    source = _seed_admin_kb(pending_legacy_tree / "alice")
    (source / "raw" / "doc.pdf").write_bytes(b"legacy account source")
    if same_name_admin:
        _seed_admin_kb(partners_root.parent)
    path = pending_legacy_tree / "_system" / "auth" / "users.json"
    path.parent.mkdir(parents=True)
    valid = {"Alice": {"id": "alice", "hash": "test-hash", "role": "user"}}
    path.write_text(json.dumps(
        {"Alice": {"id": "alice", "role": "user"}} if damaged else valid
    ))
    # Seeding must not accidentally resolve the pending layout before the test.
    identity_bytes = path.read_bytes()
    tree_bytes = {p.relative_to(pending_legacy_tree): p.read_bytes()
                  for p in pending_legacy_tree.rglob("*") if p.is_file()}
    copy_bytes = {p.relative_to(copy.parent): p.read_bytes()
                  for p in copy.parent.rglob("*") if p.is_file()}
    paths._legacy_migration_done = False
    assert not paths.SYSTEM_ROOT.exists()
    assert not identity.USERS_FILE.exists()
    assert not identity.LEGACY_USERS_FILE.exists()
    run = migrate_partner_kb_copies if entrypoint == "direct" else backfill_registry
    if damaged:
        result = run()
        assert result == ({"referenced": [], "adopted": []} if entrypoint == "direct" else {
            "admin": 0, "admin_normalized": 0, "account": 0, "workspace": 0, "promoted": 0,
            "connectors_disabled": 0, "partner_copies": 0,
        })
        assert path.read_bytes() == identity_bytes
        assert {p.relative_to(pending_legacy_tree): p.read_bytes()
                for p in pending_legacy_tree.rglob("*") if p.is_file()} == tree_bytes
        assert {p.relative_to(copy.parent): p.read_bytes()
                for p in copy.parent.rglob("*") if p.is_file()} == copy_bytes
        assert not paths.SYSTEM_ROOT.exists()
        assert not paths._legacy_migration_done
        assert registry.list_referenced("partner", "ada") == []
        assert registry.find_by_location(copy.parent, copy.name) is None
        assert not registry.get_meta("partner_copies_migrated_at")
        assert not registry.get_meta("partner_copy_progress:ada")
        assert registry.list_migration_items() == []
        path.write_text(json.dumps(valid))
    repaired = path.read_bytes()
    assert identity_snapshot() == {"alice": {"username": "Alice", "role": "user"}}
    assert path.read_bytes() == repaired
    assert not paths.SYSTEM_ROOT.exists()
    assert not paths._legacy_migration_done
    result = run()
    [record] = registry.list_referenced("partner", "ada")
    target = paths.USERS_ROOT / "alice" / "knowledge_bases"
    assert record.base_dir == str(target)
    assert record.created_by == "alice"
    assert (target / "physics" / "raw" / "doc.pdf").read_bytes() == b"legacy account source"
    assert not source.exists()
    assert not copy.exists()
    assert (copy.parent / ".retired" / "physics" / "raw" / "doc.pdf").read_bytes() == b"%PDF-fake"
    assert registry.get_meta("partner_copies_migrated_at")
    assert registry.list_migration_items() == []
    if entrypoint == "direct":
        assert result == {
            "referenced": [{"partner_id": "ada", "name": "physics", "kb_id": record.kb_id}],
            "adopted": [],
        }
        assert identity.USERS_FILE.read_bytes() == repaired
    else:
        assert result["partner_copies"] == 1
    assert migrate_partner_kb_copies() == {"referenced": [], "adopted": []}


@pytest.mark.parametrize("entrypoint", ["direct", "backfill"])
@pytest.mark.parametrize("target_store", ["current", "fallback", "empty"])
@pytest.mark.parametrize("obsolete_damaged", [False, True])
def test_migration_legacy_tree_respects_existing_targets(
    partners_root, pending_legacy_tree, entrypoint, target_store, obsolete_damaged
):
    import json

    from deeptutor.knowledge.registry_backfill import backfill_registry
    from deeptutor.multi_user import identity, paths
    from deeptutor.services.partners.kb_migration import identity_snapshot, migrate_partner_kb_copies

    copy = _migration_partner(partners_root, owner_id="alice")
    admin = _seed_admin_kb(partners_root.parent)
    legacy_source = _seed_admin_kb(pending_legacy_tree / "alice")
    source = _seed_admin_kb(paths.USERS_ROOT / "alice")
    (source / "raw" / "doc.pdf").write_bytes(b"authoritative account source")
    path = pending_legacy_tree / "_system" / "auth" / "users.json"
    path.parent.mkdir(parents=True)
    obsolete_record = {"id": "alice", "role": "admin"}
    if not obsolete_damaged:
        obsolete_record["hash"] = "old-hash"
    path.write_text(json.dumps({"Alice": obsolete_record}))
    obsolete = path.read_bytes()
    paths.SYSTEM_ROOT.mkdir(parents=True, exist_ok=True)
    if target_store != "empty":
        target = identity.USERS_FILE if target_store == "current" else identity.LEGACY_USERS_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "Alice": {"id": "alice", "hash": "test-hash", "role": "user"},
        }))
    paths._legacy_migration_done = False
    assert identity_snapshot() == (
        {} if target_store == "empty" else {"alice": {"username": "Alice", "role": "user"}}
    )
    assert not paths._legacy_migration_done
    result = (migrate_partner_kb_copies if entrypoint == "direct" else backfill_registry)()
    [record] = registry.list_referenced("partner", "ada")
    assert record.base_dir == str((admin if target_store == "empty" else source).parent)
    assert path.read_bytes() == obsolete
    assert (legacy_source / "raw" / "doc.pdf").read_bytes() == b"%PDF-fake"
    assert (source / "raw" / "doc.pdf").read_bytes() == b"authoritative account source"
    assert not copy.exists()
    assert registry.get_meta("partner_copies_migrated_at")
    if entrypoint == "backfill":
        assert result["partner_copies"] == 1
    else:
        assert result["adopted"] == []


@pytest.mark.parametrize("entrypoint", ["direct", "backfill"])
def test_migration_pending_tree_identity_precedes_auth_users(
    partners_root, pending_legacy_tree, entrypoint
):
    import json

    from deeptutor.knowledge.registry_backfill import backfill_registry
    from deeptutor.multi_user import identity, paths
    from deeptutor.services.partners.kb_migration import migrate_partner_kb_copies

    _migration_partner(partners_root, owner_id="alice")
    _seed_admin_kb(partners_root.parent)
    _seed_admin_kb(pending_legacy_tree / "alice")
    path = pending_legacy_tree / "_system" / "auth" / "users.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "Alice": {"id": "alice", "hash": "test-hash", "role": "user"},
    }))
    identity.LEGACY_USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    identity.LEGACY_USERS_FILE.write_text('{"Alice": {"id": "alice"}}')
    fallback = identity.LEGACY_USERS_FILE.read_bytes()
    paths._legacy_migration_done = False
    assert not paths.SYSTEM_ROOT.exists()
    (migrate_partner_kb_copies if entrypoint == "direct" else backfill_registry)()
    [record] = registry.list_referenced("partner", "ada")
    assert record.base_dir == str(paths.USERS_ROOT / "alice" / "knowledge_bases")
    assert identity.LEGACY_USERS_FILE.read_bytes() == fallback
    assert registry.get_meta("partner_copies_migrated_at")


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["direct", "backfill"])
@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("after_failure", [False, True])
async def test_migration_synthetic_preregistration_preserves_consumers(
    partners_root, entrypoint, live, after_failure
):
    from deeptutor.api.routers import knowledge
    from deeptutor.knowledge.registry_backfill import backfill_registry
    from deeptutor.multi_user.knowledge_access import (
        list_visible_knowledge_bases,
        resolve_kb_document_path,
    )
    from deeptutor.services.partners.kb_migration import migrate_partner_kb_copies
    from deeptutor.services.workspace.knowledge import knowledge_catalog, library_request

    copy = _migration_partner(partners_root, owner_id="alice")
    manager = PartnerManager()
    manager.save_config("other", PartnerConfig(name="Other", owner_id="alice"), auto_start=False)
    source = _seed_admin_kb(partners_root.parent) if live else None
    if live:
        (source / "raw" / "doc.pdf").write_bytes(b"%PDF-source")
    run = migrate_partner_kb_copies if entrypoint == "direct" else backfill_registry
    source_catalog = partners_root.parent / "knowledge_bases" / "kb_config.json"
    if after_failure:
        source_catalog.parent.mkdir(parents=True, exist_ok=True)
        original = source_catalog.read_bytes() if source_catalog.exists() else None
        source_catalog.write_text("{")
        run()
        assert not registry.get_meta("partner_copies_migrated_at")
        assert not registry.get_meta("partner_copy_progress:ada")
    with partner_content_context("ada", manager.load_config("ada")):
        list_visible_knowledge_bases()
    old = registry.find_by_location(copy.parent, copy.name)
    assert old.created_by == "partner_ada"
    registry.add_reference(
        old.kb_id, referrer_kind="partner", referrer_id="other",
        created_by="alice", doc_ids=["doc.pdf"],
    )
    consumers = registry.list_references(old.kb_id)
    if after_failure:
        if original is None:
            source_catalog.unlink()
        else:
            source_catalog.write_bytes(original)
    run()
    assert registry.get_meta("partner_copies_migrated_at")
    kept = registry.get_kb(old.kb_id)
    assert kept.created_by == "alice" and kept.visibility == "private"
    assert [ref for ref in registry.list_references(old.kb_id)
            if ref.referrer_id == "other"] == consumers
    [target] = registry.list_referenced("partner", "ada")
    if live:
        assert target.base_dir == str(source.parent) and target.kb_id != old.kb_id
        assert ".retired" in Path(kept.base_dir).parts
        assert not copy.exists()
    else:
        assert target.kb_id == old.kb_id and copy.exists()
    alice = CurrentUser("alice", "Alice", "user", scope_for_user("alice", is_admin=False))
    for destroyed in [False, True]:
        if destroyed:
            assert await manager.destroy_partner("ada")
        with user_context(alice):
            refs = [item["ref"] for item in knowledge_catalog()]
            assert (old.ref in refs) is not live
            token = library_request.set(True)
            try:
                assert resolve_kb_document_path(old.ref, "doc.pdf").read_bytes() == b"%PDF-fake"
                rows = {row.kb_id: row for row in await knowledge.list_knowledge_bases()}
                if not live:
                    assert not rows[old.kb_id].can_set_default
                    assert not rows[old.kb_id].is_default
                    with pytest.raises(HTTPException) as error:
                        await knowledge.set_default_kb(old.ref)
                    assert error.value.status_code == 409
                else:
                    assert old.kb_id not in rows
            finally:
                library_request.reset(token)
        assert registry.list_referenced("partner", "other") == [kept]
    assert migrate_partner_kb_copies(force=True) == {"referenced": [], "adopted": []}


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("failure", ["ownership", "rename", "relocate", "completion"])
def test_migration_preregistered_interruption_keeps_identity(
    partners_root, monkeypatch, live, failure
):
    from deeptutor.services.partners.kb_migration import migrate_partner_kb_copies

    copy = _migration_partner(partners_root, owner_id="alice")
    if live:
        _seed_admin_kb(partners_root.parent)
    old = registry.ensure_registered(copy.parent, copy.name, created_by="partner_ada")
    registry.add_reference(
        old.kb_id, referrer_kind="partner", referrer_id="other",
        created_by="alice", doc_ids=["doc.pdf"],
    )
    original_adopt = registry.adopt_partner_copy
    original_rename = Path.rename
    original_relocate = registry.relocate_kb
    original_meta = registry.set_meta

    def fail():
        raise OSError("synthetic preregistration interruption")

    def adopt_then_fail(*args, **kwargs):
        original_adopt(*args, **kwargs)
        fail()

    def rename_then_fail(*args, **kwargs):
        original_rename(*args, **kwargs)
        fail()

    def relocate_then_fail(*args, **kwargs):
        original_relocate(*args, **kwargs)
        fail()

    def completion_fail(key, value):
        if key.startswith("partner_copy_progress:") and '"done": true' in value:
            fail()
        original_meta(key, value)

    with monkeypatch.context() as patch:
        if failure == "ownership" or (not live and failure in {"rename", "relocate"}):
            patch.setattr(registry, "adopt_partner_copy", adopt_then_fail)
        elif failure == "rename":
            patch.setattr(Path, "rename", rename_then_fail)
        elif failure == "relocate":
            patch.setattr(registry, "relocate_kb", relocate_then_fail)
        else:
            patch.setattr(registry, "set_meta", completion_fail)
        assert migrate_partner_kb_copies() == {"referenced": [], "adopted": []}
    assert not registry.get_meta("partner_copies_migrated_at")
    assert len(migrate_partner_kb_copies()["referenced" if live else "adopted"]) == 1
    kept = registry.get_kb(old.ref)
    assert kept.created_by == "alice"
    assert (Path(kept.base_dir) / kept.dir_name / "raw" / "doc.pdf").read_bytes() == b"%PDF-fake"
    assert registry.list_referenced("partner", "other") == [kept]
    assert [ref.doc_ids for ref in registry.list_references(old.kb_id)
            if ref.referrer_id == "other"] == [["doc.pdf"]]
    assert migrate_partner_kb_copies(force=True) == {"referenced": [], "adopted": []}


def test_migration_preserves_independent_human_registration(partners_root):
    from deeptutor.services.partners.kb_migration import migrate_partner_kb_copies

    copy = _migration_partner(partners_root, owner_id="alice")
    _seed_admin_kb(partners_root.parent)
    old = registry.ensure_registered(copy.parent, copy.name, created_by="bob")
    assert migrate_partner_kb_copies() == {"referenced": [], "adopted": []}
    assert registry.get_kb(old.ref) == old
    assert registry.list_referenced("partner", "ada") == []
    assert (copy / "raw" / "doc.pdf").read_bytes() == b"%PDF-fake"


@pytest.mark.parametrize("problem", ["foreign_synthetic", "retired_symlink"])
def test_migration_unresolved_preregistration_remains_retryable(partners_root, problem):
    from deeptutor.services.partners.kb_migration import migrate_partner_kb_copies

    copy = _migration_partner(partners_root, owner_id="alice")
    _seed_admin_kb(partners_root.parent)
    old = registry.ensure_registered(
        copy.parent, copy.name,
        created_by="partner_other" if problem == "foreign_synthetic" else "partner_ada",
    )
    outside = partners_root.parent / "outside"
    outside.mkdir()
    if problem == "retired_symlink":
        (copy.parent / ".retired").symlink_to(outside, target_is_directory=True)
    original = (copy.parent / "kb_config.json").read_bytes()
    assert migrate_partner_kb_copies() == {"referenced": [], "adopted": []}
    assert not registry.get_meta("partner_copies_migrated_at")
    assert registry.get_kb(old.ref) == old
    assert (copy.parent / "kb_config.json").read_bytes() == original
    assert (copy / "raw" / "doc.pdf").read_bytes() == b"%PDF-fake"
    assert list(outside.iterdir()) == []
    if problem == "retired_symlink":
        (copy.parent / ".retired").unlink()
        assert len(migrate_partner_kb_copies()["referenced"]) == 1
