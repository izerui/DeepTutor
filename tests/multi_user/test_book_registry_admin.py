"""Account deletion transfers books and only their active KB reference owners."""

from __future__ import annotations

from dataclasses import replace
import sqlite3

import pytest

from deeptutor.api.routers.auth import _delete_admin_user
from deeptutor.book import registry as books
from deeptutor.knowledge import registry as kbs
from deeptutor.multi_user.context import get_current_user
from deeptutor.multi_user.identity import get_user_by_id
from deeptutor.services.auth import TokenPayload


@pytest.fixture
def accounts(seed_user):
    boss = seed_user("boss", role="admin")
    teacher = seed_user("tina", role="teacher")
    return boss, teacher


def _book(tmp_path, owner, name, *, visibility="private"):
    return books.register_book(
        base_dir=tmp_path / "books", dir_name=f"book_{name}", title=name,
        created_by=owner, visibility=visibility,
    )


def _kb(tmp_path, owner, name):
    return kbs.register_kb(
        base_dir=tmp_path / "kbs", name=name, kind="uploaded",
        created_by=owner, visibility="public",
    )


def _reference(kb, book, *, owner=None, kind="book"):
    kbs.add_reference(
        kb.kb_id, referrer_kind=kind, referrer_id=book.book_id,
        doc_ids=["chapter/one.md"], created_by=owner or book.created_by,
    )


@pytest.mark.parametrize("token_kind", ["admin", "none", "empty_id"])
def test_deletion_transfers_all_books_to_the_same_kb_successor(
    accounts, tmp_path, as_user, token_kind,
):
    boss, teacher = accounts
    owner = teacher["id"]
    records = [
        _book(tmp_path, owner, "public", visibility="public"),
        _book(tmp_path, owner, "private"),
        _book(tmp_path, owner, "referenced"),
    ]
    other = _book(tmp_path, "other", "other")
    with as_user(owner, role="teacher"):
        books.replace_book_references("course", "foreign", [records[2].ref], created_by="other")
    incoming = books.list_book_references(records[2].ref)
    kb = _kb(tmp_path, owner, "shared")
    current = None if token_kind == "none" else TokenPayload(
        username="boss", role="admin", user_id=boss["id"] if token_kind == "admin" else "",
    )
    successor = boss["id"] if token_kind == "admin" else "local-admin"
    assert _delete_admin_user("tina", current) == (200, "")
    assert get_user_by_id(owner) is None
    assert kbs.get_kb(kb.ref).created_by == successor
    assert {b.book_id for b in books.list_transferred_books(successor)} == {
        b.book_id for b in records
    }
    for before in records:
        after = books.get_book(before.ref)
        assert after == replace(
            before, created_by=successor, updated_at=after.updated_at,
        )
    assert books.get_book(other.ref) == other
    assert books.list_book_references(records[2].ref) == incoming


def test_transfer_changes_only_active_references_of_actual_transferred_books(
    tmp_path, as_user, mu_isolated_root,
):
    moved = _book(tmp_path, "gone", "moved", visibility="public")
    foreign = _book(tmp_path, "other", "foreign", visibility="public")
    active = _kb(tmp_path, "successor", "active")
    withdrawn = _kb(tmp_path, "successor", "withdrawn")
    deleted = _kb(tmp_path, "successor", "deleted")
    unreferenced = _kb(tmp_path, "successor", "unreferenced")
    books.set_book_kb_links(moved.ref, {
        kb.name: kb.kb_id for kb in (active, withdrawn, deleted, unreferenced)
    } | {"missing": "missing-id", "confirmed-missing": ""})
    for kb in (active, withdrawn, deleted):
        _reference(kb, moved)
    _reference(active, foreign)
    _reference(active, moved, kind="course")
    kbs.set_visibility(withdrawn.ref, "private", force=True)
    kbs.unregister_kb(deleted.ref)
    kbs.remove_reference(unreferenced.ref, "book", moved.book_id)
    before = kbs.list_references(active.ref)
    history = kbs.list_unavailable_references("book", moved.book_id)
    links = books.book_kb_links(moved.ref)

    assert [b.book_id for b in books.transfer_book_ownership("gone", "successor")] == [
        moved.book_id,
    ]
    assert kbs.list_references(active.ref) == [
        replace(ref, created_by="successor")
        if (ref.referrer_kind, ref.referrer_id) == ("book", moved.book_id) else ref
        for ref in before
    ]
    assert books.book_kb_links(moved.ref) == links
    assert kbs.list_unavailable_references("book", moved.book_id) == history
    assert {kb.kb_id for kb in kbs.list_referenced("book", moved.book_id)} == {active.kb_id}
    assert not kbs.list_references(unreferenced.ref)
    with as_user("reader"):
        with books.reading_book(moved.ref):
            assert not kbs.can_read(kbs.get_kb(withdrawn.ref), get_current_user())
    unchanged = books.get_book(moved.ref)
    refs = kbs.list_references(active.ref)
    assert books.transfer_book_ownership("absent", "successor") == []
    assert books.transfer_book_ownership("successor", "successor") == [unchanged]
    assert kbs.list_references(active.ref) == refs
    assert kbs.list_unavailable_references("book", moved.book_id) == history


@pytest.mark.parametrize("foreign_ref", [False, True])
def test_simultaneous_takeover_keeps_own_refs_and_requires_force_for_foreign_refs(
    accounts, tmp_path, as_user, foreign_ref,
):
    boss, teacher = accounts
    kb = _kb(tmp_path, teacher["id"], "shared")
    own = _book(tmp_path, teacher["id"], "own", visibility="public")
    foreign = _book(tmp_path, "other", "foreign", visibility="public")
    books.set_book_kb_links(own.ref, {"shared": kb.kb_id})
    _reference(kb, own)
    if foreign_ref:
        _reference(kb, foreign)
    before = kbs.list_references(kb.ref)
    token = TokenPayload(username="boss", role="admin", user_id=boss["id"])
    assert _delete_admin_user("tina", token) == (200, "")
    assert books.get_book(own.ref).created_by == boss["id"]
    expected = [
        replace(ref, created_by=boss["id"]) if ref.referrer_id == own.book_id else ref
        for ref in before
    ]
    assert kbs.list_references(kb.ref) == expected
    with as_user(boss["id"], role="admin"):
        # Rule R1: a foreign book's reference no longer blocks; it is revoked.
        kbs.set_visibility(kb.ref, "private")
    assert kbs.list_references(kb.ref) == [
        ref for ref in expected if ref.referrer_id == own.book_id
    ]
    assert not kbs.list_unavailable_references("book", own.book_id)
    if foreign_ref:
        assert kbs.list_unavailable_references("book", foreign.book_id)[0].reason == "made_private"
    with as_user("reader"):
        with books.reading_book(own.ref):
            assert kbs.can_read(kbs.get_kb(kb.ref), get_current_user())


@pytest.mark.parametrize("failure", ["statement", "commit"])
def test_failed_reference_transfer_rolls_back_books_and_refs_but_deletion_succeeds(
    accounts, tmp_path, failure, caplog,
):
    boss, teacher = accounts
    kb = _kb(tmp_path, teacher["id"], "shared")
    records = [_book(tmp_path, teacher["id"], name) for name in ("one", "two")]
    for book in records:
        _reference(kb, book)
    before = kbs.list_references(kb.ref)
    with kbs._write() as conn:
        if failure == "statement":
            conn.execute(
                "CREATE TRIGGER fail_owner BEFORE UPDATE OF created_by ON kb_references "
                "BEGIN SELECT RAISE(ABORT, 'injected reference failure'); END"
            )
        else:
            conn.execute("CREATE TABLE missing_parent (id INTEGER PRIMARY KEY)")
            conn.execute(
                "CREATE TABLE deferred_failure (id INTEGER REFERENCES missing_parent(id) "
                "DEFERRABLE INITIALLY DEFERRED)"
            )
            conn.execute(
                "CREATE TRIGGER fail_owner AFTER UPDATE OF created_by ON kb_references "
                "BEGIN INSERT INTO deferred_failure VALUES (1); END"
            )
    token = TokenPayload(username="boss", role="admin", user_id=boss["id"])
    assert _delete_admin_user("tina", token) == (200, "")
    assert get_user_by_id(teacher["id"]) is None
    assert "Could not transfer books" in caplog.text
    assert "Could not transfer knowledge bases" not in caplog.text
    assert kbs.get_kb(kb.ref).created_by == boss["id"]
    assert [books.get_book(b.ref) for b in records] == records
    assert kbs.list_references(kb.ref) == before
    assert not books.list_transferred_books(boss["id"])
    with kbs._write() as conn:
        conn.execute("DROP TRIGGER fail_owner")
    assert len(books.transfer_book_ownership(teacher["id"], boss["id"])) == 2
    assert kbs.list_references(kb.ref) == [replace(ref, created_by=boss["id"]) for ref in before]


@pytest.mark.parametrize("failed_registry", ["book", "kb"])
def test_registry_failure_is_nonfatal_and_does_not_skip_the_other_transfer(
    accounts, tmp_path, monkeypatch, caplog, failed_registry,
):
    _, teacher = accounts
    book = _book(tmp_path, teacher["id"], "retained")
    kb = _kb(tmp_path, teacher["id"], "retained")

    def fail(*_args):
        raise sqlite3.OperationalError("injected unavailable registry")

    if failed_registry == "book":
        monkeypatch.setattr(books, "transfer_book_ownership", fail)
    else:
        monkeypatch.setattr(kbs, "transfer_ownership", fail)
    assert _delete_admin_user("tina", None) == (200, "")
    assert get_user_by_id(teacher["id"]) is None
    assert books.get_book(book.ref).created_by == (
        teacher["id"] if failed_registry == "book" else "local-admin"
    )
    assert kbs.get_kb(kb.ref).created_by == (
        teacher["id"] if failed_registry == "kb" else "local-admin"
    )
    assert "Could not transfer" in caplog.text


def test_deleted_owners_books_appear_in_successors_general_library_at_canonical_storage(
    accounts, as_user,
):
    from deeptutor.book.models import Book
    from deeptutor.book.storage import get_book_storage
    from deeptutor.multi_user.book_access import accessible_books, resolve_book
    from deeptutor.services.workspace import ContentWorkspaceService
    from deeptutor.services.workspace.context import workspace_context

    boss, teacher = accounts
    with as_user(teacher["id"], role="teacher"):
        storage = get_book_storage()
        for book_id in ("public", "private"):
            storage.save_book(Book(id=book_id, title=book_id))
        accessible_books()
        books.set_book_visibility("public", "public")
        originals = {
            book_id: storage.book_root(book_id) for book_id in ("public", "private")
        }
        manifests = {book_id: storage.load_book(book_id) for book_id in originals}
    token = TokenPayload(username="boss", role="admin", user_id=boss["id"])
    assert _delete_admin_user("tina", token) == (200, "")
    with as_user(boss["id"], role="admin"):
        listed = accessible_books()
        assert sorted(book.id for book, _ in listed) == ["private", "public"]
        for book, resolved in listed:
            assert resolved.capabilities()["group"] == "mine"
            assert resolved.can_edit and resolved.can_delete
            canonical = resolve_book(f"book:{book.id}")
            assert canonical.engine.storage.book_root(book.id) == originals[book.id]
            assert canonical.engine.storage.load_book(book.id) == manifests[book.id]
        workspace = ContentWorkspaceService().create_workspace("Other")["workspace_id"]
        with workspace_context(workspace):
            assert accessible_books() == []
