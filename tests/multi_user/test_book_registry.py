"""Book registry identities, transaction boundaries, access and revocation."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import sqlite3
import subprocess
import sys
import types

import pytest

from deeptutor.book import registry


def _register(tmp_path, book_id="bk_1", *, owner="t1", visibility="private", where="a"):
    return registry.register_book(
        base_dir=tmp_path / where,
        dir_name=registry.dir_name_for(book_id),
        title=book_id.upper(),
        created_by=owner,
        visibility=visibility,
    )


def test_database_lives_under_dynamic_system_root(mu_isolated_root, monkeypatch):
    from deeptutor.multi_user import paths

    monkeypatch.setattr(registry, "DB_PATH_OVERRIDE", None)
    assert registry._db_path() == paths.SYSTEM_ROOT / "book_registry.sqlite3"
    _register(mu_isolated_root)
    assert registry._db_path().is_file()


def test_all_reads_leave_missing_database_and_parent_absent(tmp_path, monkeypatch):
    path = tmp_path / "missing" / "registry.sqlite3"
    monkeypatch.setattr(registry, "DB_PATH_OVERRIDE", path)
    assert registry.get_book("bk_missing") is None
    assert registry.resolve_book_ref("book:bk_missing") is None
    assert registry.find_book_by_location(tmp_path, "book_bk_missing") is None
    assert registry.list_book_records() == []
    assert registry.list_transferred_books("u1") == []
    assert registry.list_book_references("bk_missing") == []
    assert registry.list_referenced_books("course", "c1") == []
    assert registry.list_unavailable_book_references("course", "c1") == []
    assert registry.list_revoked_book_references("bk_missing") == []
    assert registry.book_kb_links("bk_missing") == {}
    assert not registry.has_kb_links("bk_missing")
    assert registry.kb_refs_for("bk_missing", ["raw"]) == ["raw"]
    assert registry.get_meta("k") is None
    assert not path.parent.exists()


def test_existing_database_reads_are_read_only(tmp_path):
    book = _register(tmp_path)
    with registry._read() as conn:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM book_registry")
    assert registry.get_book(book.ref) == book


def test_registration_is_idempotent_without_ownership_or_visibility_changes(tmp_path):
    first = _register(tmp_path)
    assert _register(tmp_path, owner="foreign", visibility="public") == first
    assert first.ref == "book:bk_1"
    assert first.dir_name == "book_bk_1"
    assert first.base_dir == str((tmp_path / "a").resolve())
    assert registry.get_book(first.ref) == first
    assert registry.resolve_book_ref(first.book_id) == first
    assert registry.find_book_by_location(tmp_path / "a" / ".." / "a", first.dir_name) == first
    with pytest.raises(registry.BookRegistryError, match="another location"):
        _register(tmp_path, owner="foreign", where="other", visibility="public")
    assert registry.get_book(first.book_id) == first


@pytest.mark.parametrize(
    "dir_name",
    ["notes", "book_", "book_../escape", "book_x/y", "book_x\\y", "book_..",
     "book_.", "book_ x", "book_x ", "book_x\x00y", "book_book:x"],
)
def test_invalid_directory_names_do_not_create_database(tmp_path, dir_name):
    with pytest.raises(registry.BookRegistryError):
        registry.register_book(base_dir=tmp_path, dir_name=dir_name, title="x", created_by="t1")
    assert not registry._db_path().exists()


def test_id_helpers_preserve_book_model_and_legacy_ids():
    from deeptutor.book.models import Book

    book = Book(title="Synthetic")
    assert registry.strip_ref(f"book:{book.id}") == book.id
    assert registry.dir_name_for(f"book:{book.id}") == f"book_{book.id}"
    assert registry.dir_name_for("legacy-id") == "book_legacy-id"
    assert registry.strip_ref(" book:legacy-id ") == "legacy-id"


def test_visibility_and_exception_contract(tmp_path):
    assert registry.VISIBILITIES == {"public", "private"}
    for error in (registry.BookRegistryError, registry.BookVisibilityError, registry.BookReferenceError):
        assert issubclass(error, ValueError)
    with pytest.raises(registry.BookVisibilityError):
        _register(tmp_path, visibility="shared")
    assert not registry._db_path().exists()


def test_sync_updates_titles_but_never_foreign_location_or_owner(tmp_path, caplog):
    foreign = _register(tmp_path, "bk_foreign", where="elsewhere")
    original = _register(tmp_path, visibility="public")
    records = registry.sync_book_dir(
        tmp_path / "a", {"bk_1": "Renamed", "bk_2": "Second", "bk_foreign": "Copy"},
        created_by="t2",
    )
    assert set(records) == {"bk_1", "bk_2"}
    assert records["bk_1"].title == "Renamed"
    assert records["bk_1"].created_by == original.created_by
    assert records["bk_1"].visibility == "public"
    assert records["bk_2"].created_by == "t2"
    assert records["bk_2"].visibility == "private"
    assert registry.get_book(foreign.ref) == foreign
    assert "another location" in caplog.text
    assert registry.list_book_records(visibility="public", created_by="t1", base_dir=tmp_path / "a") == [
        records["bk_1"]
    ]
    assert registry.sync_book_dir(tmp_path / "a", {}, created_by="t1") == {}


def test_sync_unchanged_books_does_not_open_writer(tmp_path, monkeypatch):
    book = _register(tmp_path)

    def unexpected_write():
        pytest.fail("unchanged directory sync must stay read-only")

    monkeypatch.setattr(registry, "_write", unexpected_write)
    assert registry.sync_book_dir(tmp_path / "a", {book.book_id: book.title}, created_by="t2") == {
        book.book_id: book
    }


def test_replace_references_validates_all_targets_before_clearing(tmp_path):
    _register(tmp_path)
    _register(tmp_path, "bk_2")
    registry.replace_book_references("course", "c1", ["bk_1", "book:bk_2", "bk_1"], created_by="t1")
    assert registry.list_book_references("bk_1")[0].to_dict() == {
        "book_id": "bk_1", "referrer_kind": "course", "referrer_id": "c1", "created_by": "t1",
    }
    before = registry.list_referenced_books("course", "c1")
    assert {r.book_id for r in before} == {"bk_1", "bk_2"}
    with pytest.raises(registry.BookReferenceError):
        registry.replace_book_references("course", "c1", ["bk_1", "missing"], created_by="t1")
    assert registry.list_referenced_books("course", "c1") == before
    registry.replace_book_references("course", "c1", ["bk_2"], created_by="t1")
    assert registry.list_book_references("bk_1") == []
    registry.remove_book_references("course", "c1")
    assert registry.list_referenced_books("course", "c1") == []


def test_read_and_write_rules_and_location_binding(tmp_path, make_user):
    admin = make_user("root", role="admin")
    owner = make_user("t1", role="teacher")
    student = make_user("s1", role="student")
    private = _register(tmp_path)
    public = _register(tmp_path, "bk_public", visibility="public")
    for user in (admin, owner):
        assert registry.can_read_book(private, user)
        assert registry.can_write_book(private, user)
    assert not registry.can_read_book(private, student)
    assert registry.can_read_book(public, student)
    assert not registry.can_write_book(public, student)
    collision = replace(public, base_dir=str(tmp_path / "foreign"), created_by=student.id)
    assert not registry.can_read_book(collision, student)
    assert not registry.can_write_book(collision, student)
    assert registry.get_book(public.ref) == public


def test_stale_records_cannot_grant_access_after_transfer_withdrawal_or_deletion(tmp_path, make_user):
    owner = make_user("t1", role="teacher")
    other = make_user("s1", role="student")
    book = _register(tmp_path, visibility="public")
    registry.transfer_book_ownership("t1", "new_owner")
    assert not registry.can_write_book(book, owner)
    with registry._write() as conn:
        conn.execute("UPDATE book_registry SET visibility = 'private'")
    assert not registry.can_read_book(book, other)
    assert not registry.can_read_book(book, owner)
    registry.unregister_book(book.book_id)
    assert not registry.can_read_book(book, make_user("root", role="admin"))
    assert not registry.can_write_book(book, owner)


def test_reference_reader_is_read_only_and_failure_is_closed(tmp_path, make_user):
    student = make_user("s1", role="student")
    book = _register(tmp_path)
    registry.replace_book_references("course", "c1", [book.ref], created_by="t1")
    assert not registry.can_read_book(book, student)
    registry.register_book_reference_reader("course", lambda rid, uid: (rid, uid) == ("c1", "s1"))
    assert registry.can_read_book(book, student)
    assert not registry.can_write_book(book, student)
    assert not registry.can_read_book(book, make_user("s2", role="student"))

    def broken(_rid, _uid):
        raise RuntimeError("synthetic reader failure")

    registry.register_book_reference_reader("course", broken)
    assert not registry.can_read_book(book, student)


def test_cached_builtin_hook_restores_reset_map_preserving_custom_reader(tmp_path, make_user, monkeypatch):
    book = _register(tmp_path)
    student = make_user("s1", role="student")
    for kind in ("course", "partner"):
        registry.replace_book_references(kind, "r1", [book.ref], created_by="t1")
    module = types.ModuleType("synthetic_book_readers")
    calls = []

    def register_book_readers():
        calls.append(True)
        registry.register_book_reference_reader("course", lambda _r, _u: True)
        registry.register_book_reference_reader("partner", lambda _r, _u: True)

    module.register_book_readers = register_book_readers
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(registry, "BUILTIN_BOOK_READER_MODULES", (module.__name__,))
    for _ in range(2):
        monkeypatch.setattr(registry, "_readers", {})

        def custom(_r, _u):
            return False

        registry.register_book_reference_reader("course", custom)
        assert registry.can_read_book(book, student)
        assert registry.can_read_book(book, student)
        assert registry._readers["course"] is custom
    assert len(calls) >= 2


def test_revocation_retains_owner_references_and_never_grants_access(tmp_path, make_user):
    book = _register(tmp_path)
    registry.replace_book_references("course", "foreign", [book.ref], created_by="t2")
    registry.replace_book_references("course", "own", [book.ref], created_by="t1")
    registry.register_book_reference_reader("course", lambda rid, _uid: rid == "foreign")
    student = make_user("s1", role="student")
    assert registry.can_read_book(book, student)
    moved = registry._revoke_references(book.ref, reason="made_private", keep_created_by="t1")
    assert [r.referrer_id for r in moved] == ["foreign"]
    assert [r.referrer_id for r in registry.list_book_references(book.ref)] == ["own"]
    assert not registry.can_read_book(book, student)
    assert registry.list_unavailable_book_references("course", "foreign") == moved
    assert moved[0].to_dict() == {
        "book_id": book.book_id, "book_title": book.title, "referrer_kind": "course",
        "referrer_id": "foreign", "created_by": "t2", "reason": "made_private",
        "revoked_at": moved[0].revoked_at,
    }
    assert moved[0].revoked_at
    registry.unregister_book(book.ref)
    assert registry.list_unavailable_book_references("course", "foreign") == moved
    [deleted] = registry.list_unavailable_book_references("course", "own")
    assert deleted.reason == "book_deleted"
    assert len(registry.list_revoked_book_references(book.ref)) == 2
    registry.remove_book_references("course", "foreign")
    assert registry.list_unavailable_book_references("course", "foreign") == []
    with pytest.raises(registry.BookReferenceError):
        registry.replace_book_references("course", "own", ["missing"], created_by="t1")
    assert registry.list_unavailable_book_references("course", "own") == [deleted]
    registry.replace_book_references("course", "own", [], created_by="t1")
    assert registry.list_revoked_book_references(book.ref) == []


def test_relocation_transfer_and_delete_cascade(tmp_path):
    first = _register(tmp_path, owner="gone")
    second = _register(tmp_path, "bk_2", owner="gone", where="old")
    assert registry.relocate_book_dir(tmp_path / "old", tmp_path / "new") == 1
    assert registry.get_book(second.ref).base_dir == str((tmp_path / "new").resolve())
    transferred = registry.transfer_book_ownership("gone", "admin-7")
    assert {r.book_id for r in transferred} == {first.book_id, second.book_id}
    assert all(r.created_by == "admin-7" for r in transferred)
    assert registry.list_transferred_books("admin-7") == transferred
    assert registry.list_transferred_books("gone") == []
    registry.replace_book_references("course", "c1", [first.ref], created_by="t1")
    registry.set_book_kb_links(first.ref, {"physics": "kb1"})
    registry.unregister_book(first.ref)
    assert registry.get_book(first.ref) is None
    assert registry.list_book_references(first.ref) == []
    assert registry.list_referenced_books("course", "c1") == []
    assert registry.book_kb_links(first.ref) == {}
    assert registry.list_revoked_book_references(first.ref)[0].book_title == first.title


def test_kb_links_and_meta_round_trip(tmp_path):
    book = _register(tmp_path)
    assert not registry.has_kb_links(book.ref)
    registry.set_book_kb_links(book.ref, {"physics": "kb1", "gone": ""})
    assert registry.has_kb_links(book.ref)
    assert registry.book_kb_links(book.ref) == {"physics": "kb1", "gone": ""}
    assert registry.kb_refs_for(book.ref, ["physics", "gone", "new"]) == ["kb:kb1", "gone", "new"]
    registry.set_book_kb_links(book.ref, {})
    assert registry.book_kb_links(book.ref) == {}
    with pytest.raises(registry.BookReferenceError):
        registry.set_book_kb_links("missing", {"x": "kb1"})
    registry.set_meta("k", "v1")
    registry.set_meta("k", "v2")
    assert registry.get_meta("k") == "v2"


def test_delete_rolls_back_revocations_when_delete_fails(tmp_path):
    book = _register(tmp_path)
    registry.replace_book_references("course", "c1", [book.ref], created_by="t1")
    registry.set_book_kb_links(book.ref, {"physics": "kb1"})
    with registry._write() as conn:
        conn.execute(
            "CREATE TRIGGER refuse_delete BEFORE DELETE ON book_registry "
            "BEGIN SELECT RAISE(ABORT, 'synthetic delete failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic delete failure"):
        registry.unregister_book(book.ref)
    assert registry.get_book(book.ref) == book
    assert len(registry.list_book_references(book.ref)) == 1
    assert registry.list_revoked_book_references(book.ref) == []
    assert registry.book_kb_links(book.ref) == {"physics": "kb1"}


def test_connection_helpers_compose_check_revoke_delete_and_rollback(tmp_path):
    book = _register(tmp_path)
    with pytest.raises(RuntimeError, match="cancel"):
        with registry._write() as conn:
            target = registry._target(conn, book.ref)
            assert registry._find_by_location(conn, tmp_path / "a", book.dir_name) == target
            registry._replace_references(conn, "course", "c1", [book.ref], created_by="t1")
            registry._set_kb_links(conn, book.ref, {"physics": "kb1"})
            registry._unregister(conn, book.ref)
            assert conn.execute("SELECT COUNT(*) FROM book_revoked_references").fetchone()[0] == 1
            raise RuntimeError("cancel")
    assert registry.get_book(book.ref) == book
    assert registry.list_book_references(book.ref) == []
    assert registry.list_revoked_book_references(book.ref) == []
    assert registry.book_kb_links(book.ref) == {}


def test_write_rolls_back_base_exception_and_rejects_nested_writer(tmp_path):
    _register(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        with registry._write() as conn:
            conn.execute("DELETE FROM book_registry")
            raise KeyboardInterrupt
    assert registry.get_book("bk_1") is not None
    with registry._write():
        with pytest.raises(registry.BookRegistryError, match="nested"):
            registry.set_meta("nested", "bad")
    assert registry.get_meta("nested") is None


def test_immediate_transaction_excludes_another_process_before_first_change(tmp_path):
    book = _register(tmp_path)
    code = """
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1], timeout=0.05, isolation_level=None)
try:
    conn.execute("BEGIN IMMEDIATE")
except sqlite3.OperationalError as exc:
    assert "locked" in str(exc), str(exc)
    print("writer blocked")
else:
    conn.rollback()
    raise AssertionError("concurrent writer entered")
finally:
    conn.close()
"""
    with registry._write() as conn:
        assert registry._target(conn, book.ref) == book
        result = subprocess.run(
            [sys.executable, "-B", "-c", code, str(registry._db_path())],
            capture_output=True, text=True, timeout=10, check=True,
        )
        assert result.stdout.strip() == "writer blocked"
    registry.replace_book_references("course", "c1", [book.ref], created_by="t1")
    assert len(registry.list_book_references(book.ref)) == 1


def test_sync_rechecks_location_after_concurrent_relocation(tmp_path, monkeypatch):
    book = _register(tmp_path)
    write = registry._write

    @contextmanager
    def relocate_before_write():
        with write() as conn:
            conn.execute(
                "UPDATE book_registry SET base_dir = ? WHERE book_id = ?",
                (str((tmp_path / "new").resolve()), book.book_id),
            )
        with write() as conn:
            yield conn

    monkeypatch.setattr(registry, "_write", relocate_before_write)
    assert registry.sync_book_dir(tmp_path / "a", {book.book_id: "Foreign title"}, created_by="t2") == {}
    current = registry.get_book(book.ref)
    assert current.title == book.title
    assert current.created_by == book.created_by
    assert current.base_dir == str((tmp_path / "new").resolve())


def test_symlink_base_is_same_canonical_registration(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "alias").symlink_to(tmp_path / "a", target_is_directory=True)
    book = _register(tmp_path)
    assert _register(tmp_path, where="alias", owner="foreign") == book
    assert registry.find_book_by_location(tmp_path / "alias", book.dir_name) == book


def test_atomic_visibility_revoke_helpers_see_uncommitted_state(tmp_path, make_user):
    book = _register(tmp_path, visibility="public")
    student = make_user("s1", role="student")
    owner = make_user("t1", role="teacher")
    registry.replace_book_references("course", "c1", [book.ref], created_by="t2")
    registry.register_book_reference_reader("course", lambda _rid, _uid: True)
    with registry._write() as conn:
        target = registry._target(conn, book.ref)
        assert registry._can_write(conn, target, owner)
        assert not registry._can_write(conn, target, student)
        conn.execute("UPDATE book_registry SET visibility = 'private' WHERE book_id = ?", (book.book_id,))
        registry._revoke(conn, target, registry._references_of(conn, book.ref), reason="made_private")
        assert not registry._can_read(conn, target, student)
        assert registry._can_read(conn, target, owner)
    assert not registry.can_read_book(book, student)
    assert registry.list_book_references(book.ref) == []
    assert registry.list_revoked_book_references(book.ref)[0].reason == "made_private"


def test_builtin_hook_failure_rolls_back_partial_readers_then_retries(tmp_path, make_user, monkeypatch):
    book = _register(tmp_path)
    student = make_user("s1", role="student")
    registry.replace_book_references("course", "c1", [book.ref], created_by="t1")
    module = types.ModuleType("synthetic_failing_book_readers")
    fail = True

    def register_book_readers():
        registry.register_book_reference_reader("course", lambda _r, _u: True)
        if fail:
            raise RuntimeError("synthetic incomplete hook")

    module.register_book_readers = register_book_readers
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(registry, "BUILTIN_BOOK_READER_MODULES", (module.__name__,))
    monkeypatch.setattr(registry, "_readers", {})
    assert not registry.can_read_book(book, student)
    assert registry._readers == {}
    fail = False
    assert registry.can_read_book(book, student)


def test_invalid_revoke_reason_preserves_live_references(tmp_path):
    book = _register(tmp_path)
    registry.replace_book_references("course", "c1", [book.ref], created_by="t1")
    with pytest.raises(registry.BookReferenceError, match="reason"):
        registry._revoke_references(book.ref, reason="invalid")
    assert len(registry.list_book_references(book.ref)) == 1
    assert registry.list_revoked_book_references(book.ref) == []


def test_process_interruption_cannot_commit_partial_revocation(tmp_path):
    book = _register(tmp_path)
    registry.replace_book_references("course", "c1", [book.ref], created_by="t1")
    code = """
import os, sys
from pathlib import Path
from deeptutor.book import registry
registry.DB_PATH_OVERRIDE = Path(sys.argv[1])
with registry._write() as conn:
    registry._unregister(conn, sys.argv[2])
    os._exit(23)
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code, str(registry._db_path()), book.book_id],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 23, result.stderr
    assert registry.get_book(book.ref) == book
    assert len(registry.list_book_references(book.ref)) == 1
    assert registry.list_revoked_book_references(book.ref) == []
    registry.unregister_book(book.ref)
    assert registry.get_book(book.ref) is None
    assert registry.list_revoked_book_references(book.ref)[0].reason == "book_deleted"
