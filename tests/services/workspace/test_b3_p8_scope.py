"""P8 item 5: Non-lock OperationalError in the joint-write phase of workspace
migration propagates raw (-> 500), and is NOT converted to WorkspaceError (409).

Supplements ``test_migration_busy_and_validation.py`` which covers only the
snapshot-phase non-lock error.  This file covers BOTH phases for completeness.
"""

from __future__ import annotations

import sqlite3

import pytest

from deeptutor.services.workspace.models import WorkspaceError
from tests.services.workspace.test_data_scope import account as account


# ── Write phase (the gap identified by B2-4) ─────────────────────────


def test_non_lock_operational_error_in_write_phase_is_not_converted(
    account, tmp_path, monkeypatch,
):
    """A non-lock ``sqlite3.OperationalError`` (e.g. ``disk I/O error``)
    raised during the joint-write commit phase must propagate as-is (-> 500)
    rather than being wrapped in ``WorkspaceError`` (-> 409).
    """
    from deeptutor.services.workspace import migration

    workspace = account.create_workspace("DiskError")
    destination = tmp_path / "disk-error-move"

    def disk_error(*_args, **_kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(migration, "_relocate_registry_rows", disk_error)
    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        with account.maintenance():
            account.migrate_workspace(workspace["workspace_id"], str(destination))


def test_lock_error_in_write_phase_is_converted_to_workspace_error(
    account, tmp_path, monkeypatch,
):
    """Counterpart: a lock/busy error in the same write phase IS converted."""
    from deeptutor.services.workspace import migration

    workspace = account.create_workspace("BusyWrite")
    destination = tmp_path / "busy-write-move"

    def locked(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(migration, "_relocate_registry_rows", locked)
    with pytest.raises(WorkspaceError, match="(?i)migration commit"):
        with account.maintenance():
            account.migrate_workspace(workspace["workspace_id"], str(destination))


# ── Snapshot phase (same pattern, verifying both error kinds) ────────


def test_non_lock_operational_error_in_snapshot_phase_is_not_converted(
    account, tmp_path, monkeypatch,
):
    """Non-lock OperationalError in snapshot -> raw propagation (-> 500)."""
    from deeptutor.services.workspace import migration

    workspace = account.create_workspace("DiskSnapshot")
    destination = tmp_path / "disk-snapshot-move"

    def disk_error(_sources):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(migration, "_registry_snapshot", disk_error)
    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        with account.maintenance():
            account.migrate_workspace(workspace["workspace_id"], str(destination))


def test_lock_error_in_snapshot_phase_is_converted_to_workspace_error(
    account, tmp_path, monkeypatch,
):
    """Lock error in snapshot -> WorkspaceError (-> 409)."""
    from deeptutor.services.workspace import migration

    workspace = account.create_workspace("BusySnapshot")
    destination = tmp_path / "busy-snapshot-move"

    def locked(_sources):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(migration, "_registry_snapshot", locked)
    with pytest.raises(WorkspaceError, match="(?i)snapshot"):
        with account.maintenance():
            account.migrate_workspace(workspace["workspace_id"], str(destination))
