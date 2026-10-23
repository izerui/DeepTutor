"""System knowledge-base registry: ids, visibility, references, access."""

from __future__ import annotations

import pytest

from deeptutor.knowledge import registry


def test_database_lives_under_system_root(mu_isolated_root, monkeypatch):
    from deeptutor.multi_user import paths

    monkeypatch.setattr(registry, "DB_PATH_OVERRIDE", None)
    assert registry._db_path() == paths.SYSTEM_ROOT / "kb_registry.sqlite3"
    registry.register_kb(
        base_dir=mu_isolated_root / "kbs", name="algebra", kind="uploaded", created_by="u1"
    )
    assert (paths.SYSTEM_ROOT / "kb_registry.sqlite3").exists()


def test_reads_do_not_create_the_database(tmp_path):
    assert registry.get_kb("missing") is None
    assert registry.find_by_location(tmp_path, "algebra") is None
    assert registry.list_records() == []
    assert registry.list_references("missing") == []
    assert not registry._db_path().exists()


def test_register_is_idempotent_per_location(tmp_path):
    first = registry.register_kb(
        base_dir=tmp_path / "a", name="algebra", kind="uploaded", created_by="u1"
    )
    again = registry.register_kb(
        base_dir=tmp_path / "a",
        name="algebra",
        kind="uploaded",
        created_by="u2",
        visibility="public",
    )
    other = registry.register_kb(
        base_dir=tmp_path / "b", name="algebra", kind="uploaded", created_by="u2"
    )

    assert again == first
    assert first.visibility == "private"
    assert first.created_by == "u1"
    assert first.base_dir == str((tmp_path / "a").resolve())
    assert first.dir_name == "algebra"
    assert other.kb_id != first.kb_id
    assert first.ref == f"kb:{first.kb_id}"
    assert registry.get_kb(first.kb_id) == first
    assert registry.get_kb(first.ref) == first
    assert registry.find_by_location(tmp_path / "a", "algebra") == first
    assert registry.find_by_location(tmp_path / "a", "missing") is None


def test_sync_base_dir_registers_missing_names_with_their_kind(tmp_path):
    existing = registry.register_kb(
        base_dir=tmp_path, name="algebra", kind="uploaded", created_by="u1", visibility="public"
    )
    records = registry.sync_base_dir(
        tmp_path,
        {"algebra": {"rag_provider": "llamaindex"}, "vault": {"type": "obsidian"}},
        created_by="u2",
    )
    assert records["algebra"] == existing
    assert records["vault"].kind == "obsidian"
    assert records["vault"].created_by == "u2"
    assert records["vault"].visibility == "private"
    assert registry.kind_of({"type": "something-else"}) == "uploaded"
    assert registry.kind_of(None) == "uploaded"


def test_connectors_are_private_and_only_partners_may_reference_them(tmp_path):
    vault = registry.register_kb(base_dir=tmp_path, name="vault", kind="obsidian", created_by="t1")
    assert registry.is_connector(vault)
    with pytest.raises(registry.KbVisibilityError):
        registry.set_visibility(vault.kb_id, "public")
    with pytest.raises(registry.KbVisibilityError):
        registry.register_kb(
            base_dir=tmp_path, name="ima", kind="ima", created_by="t1", visibility="public"
        )
    with pytest.raises(registry.KbReferenceError):
        registry.add_reference(
            vault.kb_id, referrer_kind="mastery_template", referrer_id="tpl1", created_by="t1"
        )
    registry.add_reference(vault.kb_id, referrer_kind="partner", referrer_id="ada", created_by="t1")
    assert [ref.referrer_id for ref in registry.list_references(vault.kb_id)] == ["ada"]


def test_making_private_is_blocked_only_by_other_owners_references(tmp_path):
    kb = registry.register_kb(
        base_dir=tmp_path, name="physics", kind="uploaded", created_by="t1", visibility="public"
    )
    registry.add_reference(
        kb.kb_id, referrer_kind="mastery_template", referrer_id="own", created_by="t1"
    )
    assert registry.set_visibility(kb.kb_id, "private").visibility == "private"

    registry.set_visibility(kb.kb_id, "public")
    registry.add_reference(kb.kb_id, referrer_kind="partner", referrer_id="ada", created_by="t2")
    with pytest.raises(registry.KbVisibilityError, match="other accounts"):
        registry.set_visibility(kb.kb_id, "private")
    assert registry.list_unavailable_references("partner", "ada") == []
    assert registry.set_visibility(kb.kb_id, "private", force=True).visibility == "private"
    with pytest.raises(registry.KbVisibilityError):
        registry.set_visibility(kb.kb_id, "hidden")

    # The forced withdrawal cut the other account off ("资料不可用", spec 7.4);
    # the owner's own reference stays and keeps reading through it.
    assert [(r.referrer_kind, r.referrer_id) for r in registry.list_references(kb.kb_id)] == [
        ("mastery_template", "own")
    ]
    [revoked] = registry.list_unavailable_references("partner", "ada")
    assert (revoked.kb_id, revoked.kb_name, revoked.reason) == (kb.kb_id, "physics", "made_private")
    assert registry.list_revoked_references(kb.kb_id) == [revoked]


def test_revoked_references_never_grant_access_and_clear_on_restatement(tmp_path, make_user):
    partner = make_user("partner_ada")
    kb = registry.register_kb(
        base_dir=tmp_path, name="physics", kind="uploaded", created_by="t1", visibility="public"
    )
    registry.add_reference(kb.kb_id, referrer_kind="partner", referrer_id="ada", created_by="t2")
    registry.set_visibility(kb.kb_id, "private", force=True)
    assert not registry.can_read(registry.get_kb(kb.kb_id), partner)

    # The referrer re-adds the KB (e.g. after the owner shares it again).
    registry.set_visibility(kb.kb_id, "public")
    registry.add_reference(kb.kb_id, referrer_kind="partner", referrer_id="ada", created_by="t2")
    assert registry.list_unavailable_references("partner", "ada") == []

    registry.set_visibility(kb.kb_id, "private", force=True)
    other = registry.register_kb(base_dir=tmp_path, name="other", kind="uploaded", created_by="t2")
    registry.replace_references("partner", "ada", [(other.kb_id, None)], created_by="t2")
    assert registry.list_unavailable_references("partner", "ada") == []
    registry.remove_references("partner", "ada")
    assert registry.list_references(other.kb_id) == []


def test_replace_and_remove_references(tmp_path):
    a = registry.register_kb(base_dir=tmp_path, name="a", kind="uploaded", created_by="t1")
    b = registry.register_kb(base_dir=tmp_path, name="b", kind="uploaded", created_by="t1")

    registry.replace_references(
        "mastery_template", "tpl1", [(a.kb_id, None), (b.ref, ["d1", "d2", "d1"])], created_by="t1"
    )
    assert [ref.doc_ids for ref in registry.list_references(a.kb_id)] == [None]
    assert [ref.doc_ids for ref in registry.list_references(b.kb_id)] == [["d1", "d2"]]
    assert {record.kb_id for record in registry.list_referenced("mastery_template", "tpl1")} == {
        a.kb_id,
        b.kb_id,
    }

    with pytest.raises(registry.KbReferenceError):
        registry.replace_references(
            "mastery_template", "tpl1", [(a.kb_id, None), ("nope", None)], created_by="t1"
        )
    # A refused replacement leaves the previous set untouched.
    assert len(registry.list_referenced("mastery_template", "tpl1")) == 2

    registry.replace_references("mastery_template", "tpl1", [(b.kb_id, None)], created_by="t1")
    assert registry.list_references(a.kb_id) == []
    registry.remove_reference(b.kb_id, "mastery_template", "tpl1")
    assert registry.list_references(b.kb_id) == []
    registry.add_reference(b.kb_id, referrer_kind="course", referrer_id="c1", created_by="t1")
    registry.remove_references("course", "c1")
    assert registry.list_referenced("course", "c1") == []


def test_read_and_write_rules(tmp_path, make_user):
    admin = make_user("root", role="admin")
    owner = make_user("t1", role="teacher")
    student = make_user("s1", role="student")
    private = registry.register_kb(base_dir=tmp_path, name="notes", kind="uploaded", created_by="t1")
    public = registry.register_kb(
        base_dir=tmp_path, name="atlas", kind="uploaded", created_by="t1", visibility="public"
    )

    assert registry.can_read(private, owner) and registry.can_write(private, owner)
    assert registry.can_read(private, admin) and registry.can_write(private, admin)
    assert not registry.can_read(private, student)
    assert registry.can_read(public, student)
    assert not registry.can_write(public, student)


def test_reference_readers_grant_read_only_access(tmp_path, make_user):
    student = make_user("s1", role="student")
    other = make_user("s2", role="student")
    kb = registry.register_kb(base_dir=tmp_path, name="notes", kind="uploaded", created_by="t1")
    registry.add_reference(
        kb.kb_id, referrer_kind="mastery_template", referrer_id="tpl1", created_by="t1"
    )
    assert not registry.can_read(kb, student)

    registry.register_reference_reader(
        "mastery_template",
        lambda referrer_id, user_id: (referrer_id, user_id) == ("tpl1", "s1"),
    )
    assert registry.can_read(kb, student)
    assert not registry.can_write(kb, student)
    assert not registry.can_read(kb, other)


def test_a_broken_reader_never_grants_access(tmp_path, make_user):
    student = make_user("s1", role="student")
    kb = registry.register_kb(base_dir=tmp_path, name="notes", kind="uploaded", created_by="t1")
    registry.add_reference(kb.kb_id, referrer_kind="course", referrer_id="c1", created_by="t1")

    def _boom(_referrer_id: str, _user_id: str) -> bool:
        raise RuntimeError("reader failed")

    registry.register_reference_reader("course", _boom)
    assert not registry.can_read(kb, student)


def test_builtin_reader_modules_never_clobber_registered_readers(tmp_path, make_user, monkeypatch):
    """Plans C/D add modules to BUILTIN_READER_MODULES; a reader a test (or an
    earlier import) registered must survive their lazy import."""
    student = make_user("s1", role="student")
    kb = registry.register_kb(base_dir=tmp_path, name="notes", kind="uploaded", created_by="t1")
    registry.add_reference(kb.kb_id, referrer_kind="probe", referrer_id="p1", created_by="t1")
    registry.register_reference_reader("probe", lambda _ref, user_id: user_id == "s1")

    imported: list[str] = []

    def fake_import(name: str):
        imported.append(name)
        registry.register_reference_reader("probe", lambda _ref, _user: False)
        registry.register_reference_reader("other", lambda _ref, _user: True)

    monkeypatch.setattr(registry, "BUILTIN_READER_MODULES", ("tests.fake_reader_module",))
    monkeypatch.setattr(registry.importlib, "import_module", fake_import)

    assert registry.can_read(kb, student) is True
    assert imported == ["tests.fake_reader_module"]
    # New kinds from the module are kept; the earlier registration won.
    assert "other" in registry._readers
    assert registry._readers["probe"](None, "s1") is True
    # Loaded once per process (the autouse fixture resets the flag per test).
    registry.can_read(kb, student)
    assert imported == ["tests.fake_reader_module"]


def test_migration_items_are_typed_and_idempotent():
    assert registry.list_migration_items() == []
    registry.add_migration_item("kb_made_public", "kb1", {"name": "atlas", "users": ["alice"]})
    registry.add_migration_item("connector_grant_disabled", "vault", {"users": ["bob"]})
    registry.add_migration_item("kb_made_public", "kb1", {"name": "atlas", "users": ["alice", "carol"]})

    items = registry.list_migration_items()
    assert [(item.kind, item.key) for item in items] == [
        ("kb_made_public", "kb1"),
        ("connector_grant_disabled", "vault"),
    ]
    assert items[0].data == {"name": "atlas", "users": ["alice", "carol"]}
    assert items[0].to_dict()["kind"] == "kb_made_public"


def test_partner_runtimes_read_through_partner_references(tmp_path, make_user):
    from deeptutor.services.partners.scope import partner_user_id

    kb = registry.register_kb(base_dir=tmp_path, name="notes", kind="uploaded", created_by="t1")
    registry.add_reference(kb.kb_id, referrer_kind="partner", referrer_id="ada", created_by="t1")
    assert registry.can_read(kb, make_user(partner_user_id("ada")))
    assert not registry.can_read(kb, make_user(partner_user_id("bob")))


def test_unregister_relocate_and_transfer(tmp_path):
    shared = registry.register_kb(
        base_dir=tmp_path, name="shared", kind="uploaded", created_by="gone", visibility="public"
    )
    referenced = registry.register_kb(
        base_dir=tmp_path, name="referenced", kind="uploaded", created_by="gone"
    )
    lonely = registry.register_kb(base_dir=tmp_path, name="lonely", kind="uploaded", created_by="gone")
    registry.add_reference(
        referenced.kb_id, referrer_kind="partner", referrer_id="ada", created_by="gone"
    )

    moved = registry.relocate_kb(lonely.kb_id, tmp_path / "elsewhere")
    assert moved.kb_id == lonely.kb_id
    assert moved.base_dir == str((tmp_path / "elsewhere").resolve())
    assert registry.relocate_base_dir(tmp_path / "elsewhere", tmp_path / "third") == 1
    assert registry.get_kb(lonely.kb_id).base_dir == str((tmp_path / "third").resolve())

    transferred = registry.transfer_ownership("gone", "local-admin")
    assert {record.kb_id for record in transferred} == {shared.kb_id, referenced.kb_id}
    assert registry.get_kb(lonely.kb_id).created_by == "gone"
    assert registry.get_kb(shared.kb_id).created_by == "local-admin"

    registry.unregister_kb(referenced.kb_id)
    assert registry.get_kb(referenced.kb_id) is None
    assert registry.list_referenced("partner", "ada") == []
    # A KB that disappears under a live reference leaves a "资料不可用" trace.
    [gone] = registry.list_unavailable_references("partner", "ada")
    assert (gone.kb_id, gone.kb_name, gone.reason) == (referenced.kb_id, "referenced", "kb_deleted")


def test_has_indexed_kbs_ignores_pure_connectors(tmp_path):
    assert registry.has_indexed_kbs() is False
    registry.register_kb(base_dir=tmp_path, name="vault", kind="obsidian", created_by="u1")
    assert registry.has_indexed_kbs() is False
    registry.register_kb(base_dir=tmp_path, name="linked", kind="linked", created_by="u1")
    assert registry.has_indexed_kbs() is True


def test_meta_round_trip():
    assert registry.get_meta("k") is None
    registry.set_meta("k", "v1")
    registry.set_meta("k", "v2")
    assert registry.get_meta("k") == "v2"
