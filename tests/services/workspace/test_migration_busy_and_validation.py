"""Three residual workspace migration issues from fff1e7d6.

1. OperationalError (database-is-locked) escapes the router → 500.
2. shared_write() attach-path collision reports inaccurate message.
3. _registry_snapshot creates registry DBs as side-effect before source check.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from deeptutor.book.registry import BookRegistryError
from deeptutor.services.workspace.models import WorkspaceError
from tests.services.workspace.test_data_scope import account as account


# ── Problem 1: busy/locked → WorkspaceError (409), not 500 ──────────


def test_locked_registry_during_migration_raises_workspace_error(account, tmp_path, monkeypatch):
    """A sqlite3.OperationalError from a busy joint writer must surface as
    WorkspaceError so the router converts it to 409, not 500."""
    from deeptutor.services.workspace import migration

    workspace = account.create_workspace("Busy")
    destination = tmp_path / "busy-move"

    original_snapshot = migration._registry_snapshot

    def locked_snapshot(sources):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(migration, "_registry_snapshot", locked_snapshot)
    with pytest.raises(WorkspaceError, match="(?i)busy|locked|in use"):
        with account.maintenance():
            account.migrate_workspace(workspace["workspace_id"], str(destination))
    assert not destination.exists()


def test_locked_registry_during_joint_write_raises_workspace_error(account, tmp_path, monkeypatch):
    """OperationalError from the joint-write block (lines 148-188) must also
    be caught as WorkspaceError."""
    from deeptutor.services.workspace import migration

    workspace = account.create_workspace("BusyJoint")
    destination = tmp_path / "busy-joint-move"

    original_relocate = migration._relocate_registry_rows

    def locked_relocate(conn, *args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(migration, "_relocate_registry_rows", locked_relocate)
    with pytest.raises(WorkspaceError, match="(?i)busy|locked|in use"):
        with account.maintenance():
            account.migrate_workspace(workspace["workspace_id"], str(destination))


# ── Problem 2: attach-path collision message accuracy ────────────────


def test_attach_path_collision_message_names_actual_conflict(tmp_path):
    """When an attach path collides with a registry path, the error message
    should name the actual conflict, not just 'Book and KB registries'."""
    from deeptutor.book import registry as books
    from deeptutor.book.sharing import shared_write

    book_path = books._db_path().resolve()
    # Attach a DB that happens to be the same file as the book registry
    with pytest.raises(BookRegistryError, match="(?i)catalog.*books|books.*catalog") as exc_info:
        with shared_write(attach={"catalog": book_path}):
            pass
    # The message must name the actual conflicting databases, not a generic claim
    assert "distinct" in str(exc_info.value).lower()
    assert "catalog" in str(exc_info.value)


# ── Problem 3: no side-effect DB creation before source validation ───


def test_registry_snapshot_does_not_create_dbs_for_missing_source(account, tmp_path, monkeypatch):
    """_registry_snapshot must not create registry DB files as a side effect
    when called for a source directory that doesn't exist."""
    import shutil

    from deeptutor.book import registry as books
    from deeptutor.knowledge import registry as kbs

    workspace = account.create_workspace("Ghost")
    # Delete the workspace source directory so the migration should fail
    source = Path(workspace["path"]).resolve()
    if source.is_dir():
        shutil.rmtree(source)

    kb_path = kbs._db_path()
    book_path = books._db_path()
    # Remove existing DB files to check they aren't recreated as side-effect
    for p in (kb_path, book_path):
        if p.exists():
            p.unlink()
        # Also remove WAL/SHM artifacts
        for suffix in ("-wal", "-shm", "-journal"):
            artifact = p.parent / (p.name + suffix)
            if artifact.exists():
                artifact.unlink()

    destination = tmp_path / "ghost-move"
    with pytest.raises(WorkspaceError, match="(?i)source|does not exist"):
        with account.maintenance():
            account.migrate_workspace(workspace["workspace_id"], str(destination))

    # Registry DBs must not have been created as side-effect of a rejected migration
    assert not kb_path.exists(), "KB registry DB was created as side-effect"
    assert not book_path.exists(), "Book registry DB was created as side-effect"
