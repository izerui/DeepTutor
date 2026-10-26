"""Book publication, preparation, canonical reading and cross-DB rollback."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import sqlite3
from types import SimpleNamespace

import pytest

from deeptutor.book import registry as books
from deeptutor.knowledge import registry as kbs
from deeptutor.multi_user.context import get_current_user


@pytest.fixture
def library(tmp_path, mu_isolated_root):
    public = kbs.register_kb(
        base_dir=tmp_path / "kbs", name="atlas", kind="uploaded",
        created_by="t1", visibility="public",
    )
    private = kbs.register_kb(
        base_dir=tmp_path / "kbs", name="answers", kind="uploaded", created_by="t1",
    )
    book = books.register_book(
        base_dir=tmp_path / "books", dir_name="book_waves", title="Waves", created_by="t1",
    )
    books.set_book_kb_links(book.book_id, {"atlas": public.kb_id, "answers": private.kb_id, "gone": ""})
    return book, public, private


def protected(book):
    return {kb.kb_id for kb in kbs.list_referenced("book", book.book_id)}


def test_plan_defaults_and_preparation_never_leak_protection(library, as_user):
    book, public, private = library
    with as_user("t1", role="teacher"):
        planned = books.plan_book_kbs(book.ref)
        assert planned.public_kbs == (public,)
        assert planned.private_kbs == (private,)
        assert planned.missing == ("gone",) and not planned.issues
        assert planned.to_dict()["private_kbs"] == [
            {"kb_id": private.kb_id, "name": "answers", "default": "make_public"},
        ]
        books.share_book_kbs(book.ref)
        assert not protected(book)
        assert kbs.get_kb(private.kb_id).visibility == "private"
        # Plan C may fail or never commit after preparing. Nothing to compensate.
        with pytest.raises(RuntimeError):
            books.share_book_kbs(book.ref)
            raise RuntimeError("later C publication failed")
        assert not protected(book)
        books.share_book_kbs(book.ref, {private.kb_id: "make_public"})
        assert kbs.get_kb(private.kb_id).visibility == "public"
        assert not protected(book)
        books.replace_book_references("template", "one", [book.ref], created_by="t1")
        assert protected(book) == {public.kb_id, private.kb_id}


def test_publish_default_reference_only_and_withdraw(library, as_user):
    book, public, private = library
    with as_user("t1", role="teacher"):
        assert books.set_book_visibility(book.ref, "public").visibility == "public"
        assert protected(book) == {public.kb_id, private.kb_id}
        assert kbs.get_kb(private.kb_id).visibility == "private"
        assert books.plan_book_kbs(book.ref).to_dict()["private_kbs"][0]["default"] == "reference_only"
        books.replace_book_references("template", "own", [book.ref], created_by="t1")
        books.set_book_visibility(book.ref, "private")
        assert protected(book)
        books.remove_book_references("template", "own")
        assert not protected(book)


def test_make_public_is_atomic_with_book_publication(library, as_user):
    book, public, private = library
    with as_user("t1", role="teacher"):
        books.set_book_visibility(book.ref, "public", kb_choices={private.kb_id: "make_public"})
    assert kbs.get_kb(private.kb_id).visibility == "public"
    assert protected(book) == {public.kb_id, private.kb_id}


def test_connectors_and_foreign_private_block_force_skips_connector(library, as_user, tmp_path):
    book, _, _ = library
    vault = kbs.register_kb(
        base_dir=tmp_path / "kbs", name="vault", kind="obsidian", created_by="t1",
    )
    foreign = kbs.register_kb(
        base_dir=tmp_path / "other", name="secret", kind="uploaded", created_by="t2",
    )
    books.set_book_kb_links(book.ref, {"vault": vault.kb_id, "secret": foreign.kb_id})
    with as_user("t1", role="teacher"):
        assert [i.code for i in books.plan_book_kbs(book.ref).issues] == ["connector", "foreign_private"]
        with pytest.raises(books.BookVisibilityError, match="vault"):
            books.set_book_visibility(book.ref, "public")
        with pytest.raises(books.BookReferenceError, match="vault"):
            books.replace_book_references("template", "one", [book.ref], created_by="t1")
    assert books.get_book(book.ref).visibility == "private"
    assert not books.list_book_references(book.ref) and not protected(book)
    with as_user("root", role="admin"):
        books.set_book_visibility(book.ref, "public", force=True)
    assert protected(book) == {foreign.kb_id}
    assert kbs.get_kb(vault.kb_id).visibility == "private"


@pytest.mark.parametrize("role", ["user", "student"])
def test_publish_role_and_force_permissions(library, as_user, role):
    book, _, private = library
    with as_user("t1", role=role):
        with pytest.raises(books.BookVisibilityError, match="teachers"):
            books.set_book_visibility(book.ref, "public")
        with pytest.raises(books.BookVisibilityError, match="teachers"):
            books.share_book_kbs(book.ref, {private.kb_id: "make_public"})
        with pytest.raises(books.BookVisibilityError, match="admins"):
            books.share_book_kbs(book.ref, force=True)
        books.share_book_kbs(book.ref)
        with pytest.raises(books.BookVisibilityError, match="Unknown"):
            books.share_book_kbs(book.ref, {"unrelated": "everyone"})
    with as_user("foreign", role="teacher"):
        with pytest.raises(books.BookVisibilityError, match="owner"):
            books.share_book_kbs(book.ref)
        with pytest.raises(books.BookVisibilityError, match="owner"):
            books.set_book_visibility(book.ref, "public")
        with pytest.raises(books.BookReferenceError, match="actor"):
            books.replace_book_references("template", "forged", [book.ref], created_by="t1")
    assert not protected(book)


def test_forced_withdraw_revokes_only_foreign_then_delete_keeps_evidence(library, as_user):
    book, _, _ = library
    with as_user("t1", role="teacher"):
        books.set_book_visibility(book.ref, "public")
        books.replace_book_references("template", "own", [book.ref], created_by="t1")
        books.replace_book_references("template", "other", [book.ref], created_by="t2")
        with pytest.raises(books.BookVisibilityError, match="other accounts"):
            books.set_book_visibility(book.ref, "private")
        assert len(books.list_book_references(book.ref)) == 2
    with as_user("root", role="admin"):
        books.set_book_visibility(book.ref, "private", force=True)
        assert [ref.referrer_id for ref in books.list_book_references(book.ref)] == ["own"]
        assert books.list_unavailable_book_references("template", "other")[0].reason == "made_private"
        assert protected(book)
        books.unregister_book(book.ref)
    assert not protected(book) and books.get_book(book.ref) is None
    assert books.list_unavailable_book_references("template", "own")[0].reason == "book_deleted"
    assert books.list_unavailable_book_references("template", "other")[0].reason == "made_private"


def test_cached_builtin_actual_can_read_and_custom_precedence(library, as_user, monkeypatch):
    book, _, private = library
    with as_user("t1", role="teacher"):
        books.set_book_visibility(book.ref, "public")
    monkeypatch.setattr(kbs, "_readers", {})
    monkeypatch.setattr(kbs, "_builtin_readers_loaded", False)
    with as_user("s1", role="student"):
        user = get_current_user()
        assert not kbs.can_read(private, user)
        with books.reading_book(book.ref):
            assert kbs.can_read(private, user)
            assert not kbs.can_write(private, user)
        assert not kbs.can_read(private, user)
        custom = lambda *_: False
        kbs.register_reference_reader("book", custom)
        kbs._builtin_readers_loaded = False
        books.register_kb_reader()
        with books.reading_book(book.ref):
            assert not kbs.can_read(private, user)
        assert kbs._readers["book"] is custom


def test_failed_builtin_hook_rolls_back_and_retries(library, as_user, monkeypatch):
    book, _, private = library
    with as_user("t1", role="teacher"):
        books.set_book_visibility(book.ref, "public")
    monkeypatch.setattr(kbs, "_readers", {})
    monkeypatch.setattr(kbs, "_builtin_readers_loaded", False)
    attempts = []

    def hook():
        kbs.register_reference_reader("book", books.book_kb_reader)
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("broken hook")

    # One builtin module: the stand-in import answers every name with this hook.
    monkeypatch.setattr(kbs, "BUILTIN_READER_MODULES", ("deeptutor.book.registry",))
    monkeypatch.setattr(kbs.importlib, "import_module", lambda _: SimpleNamespace(register_kb_reader=hook))
    with as_user("s1", role="student"), books.reading_book(book.ref):
        assert not kbs.can_read(private, get_current_user())
        assert "book" not in kbs._readers
        assert kbs.can_read(private, get_current_user())
    assert len(attempts) == 2


def test_read_scope_canonical_actor_revocation_and_forgery(library, as_user, tmp_path):
    book, _, private = library
    with as_user("s1", role="student"), books.reading_book(book.ref):
        assert not kbs.can_read(private, get_current_user())
    with as_user("t1", role="teacher"):
        books.set_book_visibility(book.ref, "public")
    with as_user("s1", role="student"), books.reading_book(book.ref):
        assert kbs.can_read(private, get_current_user())
        with as_user("s2", role="student"):
            assert not kbs.can_read(private, get_current_user())
        collision = replace(book, base_dir=str(tmp_path / "collision"), created_by="s1")
        assert not books.can_read_book(collision, get_current_user())
        books.relocate_book_dir(book.base_dir, tmp_path / "moved")
        assert not kbs.can_read(private, get_current_user())
    with as_user("s1", role="student"):
        token = books.active_books.set(frozenset({book.book_id}))
        try:
            assert not kbs.can_read(private, get_current_user())
        finally:
            books.active_books.reset(token)
        with books.reading_book(book.ref):
            assert kbs.can_read(private, get_current_user())
            with as_user("t1", role="teacher"):
                books.set_book_visibility(book.ref, "private")
            assert not kbs.can_read(private, get_current_user())


@pytest.mark.asyncio
async def test_read_scope_survives_to_thread_and_resets_on_error(library, as_user):
    book, _, private = library
    with as_user("t1", role="teacher"):
        books.set_book_visibility(book.ref, "public")
    with as_user("s1", role="student"):
        with pytest.raises(RuntimeError), books.reading_book(book.ref):
            assert await asyncio.to_thread(kbs.can_read, private, get_current_user())
            with books.reading_book("missing"):
                assert books.book_kb_reader(book.book_id, "s1")
            raise RuntimeError("cancel turn")
        assert not books.active_books.get()
        assert not kbs.can_read(private, get_current_user())


def test_failed_second_target_and_cleanup_keep_old_sets(library, tmp_path, as_user):
    book, _, private = library
    other = books.register_book(
        base_dir=tmp_path / "books", dir_name="book_zbad", title="Bad", created_by="t1",
    )
    connector = kbs.register_kb(
        base_dir=tmp_path / "kbs", name="vault", kind="obsidian", created_by="t1",
    )
    books.set_book_kb_links(other.ref, {"vault": connector.kb_id})
    with as_user("t1", role="teacher"):
        books.replace_book_references("template", "one", [book.ref], created_by="t1")
        before = protected(book)
        with pytest.raises(books.BookReferenceError):
            books.replace_book_references("template", "one", [book.ref, other.ref], created_by="t1")
        assert [r.book_id for r in books.list_referenced_books("template", "one")] == [book.book_id]
        assert protected(book) == before and not protected(other)
        with kbs._write() as conn:
            conn.execute(
                "CREATE TRIGGER fail_cleanup BEFORE DELETE ON kb_references "
                "BEGIN SELECT RAISE(ABORT, 'cleanup failed'); END"
            )
        with pytest.raises(sqlite3.IntegrityError, match="cleanup failed"):
            books.remove_book_references("template", "one")
        assert protected(book) == before and books.list_book_references(book.ref)
        assert kbs.get_kb(private.kb_id).visibility == "private"


def test_book_commit_failure_rolls_back_kb_publication(library, as_user):
    book, _, private = library
    # A deferred FK fails at COMMIT, after book, KB and protection writes.
    with books._write() as conn:
        conn.execute("CREATE TABLE commit_parent(id TEXT PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE commit_child(id TEXT REFERENCES commit_parent(id) DEFERRABLE INITIALLY DEFERRED)"
        )
        conn.execute(
            "CREATE TRIGGER fail_commit AFTER UPDATE OF visibility ON book_registry "
            "BEGIN INSERT INTO commit_child VALUES ('missing'); END"
        )
    with as_user("t1", role="teacher"), pytest.raises(sqlite3.IntegrityError):
        books.set_book_visibility(book.ref, "public", kb_choices={private.kb_id: "make_public"})
    assert books.get_book(book.ref).visibility == "private"
    assert kbs.get_kb(private.kb_id).visibility == "private"
    assert not protected(book)


@pytest.mark.parametrize("database", ["book", "kb"])
def test_no_nested_writer_and_unsupported_journal_fails_closed(library, as_user, database):
    book, _, _ = library
    with books._write(), pytest.raises(books.BookRegistryError, match="active book writer"):
        books.share_book_kbs(book.ref)
    conn = sqlite3.connect(books._db_path() if database == "book" else kbs._db_path())
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    finally:
        conn.close()
    with as_user("t1", role="teacher"), pytest.raises(books.BookRegistryError, match="DELETE"):
        books.set_book_visibility(book.ref, "public")
    assert books.get_book(book.ref).visibility == "private" and not protected(book)


def test_shared_transaction_uses_disk_main_and_full_delete_journals(library):
    from deeptutor.book.sharing import shared_write

    with shared_write() as conn:
        databases = {row["name"]: row["file"] for row in conn.execute("PRAGMA database_list")}
        assert databases == {
            "main": str(kbs._db_path().resolve()),
            "books": str(books._db_path().resolve()),
        }
        assert all(databases.values())
        for schema in ("main", "books"):
            assert conn.execute(f"PRAGMA {schema}.journal_mode").fetchone()[0] == "delete"
            assert conn.execute(f"PRAGMA {schema}.synchronous").fetchone()[0] == 2


def test_resolution_exception_is_retryable_and_not_empty(library, monkeypatch):
    _, _, private = library
    assert books.resolve_kb_links([private.ref, "kb:gone", private.ref]) == {
        private.ref: private.kb_id, "kb:gone": "",
    }
    monkeypatch.setattr(kbs, "resolve_ref", lambda _: (_ for _ in ()).throw(OSError("temporary")))
    with pytest.raises(books.BookKbResolutionError):
        books.resolve_kb_links(["answers"])


def test_resolution_http_failure_is_not_confirmed_missing(library, monkeypatch):
    from fastapi import HTTPException
    from deeptutor.multi_user import knowledge_access

    def unavailable(_):
        raise HTTPException(status_code=503, detail="catalog temporarily unavailable")

    monkeypatch.setattr(knowledge_access, "resolve_kb", unavailable)
    with pytest.raises(books.BookKbResolutionError):
        books.resolve_kb_links(["answers"])


def test_initializer_preserves_ids_retries_pending_and_requires_owner_scope(
    library, as_user, monkeypatch, tmp_path,
):
    from deeptutor.services import path_service

    book, public, private = library
    monkeypatch.setattr(path_service, "get_path_service", lambda: SimpleNamespace(get_book_dir=lambda: book.base_dir))
    calls = []

    def resolve(ref):
        calls.append((get_current_user().id, ref))
        if ref == "pending":
            raise OSError("catalog not ready")
        return private if ref == "answers" else None

    monkeypatch.setattr(kbs, "resolve_ref", resolve)
    with as_user("t1", role="teacher"):
        assert not books.initialize_book_kb_links(book.ref, ["atlas", "answers", "pending", "notyet"])
        assert books.book_kb_links(book.ref)["answers"] == private.kb_id
        assert not books.kb_links_initialized(book.ref)
        with pytest.raises(books.BookKbResolutionError):
            books.set_book_visibility(book.ref, "public")
    with as_user("s1", role="student"), pytest.raises(books.BookKbResolutionError, match="owner"):
        books.initialize_book_kb_links(book.ref, ["pending"])
    kbs.unregister_kb(private.kb_id)
    replacement = kbs.register_kb(
        base_dir=tmp_path / "replacement", name="answers", kind="uploaded", created_by="t1",
    )
    monkeypatch.setattr(kbs, "resolve_ref", lambda ref: public if ref == "pending" else replacement)
    with as_user("t1", role="teacher"):
        assert books.initialize_book_kb_links(book.ref, ["atlas", "answers", "pending", "notyet"])
        assert books.kb_links_initialized(book.ref)
    links = books.book_kb_links(book.ref)
    assert links["answers"] == private.kb_id and links["pending"] == public.kb_id
    assert calls == [("t1", "pending"), ("t1", "notyet")]


def test_pending_names_cannot_be_forgotten_or_confirmed_missing_rebound(library, as_user, monkeypatch):
    from deeptutor.services import path_service

    book, _, private = library
    monkeypatch.setattr(path_service, "get_path_service", lambda: SimpleNamespace(get_book_dir=lambda: book.base_dir))
    monkeypatch.setattr(kbs, "resolve_ref", lambda _: None)
    with as_user("t1", role="teacher"):
        assert not books.initialize_book_kb_links(book.ref, ["later"])
        assert not books.initialize_book_kb_links(book.ref, [])
        assert books.initialize_book_kb_links(book.ref, [], confirmed_missing=["later"])
        assert books.book_kb_links(book.ref)["later"] == ""
        monkeypatch.setattr(kbs, "resolve_ref", lambda _: private)
        assert books.initialize_book_kb_links(book.ref, ["later"])
        assert books.book_kb_links(book.ref)["later"] == ""


def test_new_reference_reader_cannot_prepare_foreign_private_kb(library, as_user):
    book, _, private = library
    with as_user("t1", role="teacher"):
        books.replace_book_references("template", "one", [book.ref], created_by="t1")
    books.register_book_reference_reader("template", lambda ref, user: ref == "one" and user == "s1")
    with as_user("s1", role="student"):
        books.replace_book_references("template", "two", [book.ref], created_by="s1")
        with pytest.raises(books.BookVisibilityError, match="owner"):
            books.share_book_kbs(book.ref, {private.kb_id: "make_public"})
        with books.reading_book(book.ref):
            assert kbs.can_read(private, get_current_user())
    assert kbs.get_kb(private.kb_id).visibility == "private"


def test_d1_database_advisory_read_does_not_migrate(library, as_user):
    book, _, private = library
    with books._write() as conn:
        conn.execute("DROP TABLE book_kb_pending")
        conn.execute("DROP TABLE book_kb_binding_state")
    with as_user("t1", role="teacher"):
        assert books.plan_book_kbs(book.ref).private_kbs == (private,)
        assert not books.kb_links_initialized(book.ref)
    with books._read() as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'book_kb_pending'"
        ).fetchone() is None


def test_plan_missing_database_and_reading_do_not_create_files(as_user):
    assert not books._db_path().exists() and not kbs._db_path().exists()
    with as_user("s1", role="student"), books.reading_book("missing"):
        assert not books.active_books.get()
        with pytest.raises(books.BookRegistryError):
            books.plan_book_kbs("missing")
        assert not books.kb_links_initialized("missing")
    assert not books._db_path().exists() and not kbs._db_path().exists()


def test_link_update_failure_preserves_shared_binding(library, as_user, tmp_path):
    book, _, private = library
    vault = kbs.register_kb(
        base_dir=tmp_path / "kbs", name="vault", kind="obsidian", created_by="t1",
    )
    with as_user("t1", role="teacher"):
        books.set_book_visibility(book.ref, "public")
        old = books.book_kb_links(book.ref)
        with pytest.raises(books.BookReferenceError):
            books.set_book_kb_links(book.ref, {"vault": vault.kb_id})
    assert books.book_kb_links(book.ref) == old
    assert private.kb_id in protected(book)


def test_passive_consumer_changes_do_not_restore_admin_kb_withdrawal(library, as_user, tmp_path):
    book, public, _ = library
    # Foreign owner means force-private revokes this book's KB reference.
    foreign = kbs.register_kb(
        base_dir=tmp_path / "other", name="foreign", kind="uploaded",
        visibility="public", created_by="t2",
    )
    books.set_book_kb_links(book.ref, {"foreign": foreign.kb_id, "atlas": public.kb_id})
    with as_user("t1", role="teacher"):
        books.set_book_visibility(book.ref, "public")
        books.replace_book_references("template", "one", [book.ref], created_by="t1")
    kbs.set_visibility(foreign.kb_id, "private", force=True)
    with as_user("root", role="admin"):
        books.replace_book_references("template", "two", [book.ref], created_by="t1")
        books.remove_book_references("template", "one")
        books.set_book_visibility(book.ref, "private")
    assert protected(book) == {public.kb_id}
    assert kbs.list_unavailable_references("book", book.book_id)[0].reason == "made_private"


def test_forced_public_connector_stays_unavailable_during_consumer_changes(library, as_user, tmp_path):
    book, _, _ = library
    connector = kbs.register_kb(
        base_dir=tmp_path / "kbs", name="vault", kind="obsidian", created_by="t1",
    )
    books.set_book_kb_links(book.ref, {"vault": connector.kb_id})
    with as_user("root", role="admin"):
        books.set_book_visibility(book.ref, "public", force=True)
    with as_user("s1", role="student"):
        books.replace_book_references("template", "one", [book.ref], created_by="s1")
        books.remove_book_references("template", "one")
    assert not protected(book)


def test_initial_resolution_holds_no_global_sqlite_writer(library, as_user, monkeypatch):
    from deeptutor.services import path_service

    book, _, private = library
    monkeypatch.setattr(path_service, "get_path_service", lambda: SimpleNamespace(get_book_dir=lambda: book.base_dir))
    calls = []

    def resolve(_):
        for path in (books._db_path(), kbs._db_path()):
            conn = sqlite3.connect(path, timeout=0, isolation_level=None)
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("ROLLBACK")
                calls.append(path)
            finally:
                conn.close()
        return private

    monkeypatch.setattr(kbs, "resolve_ref", resolve)
    with as_user("t1", role="teacher"):
        assert books.initialize_book_kb_links(book.ref, ["new"])
    assert calls == [books._db_path(), kbs._db_path()]


@pytest.mark.parametrize("actor,role", [("t1", "teacher"), ("root", "admin"), ("t1", "user")])
@pytest.mark.parametrize("operation", ["prepare", "publish"])
def test_explicit_reference_only_records_only_actual_private_targets(
    library, as_user, tmp_path, actor, role, operation,
):
    book, public, private = library
    unrelated = kbs.register_kb(
        base_dir=tmp_path / "kbs", name="unrelated", kind="uploaded", created_by="t1",
    )
    choices = {kb.kb_id: "reference_only" for kb in (public, private, unrelated)}
    with as_user(actor, role=role):
        if operation == "publish" and role == "user":
            with pytest.raises(books.BookVisibilityError, match="teachers"):
                books.set_book_visibility(book.ref, "public", kb_choices=choices)
            assert kbs.get_meta("visibility_intent_v1:" + private.kb_id) is None
            assert not protected(book)
            return
        if operation == "publish":
            books.set_book_visibility(book.ref, "public", kb_choices=choices)
        else:
            books.share_book_kbs(book.ref, choices)
    assert kbs.get_meta("visibility_intent_v1:" + private.kb_id) == "private"
    assert books.get_meta("visibility_intent_v1:" + private.kb_id) is None
    assert kbs.get_kb(private.kb_id).visibility == "private"
    assert kbs.get_kb(public.kb_id).visibility == "public"
    for kb in (public, unrelated):
        assert kbs.get_meta("visibility_intent_v1:" + kb.kb_id) is None
    assert protected(book) == ({public.kb_id, private.kb_id} if operation == "publish" else set())


def test_reference_only_same_id_uses_separate_registry_schemas(library, as_user, tmp_path):
    _, _, private = library
    book = books.register_book(
        base_dir=tmp_path / "books", dir_name="book_" + private.kb_id,
        title="Same ID", created_by="t1",
    )
    assert book.book_id == private.kb_id
    books.set_book_kb_links(book.ref, {private.ref: private.kb_id})
    with as_user("t1", role="teacher"):
        books.set_book_visibility(book.ref, "public", kb_choices={private.kb_id: "reference_only"})
    key = "visibility_intent_v1:" + private.kb_id
    assert kbs.get_meta(key) == "private"
    assert books.get_meta(key) == "public"
    assert kbs.get_kb(private.kb_id).visibility == "private"
    assert books.get_book(book.ref).visibility == "public"


@pytest.mark.parametrize("boundary", ["intent", "protection", "commit"])
@pytest.mark.parametrize("prior_intent", [None, "public"])
def test_reference_only_actual_sql_failure_rolls_back_joint_state(
    library, as_user, monkeypatch, boundary, prior_intent,
):
    from deeptutor.book import sharing

    book, _, private = library
    key = "visibility_intent_v1:" + private.kb_id
    if prior_intent is not None:
        kbs.set_meta(key, prior_intent)
    # Preserve a pre-existing document-scoped reference through failed refresh.
    kbs.add_reference(
        private.kb_id, referrer_kind="book", referrer_id=book.book_id,
        created_by="t1", doc_ids=["doc-one"],
    )
    before = kbs.list_references(private.kb_id)
    if boundary == "intent":
        with kbs._write() as conn:
            conn.execute(
                "CREATE TRIGGER fail_intent BEFORE INSERT ON registry_meta "
                "WHEN NEW.value = 'private' BEGIN SELECT RAISE(ABORT, 'intent failed'); END"
            )
    elif boundary == "protection":
        with kbs._write() as conn:
            conn.execute(
                "CREATE TRIGGER fail_protection BEFORE INSERT ON kb_references "
                "BEGIN SELECT RAISE(ABORT, 'protection failed'); END"
            )
    else:
        with books._write() as conn:
            conn.execute("CREATE TABLE commit_parent(id TEXT PRIMARY KEY)")
            conn.execute(
                "CREATE TABLE commit_child("
                "id TEXT REFERENCES commit_parent(id) DEFERRABLE INITIALLY DEFERRED)"
            )
            conn.execute(
                "CREATE TRIGGER fail_commit AFTER UPDATE OF visibility ON book_registry "
                "BEGIN INSERT INTO commit_child VALUES ('missing'); END"
            )
    synced = []
    original = sharing.sync_protection

    def observe(conn, book_id, **kwargs):
        assert conn.in_transaction
        assert conn.execute(
            "SELECT value FROM main.registry_meta WHERE key = ?", (key,),
        ).fetchone()[0] == "private"
        assert conn.execute(
            "SELECT visibility FROM books.book_registry WHERE book_id = ?", (book_id,),
        ).fetchone()[0] == "public"
        original(conn, book_id, **kwargs)
        synced.append(book_id)

    with monkeypatch.context() as observation, as_user("t1", role="teacher"):
        observation.setattr(sharing, "sync_protection", observe)
        with pytest.raises(sqlite3.IntegrityError, match={
            "intent": "intent failed", "protection": "protection failed",
            "commit": "FOREIGN KEY constraint failed",
        }[boundary]):
            books.set_book_visibility(
                book.ref, "public", kb_choices={private.kb_id: "reference_only"},
            )
    assert synced == ([book.book_id] if boundary == "commit" else [])
    assert books.get_book(book.ref) == book
    assert kbs.get_kb(private.kb_id) == private
    assert kbs.get_meta(key) == prior_intent
    assert books.get_meta("visibility_intent_v1:" + book.book_id) is None
    assert books.get_meta(key) is None
    assert kbs.list_references(private.kb_id) == before


@pytest.mark.parametrize("failure", ["foreign_actor", "invalid", "force", "connector", "foreign_kb"])
def test_rejected_reference_choice_records_no_intent(library, as_user, tmp_path, failure):
    book, _, private = library
    choices = {private.kb_id: "reference_only"}
    if failure in {"connector", "foreign_kb"}:
        blocked = kbs.register_kb(
            base_dir=tmp_path / "kbs", name="blocked",
            kind="obsidian" if failure == "connector" else "uploaded", created_by="t2",
        )
        books.set_book_kb_links(book.ref, {"answers": private.kb_id, "blocked": blocked.kb_id})
        choices[blocked.kb_id] = "reference_only"
    elif failure == "invalid":
        choices["unrelated"] = "invalid"
    with as_user("foreign" if failure == "foreign_actor" else "t1", role="teacher"):
        with pytest.raises(books.BookVisibilityError):
            books.set_book_visibility(
                book.ref, "public", kb_choices=choices, force=failure == "force",
            )
    for kb_id in choices:
        assert kbs.get_meta("visibility_intent_v1:" + kb_id) is None
    assert books.get_book(book.ref) == book
    assert not protected(book)


def test_force_skipped_reference_target_has_no_intent_or_access(library, as_user, tmp_path):
    book, _, private = library
    connector = kbs.register_kb(
        base_dir=tmp_path / "kbs", name="vault", kind="obsidian", created_by="t1",
    )
    books.set_book_kb_links(book.ref, {"answers": private.kb_id, "vault": connector.kb_id})
    with as_user("root", role="admin"):
        books.set_book_visibility(book.ref, "public", force=True, kb_choices={
            private.kb_id: "reference_only", connector.kb_id: "reference_only",
        })
    assert protected(book) == {private.kb_id}
    assert kbs.get_meta("visibility_intent_v1:" + private.kb_id) == "private"
    assert kbs.get_meta("visibility_intent_v1:" + connector.kb_id) is None
    with as_user("s1", role="student"), books.reading_book(book.ref):
        assert not kbs.can_read(connector, get_current_user())
        assert not kbs.can_write(private, get_current_user())


def test_generic_reference_changes_preserve_absent_intent_and_other_doc_scope(library, as_user):
    book, _, private = library
    kbs.add_reference(
        private.kb_id, referrer_kind="template", referrer_id="limited",
        created_by="t1", doc_ids=["doc-one"],
    )
    limited = kbs.list_references(private.kb_id)
    with as_user("t1", role="teacher"):
        books.replace_book_references("template", "one", [book.ref], created_by="t1")
        books.set_book_kb_links(book.ref, books.book_kb_links(book.ref))
        assert kbs.get_meta("visibility_intent_v1:" + private.kb_id) is None
        books.share_book_kbs(book.ref, {private.kb_id: "reference_only"})
    assert [ref for ref in kbs.list_references(private.kb_id) if ref.referrer_kind == "template"] == limited
