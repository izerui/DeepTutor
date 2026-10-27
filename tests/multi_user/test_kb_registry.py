"""System knowledge-base registry: ids, visibility, references, access."""

from __future__ import annotations

import pytest

from deeptutor.knowledge import registry


def test_documents_get_stable_ids_and_versions(tmp_path):
    kb = registry.register_kb(
        base_dir=tmp_path, name="physics", kind="uploaded", created_by="t1"
    )
    first = {d.rel_path: d for d in registry.upsert_documents(
        kb.ref, {"a.md": "sha256:1", "b.md": "sha256:2"}
    )}
    assert first["a.md"].version == 1
    changed = registry.upsert_documents(kb.kb_id, {"a.md": "sha256:changed"})[0]
    assert changed.doc_id == first["a.md"].doc_id and changed.version == 2
    assert registry.upsert_documents(kb.ref, {"a.md": "sha256:changed"}) == [changed]
    assert [d.rel_path for d in registry.sync_documents(
        kb.ref, {"a.md": "sha256:changed"}
    )] == ["a.md"]
    assert registry.find_document(kb.ref, "b.md").deleted
    revived = registry.upsert_documents(kb.ref, {"b.md": "sha256:2"})[0]
    assert revived.doc_id == first["b.md"].doc_id
    assert revived.version == 2 and not revived.deleted
    registry.move_document_paths(kb.ref, "a.md", "folder/a.md")
    registry.move_document_paths(kb.ref, "folder", "archive/folder")
    moved = registry.find_document(kb.ref, "archive/folder/a.md")
    assert moved.doc_id == changed.doc_id and moved.version == 2
    assert registry.mark_document_deleted(kb.ref, moved.rel_path).deleted
    assert len(registry.list_documents(kb.ref, include_deleted=True)) == 2


def test_document_referrers_are_doc_level_only(tmp_path):
    kb = registry.register_kb(
        base_dir=tmp_path, name="physics", kind="uploaded", created_by="t1"
    )
    doc = registry.upsert_documents(kb.ref, {"a.md": "sha256:1"})[0]
    for name, ids in [("whole", None), ("chapter", [doc.doc_id]), ("empty", [])]:
        registry.add_reference(
            kb.ref, referrer_kind="mastery_template", referrer_id=name,
            doc_ids=ids, created_by="t1",
        )
    assert [r.referrer_id for r in registry.document_referrers(kb.ref, doc.doc_id)] == [
        "chapter"
    ]


def test_document_moves_are_literal_and_collision_is_atomic(tmp_path):
    kb = registry.register_kb(
        base_dir=tmp_path, name="physics", kind="uploaded", created_by="t1"
    )
    registry.upsert_documents(kb.ref, {
        "a%_/one.md": "sha256:1", "a%_/two.md": "sha256:2",
        "abc/one.md": "sha256:3", "dest/two.md": "sha256:4",
        "dest/one.md": "sha256:old",
    })
    registry.mark_document_deleted(kb.ref, "dest/one.md")
    before = registry.list_documents(kb.ref, include_deleted=True)
    with pytest.raises(registry.KbRegistryError):
        registry.move_document_paths(kb.ref, "a%_", "dest")
    assert registry.list_documents(kb.ref, include_deleted=True) == before
    registry.move_document_paths(kb.ref, "a%_", "new")
    assert registry.find_document(kb.ref, "abc/one.md")
    assert registry.find_document(kb.ref, "new/one.md")


def test_document_reads_do_not_create_database(tmp_path):
    assert registry.find_document("absent", "a.md") is None
    assert registry.list_documents("absent") == []
    assert registry.document_referrers("absent", "doc") == []
    assert registry.refresh_documents(tmp_path, "absent") == []
    assert not registry._db_path().exists()


def test_document_history_is_additive_and_keeps_revival_authority(tmp_path):
    import sqlite3

    kb = registry.register_kb(
        base_dir=tmp_path, name="physics", kind="uploaded", created_by="t1"
    )
    moving, old = registry.upsert_documents(kb.ref, {
        "source.md": "sha256:source", "dest.md": "sha256:old",
    })
    old = registry.mark_document_deleted(kb.ref, old.rel_path)
    # Simulate the previously shipped schema with its existing rows and UNIQUE.
    with sqlite3.connect(registry._db_path()) as conn:
        conn.execute("DROP TABLE kb_document_history")
    assert registry.find_document(kb.ref, "dest.md") == old
    registry.move_document_paths(kb.ref, "source.md", "dest.md")
    assert old in registry.list_documents(kb.ref, include_deleted=True)
    registry.mark_document_deleted(kb.ref, "dest.md")
    [revived] = registry.upsert_documents(kb.ref, {"dest.md": "sha256:revived"})
    assert revived.doc_id == moving.doc_id and revived.version == moving.version + 1
    assert old in registry.list_documents(kb.ref, include_deleted=True)
    # Repeated displacement must retain all deleted identities, even at one path.
    deleted = registry.mark_document_deleted(kb.ref, "dest.md")
    [third] = registry.upsert_documents(kb.ref, {"third.md": "sha256:third"})
    registry.move_document_paths(kb.ref, "third.md", "dest.md")
    assert registry.find_document(kb.ref, "dest.md").doc_id == third.doc_id
    history = registry.list_documents(kb.ref, include_deleted=True)
    assert old in history and deleted in history and len(history) == 3
    registry.move_document_paths(kb.ref, "dest.md", "elsewhere.md")
    assert registry.find_document(kb.ref, "dest.md") is None
    assert old in registry.list_documents(kb.ref, include_deleted=True)
    registry.unregister_kb(kb.ref)
    assert registry.list_documents(kb.ref, include_deleted=True) == []


def test_document_refresh_hashes_renames_and_connector_contract(tmp_path):
    import hashlib

    raw = tmp_path / "physics/raw"
    raw.mkdir(parents=True)
    (raw / "a.md").write_bytes(b"first")
    kb = registry.register_kb(
        base_dir=tmp_path, name="physics", kind="uploaded", created_by="t1"
    )
    [first] = registry.refresh_documents(tmp_path, "physics")
    assert first.content_hash == "sha256:" + hashlib.sha256(b"first").hexdigest()
    (raw / "a.md").rename(raw / "b.md")
    [renamed] = registry.refresh_documents(tmp_path, "physics")
    assert renamed.doc_id != first.doc_id
    assert registry.find_document(kb.ref, "a.md").deleted
    (raw / "b.md").unlink()
    assert registry.refresh_documents(tmp_path, "physics") == []
    assert registry.find_document(kb.ref, "b.md").deleted
    connector = registry.register_kb(
        base_dir=tmp_path, name="vault", kind="linked", created_by="t1"
    )
    (tmp_path / "vault/raw").mkdir(parents=True)
    (tmp_path / "vault/raw/a.md").write_text("connector")
    assert registry.refresh_documents(tmp_path, "vault") == []
    assert registry.list_documents(connector.ref) == []
    registry.unregister_kb(kb.ref)
    assert registry.list_documents(kb.ref, include_deleted=True) == []


def test_document_hashing_does_not_read_escaped_symlinks(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (tmp_path / "private.md").write_text("private")
    (raw / "escape.md").symlink_to(tmp_path / "private.md")
    (raw / "own.md").write_text("own")
    assert list(registry.document_hashes(raw)) == ["own.md"]


@pytest.mark.parametrize("bad_path", ["../a.md", "/a.md", "a//b.md", "./a.md", "."])
def test_document_upsert_rejects_path_aliases_atomically(tmp_path, bad_path):
    kb = registry.register_kb(
        base_dir=tmp_path, name="physics", kind="uploaded", created_by="t1"
    )
    with pytest.raises(registry.KbRegistryError):
        registry.upsert_documents(kb.ref, {"valid.md": "sha256:1", bad_path: "sha256:2"})
    assert registry.list_documents(kb.ref) == []


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


@pytest.mark.parametrize("revoked", [False, True])
def test_add_reference_guard_preserves_state_and_excludes_concurrent_writes(tmp_path, revoked):
    import sqlite3

    kb = registry.register_kb(
        base_dir=tmp_path, name="physics", kind="uploaded", created_by="owner",
        visibility="public",
    )
    registry.add_reference(
        kb.kb_id, referrer_kind="partner", referrer_id="ada", created_by="alice",
        doc_ids=["original"],
    )
    if revoked:
        registry.set_visibility(kb.kb_id, "private", force=True)
    before_live = registry.list_references(kb.kb_id)
    before_revoked = registry.list_unavailable_references("partner", "ada")
    targets = []

    def reject(target):
        targets.append(target)
        # A separate SQLite writer cannot withdraw visibility during validation.
        with sqlite3.connect(registry._db_path(), timeout=0) as concurrent:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                concurrent.execute("BEGIN IMMEDIATE")
        raise registry.KbReferenceError("delegation rejected")

    with pytest.raises(registry.KbReferenceError, match="delegation rejected"):
        registry.add_reference(
            kb.kb_id, referrer_kind="partner", referrer_id="ada", created_by="bob",
            validate_target=reject,
        )
    assert targets == [registry.get_kb(kb.ref)]
    assert targets[0].visibility == ("private" if revoked else "public")
    assert registry.list_references(kb.kb_id) == before_live
    assert registry.list_unavailable_references("partner", "ada") == before_revoked


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


def test_restored_references_are_rechecked_inside_the_write(tmp_path):
    """Released targets asked back are judged on fresh records; failures drop, never raise."""
    kept = registry.register_kb(base_dir=tmp_path, name="kept", kind="uploaded", created_by="t1")
    back = registry.register_kb(base_dir=tmp_path, name="back", kind="uploaded", created_by="t1")
    refused = registry.register_kb(base_dir=tmp_path, name="no", kind="uploaded", created_by="t2")
    seen: list[str] = []

    def restorable(record):
        seen.append(record.kb_id)
        return record.created_by == "t1"

    registry.replace_references(
        "mastery_template",
        "tpl1",
        [(kept.kb_id, None), (back.kb_id, None), (refused.kb_id, None), ("gone", None)],
        created_by="t1",
        restore_kb_ids=[back.kb_id, refused.ref, "gone"],
        restorable=restorable,
    )
    assert sorted(seen) == sorted([back.kb_id, refused.kb_id])
    assert {r.kb_id for r in registry.list_referenced("mastery_template", "tpl1")} == {
        kept.kb_id,
        back.kb_id,
    }


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


# ── Fix round 1: privilege escalation & TOCTOU ──────────────────────


def test_student_resolving_admin_kb_does_not_become_owner(mu_isolated_root, as_user, make_user, monkeypatch):
    """A student resolving an assigned admin KB must not gain write access."""
    from types import SimpleNamespace

    from deeptutor.multi_user.knowledge_access import admin_kb_base_dir
    from deeptutor.multi_user.models import LOCAL_ADMIN_ID

    admin_base = admin_kb_base_dir()
    # Pre-register the KB under the admin so it exists on disk; then
    # remove the registry entry to simulate "not yet registered".
    record = registry.register_kb(
        base_dir=admin_base, name="algebra", kind="uploaded", created_by=LOCAL_ADMIN_ID
    )
    registry.unregister_kb(record.kb_id)

    # Monkeypatch resolve_kb to return a read-only admin resource.
    fake_resource = SimpleNamespace(
        base_dir=admin_base, name="algebra", read_only=True,
    )
    import deeptutor.multi_user.knowledge_access as ka_mod
    monkeypatch.setattr(ka_mod, "resolve_kb", lambda ref, require_write=False: fake_resource)

    # Also patch KnowledgeBaseManager.get_kb_entry used by ensure_registered
    monkeypatch.setattr(
        "deeptutor.knowledge.manager.KnowledgeBaseManager.get_kb_entry",
        lambda self, name: {"rag_provider": "llamaindex"},
    )

    student = make_user("s1", role="student")
    with as_user("s1", role="student"):
        resolved = registry.resolve_ref("admin:kb:algebra")

    assert resolved is not None
    assert resolved.created_by == LOCAL_ADMIN_ID
    assert not registry.can_write(resolved, student)


def test_owner_resolving_own_legacy_ref_autoregisters(mu_isolated_root, as_user, make_user, monkeypatch):
    """An owner resolving their own legacy ref auto-registers with themselves."""
    from types import SimpleNamespace

    from deeptutor.multi_user import paths

    user_base = mu_isolated_root / "data" / "users" / "t1" / "knowledge_bases"
    user_base.mkdir(parents=True, exist_ok=True)

    fake_resource = SimpleNamespace(
        base_dir=user_base, name="my_notes", read_only=False,
    )
    import deeptutor.multi_user.knowledge_access as ka_mod
    monkeypatch.setattr(ka_mod, "resolve_kb", lambda ref, require_write=False: fake_resource)

    # Also patch KnowledgeBaseManager.get_kb_entry used by ensure_registered
    monkeypatch.setattr(
        "deeptutor.knowledge.manager.KnowledgeBaseManager.get_kb_entry",
        lambda self, name: {"rag_provider": "llamaindex"},
    )

    owner = make_user("t1", role="teacher")
    with as_user("t1", role="teacher"):
        resolved = registry.resolve_ref("my_notes")

    assert resolved is not None
    assert resolved.created_by == "t1"
    assert registry.can_write(resolved, owner)


def test_set_visibility_force_revokes_concurrent_reference(tmp_path):
    """A reference added just before force-private is revoked in the same transaction."""
    kb = registry.register_kb(
        base_dir=tmp_path, name="shared", kind="uploaded", created_by="t1", visibility="public"
    )
    # Add a foreign reference right before force-private.
    registry.add_reference(kb.kb_id, referrer_kind="course", referrer_id="c1", created_by="t2")
    result = registry.set_visibility(kb.kb_id, "private", force=True)
    assert result.visibility == "private"
    # The reference was revoked inside the transaction.
    assert registry.list_references(kb.kb_id) == []
    [revoked] = registry.list_unavailable_references("course", "c1")
    assert revoked.reason == "made_private"


def test_guarded_replacement_holds_sqlite_write_lock_during_read_check(tmp_path, make_user, monkeypatch):
    import sqlite3

    kb = registry.register_kb(
        base_dir=tmp_path, name="shared", kind="uploaded", created_by="t1", visibility="public"
    )
    user = make_user("u2")
    can_read = registry.can_read
    checked = []

    def check_while_writer_attempts(record, reader):
        # An independent connection bypasses the process RLock and proves the
        # SQLite transaction itself excludes visibility/revocation writers.
        conn = sqlite3.connect(registry._db_path(), timeout=0, isolation_level=None)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                conn.execute("BEGIN IMMEDIATE")
        finally:
            conn.close()
        checked.append(record.kb_id)
        return can_read(record, reader)

    monkeypatch.setattr(registry, "can_read", check_while_writer_attempts)
    registry.replace_references(
        "course", "c1", [(kb.ref, None)], created_by=user.id, readable_by=user,
    )
    assert checked == [kb.kb_id]
    with pytest.raises(registry.KbVisibilityError, match="other accounts"):
        registry.set_visibility(kb.kb_id, "private")
    registry.set_visibility(kb.kb_id, "private", force=True)
    assert registry.list_references(kb.kb_id) == []
    assert registry.list_unavailable_references("course", "c1")[0].reason == "made_private"


def test_guarded_replacement_preserves_live_and_revoked_rows_on_rejection(tmp_path, make_user):
    own = registry.register_kb(base_dir=tmp_path, name="own", kind="uploaded", created_by="u2")
    shared = registry.register_kb(
        base_dir=tmp_path, name="shared", kind="uploaded", created_by="t1", visibility="public"
    )
    registry.replace_references(
        "course", "c1", [(own.kb_id, None), (shared.kb_id, None)], created_by="u2",
    )
    registry.set_visibility(shared.kb_id, "private", force=True)
    live = registry.list_references(own.kb_id)
    revoked = registry.list_unavailable_references("course", "c1")
    with pytest.raises(registry.KbReferenceError, match="read"):
        registry.replace_references(
            "course", "c1", [(shared.kb_id, None)], created_by="u2", readable_by=make_user("u2"),
        )
    assert registry.list_references(own.kb_id) == live
    assert registry.list_references(shared.kb_id) == []
    assert registry.list_unavailable_references("course", "c1") == revoked
