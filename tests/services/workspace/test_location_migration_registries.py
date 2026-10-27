"""Workspace location migrations keep KB/Book registry identities attached."""

from __future__ import annotations

from pathlib import Path

import pytest

from deeptutor.services.path_service import get_path_service
from deeptutor.services.workspace.context import workspace_context
from deeptutor.services.workspace.models import WorkspaceError
from tests.services.workspace.test_data_scope import account as account

_TEMPLATE = "tpl_location"


def _seed(account) -> dict:
    """A workspace KB (public + referenced) and book (public + teacher path)."""
    from deeptutor.book import registry as books
    from deeptutor.book.models import Book
    from deeptutor.book.storage import BookStorage
    from deeptutor.knowledge import registry as kbs

    workspace = account.create_workspace("Algebra")
    with workspace_context(workspace["workspace_id"]):
        paths = get_path_service()
        BookStorage(path_service=paths).save_book(Book(id="bk_loc", title="Located"))
        kb_dir = paths.get_knowledge_bases_root() / "atlas"
        kb_dir.mkdir(parents=True)
        (kb_dir / "document.md").write_text("workspace KB")
        kb = kbs.register_kb(
            base_dir=kb_dir.parent, name="atlas", kind="uploaded", created_by="scope-test",
        )
        kbs.set_visibility(kb.kb_id, "public")
        kbs.add_reference(
            kb.kb_id, referrer_kind="course", referrer_id="course_1", created_by="other",
        )
        book = books.register_new_book(
            base_dir=paths.get_book_dir(), dir_name=books.dir_name_for("bk_loc"),
            title="Located", created_by="scope-test",
        )
        books.set_book_kb_links(book.book_id, {"atlas": kb.kb_id})
        books.set_book_visibility(book.book_id, "public")
        books.replace_book_references(
            "mastery_template", _TEMPLATE, [book.book_id], created_by="scope-test",
        )
        old_roots = {
            "kb": paths.get_knowledge_bases_root().resolve(),
            "book": paths.get_book_dir().resolve(),
        }
    return {
        "workspace": workspace,
        "kb": kbs.get_kb(kb.kb_id),
        "book": books.get_book(book.book_id),
        "kb_refs": kbs.list_references(kb.kb_id),
        "book_refs": books.list_book_references(book.book_id),
        "old_roots": old_roots,
    }


def _assert_follows(account, seeded: dict, destination: Path) -> None:
    from deeptutor.book import registry as books
    from deeptutor.knowledge import registry as kbs
    from deeptutor.multi_user import book_access
    from deeptutor.teaching import books as teaching_books

    workspace_id = seeded["workspace"]["workspace_id"]
    kb, book = seeded["kb"], seeded["book"]
    assert account.binding_by_id(workspace_id).root == destination
    with workspace_context(workspace_id):
        paths = get_path_service()
        kb_root = paths.get_knowledge_bases_root().resolve()
        book_root = paths.get_book_dir().resolve()
        assert kb_root.is_relative_to(destination)
        # Fixed refs resolve to the active copy, keeping visibility/refs/links.
        moved_kb = kbs.resolve_ref(f"kb:{kb.kb_id}")
        assert moved_kb.base_dir == str(kb_root)
        assert moved_kb.visibility == "public"
        assert kbs.list_references(kb.kb_id) == seeded["kb_refs"]
        # Listing the new location never mints a second identity.
        synced = kbs.sync_base_dir(kb_root, {"atlas": {}}, created_by="scope-test")
        assert synced["atlas"].kb_id == kb.kb_id
        assert [r.kb_id for r in kbs.list_records() if r.dir_name == "atlas"] == [kb.kb_id]
        assert kbs.list_records(base_dir=seeded["old_roots"]["kb"]) == []
        moved_book = books.get_book(book.book_id)
        assert moved_book.base_dir == str(book_root)
        assert moved_book.visibility == "public"
        assert books.book_kb_links(book.book_id) == {"atlas": kb.kb_id}
        assert books.list_book_references(book.book_id) == seeded["book_refs"]
        # The owner's own copy stays on the own branch (sharing manageable).
        storage = book_access.storage_for(moved_book)
        loaded = storage.load_book(book.book_id)
        assert loaded is not None
        record = book_access.ensure_book_registered(loaded, storage)
        assert record is not None and record.book_id == book.book_id
        assert teaching_books.resolve(book.book_id).book_id == book.book_id
        # The teacher path's cited material resolves at the new location.
        cited = teaching_books.resolve(f"book:{book.book_id}")
        assert cited.base_dir == str(book_root)
        assert [r.book_id for r in books.list_referenced_books("mastery_template", _TEMPLATE)] == [
            book.book_id
        ]


def test_single_workspace_migration_moves_registry_locations(account, tmp_path):
    seeded = _seed(account)
    destination = (tmp_path / "moved-algebra").resolve()
    with account.maintenance():
        account.migrate_workspace(seeded["workspace"]["workspace_id"], str(destination))
    _assert_follows(account, seeded, destination)
    # The retained source copy is no longer bound to any registry identity.
    assert seeded["old_roots"]["book"].is_dir()


def test_root_migration_moves_registry_locations(account, tmp_path):
    seeded = _seed(account)
    root = (tmp_path / "new-root").resolve()
    with account.maintenance():
        account.migrate_root(str(root))
    _assert_follows(account, seeded, root / seeded["workspace"]["workspace_id"])


def _assert_unchanged(account, seeded: dict, destination: Path) -> None:
    from deeptutor.book import registry as books
    from deeptutor.knowledge import registry as kbs

    workspace_id = seeded["workspace"]["workspace_id"]
    assert account.binding_by_id(workspace_id).root == Path(seeded["workspace"]["path"])
    assert kbs.get_kb(seeded["kb"].kb_id) == seeded["kb"]
    assert books.get_book(seeded["book"].book_id) == seeded["book"]
    assert not destination.exists()
    account.assert_available()


def test_registry_failure_rolls_back_catalog_and_both_registries(account, tmp_path, monkeypatch):
    from deeptutor.book import registry as books
    from deeptutor.knowledge import registry as kbs

    seeded = _seed(account)
    destination = tmp_path / "failed-move"
    observed = []

    def fail_after_kb(conn, book_id, _base_dir):
        # The KB row already moved inside the joint transaction.
        assert conn.execute(
            "SELECT base_dir FROM main.kb_registry WHERE kb_id = ?", (seeded["kb"].kb_id,)
        ).fetchone()[0] != seeded["kb"].base_dir
        observed.append(book_id)
        raise OSError("injected book relocation failure")

    monkeypatch.setattr(books, "_relocate_book", fail_after_kb)
    with pytest.raises(OSError, match="injected"):
        with account.maintenance():
            account.migrate_workspace(seeded["workspace"]["workspace_id"], str(destination))
    assert observed == [seeded["book"].book_id]
    _assert_unchanged(account, seeded, destination)
    assert kbs.get_kb(seeded["kb"].kb_id).base_dir == seeded["kb"].base_dir


def test_catalog_failure_after_registry_writes_rolls_back_everything(
    account, tmp_path, monkeypatch,
):
    from deeptutor.services.workspace import migration

    seeded = _seed(account)
    destination = tmp_path / "failed-catalog"
    original = migration._relocate_registry_rows
    calls = []

    def relocate_then_change_catalog(conn, *args, **kwargs):
        original(conn, *args, **kwargs)
        calls.append(True)
        # Another writer changed the workspace path before the catalog update.
        conn.execute(
            "UPDATE catalog.workspaces SET payload = json_set(payload, '$.path', '/elsewhere') "
            "WHERE id = ?", (seeded["workspace"]["workspace_id"],),
        )

    monkeypatch.setattr(migration, "_relocate_registry_rows", relocate_then_change_catalog)
    with pytest.raises(WorkspaceError, match="changed during migration"):
        with account.maintenance():
            account.migrate_workspace(seeded["workspace"]["workspace_id"], str(destination))
    assert calls
    _assert_unchanged(account, seeded, destination)


def test_registry_identity_change_during_copy_refuses_migration(account, tmp_path, monkeypatch):
    from deeptutor.knowledge import registry as kbs
    from deeptutor.services.workspace import migration

    seeded = _seed(account)
    destination = tmp_path / "raced-move"
    copytree = migration.shutil.copytree
    added = []

    def register_during_copy(*args, **kwargs):
        result = copytree(*args, **kwargs)
        added.append(
            kbs.register_kb(
                base_dir=seeded["kb"].base_dir, name="late", kind="uploaded",
                created_by="scope-test",
            )
        )
        return result

    with monkeypatch.context() as patch:
        patch.setattr(migration.shutil, "copytree", register_during_copy)
        with pytest.raises(WorkspaceError, match="registrations changed"):
            with account.maintenance():
                account.migrate_workspace(seeded["workspace"]["workspace_id"], str(destination))
    _assert_unchanged(account, seeded, destination)
    assert kbs.get_kb(added[0].kb_id).base_dir == seeded["kb"].base_dir


def test_destination_registry_conflict_refuses_without_divergence(account, tmp_path):
    from deeptutor.knowledge import registry as kbs

    seeded = _seed(account)
    destination = (tmp_path / "conflicted").resolve()
    relative = Path(seeded["kb"].base_dir).relative_to(Path(seeded["workspace"]["path"]).resolve())
    stale = kbs.register_kb(
        base_dir=destination / relative, name="atlas", kind="uploaded", created_by="other",
    )
    with pytest.raises(WorkspaceError, match="already registered"):
        with account.maintenance():
            account.migrate_workspace(seeded["workspace"]["workspace_id"], str(destination))
    _assert_unchanged(account, seeded, destination)
    assert kbs.get_kb(stale.kb_id) == stale


def test_non_atomic_catalog_journal_refuses_migration(account, tmp_path):
    import sqlite3

    seeded = _seed(account)
    destination = tmp_path / "wal-move"
    with sqlite3.connect(account._catalog_file()) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
    with pytest.raises(WorkspaceError, match="cannot be moved together"):
        with account.maintenance():
            account.migrate_workspace(seeded["workspace"]["workspace_id"], str(destination))
    _assert_unchanged(account, seeded, destination)
