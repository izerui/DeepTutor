"""P8 item 5 — B2-4: non-lock sqlite3.OperationalError surfaces as 500.

When the migration write phase hits a non-lock OperationalError (e.g.
``disk I/O error``), the exception must NOT be mapped to a retryable 409
WorkspaceError. It should propagate as an unhandled error (500).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from deeptutor.services.workspace.migration import migrate_locations
from deeptutor.services.workspace.models import WorkspaceError


def test_non_lock_operational_error_is_not_wrapped_as_workspace_error(
    tmp_path: Path, monkeypatch,
) -> None:
    """A ``disk I/O error`` during registry snapshot must not become a 409."""
    from deeptutor.services.workspace import migration

    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "destination"

    service_stub = type("S", (), {
        "_assert_allowed_root": staticmethod(lambda _: None),
        "_catalog_file": lambda self: tmp_path / "catalog.db",
        "_catalog_connection": lambda self: _noop_ctx(),
    })()

    original_snapshot = migration._registry_snapshot

    def exploding_snapshot(sources):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(migration, "_registry_snapshot", exploding_snapshot)

    row = {"path": str(source), "workspace_id": "ws_test"}
    # A non-lock OperationalError must propagate raw — NOT as WorkspaceError.
    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        migrate_locations(service_stub, [(row, destination)])


def test_lock_error_during_snapshot_is_wrapped_as_workspace_error(
    tmp_path: Path, monkeypatch,
) -> None:
    """A ``database is locked`` during snapshot must become a retryable WorkspaceError."""
    from deeptutor.services.workspace import migration

    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "destination"

    service_stub = type("S", (), {
        "_assert_allowed_root": staticmethod(lambda _: None),
        "_catalog_file": lambda self: tmp_path / "catalog.db",
        "_catalog_connection": lambda self: _noop_ctx(),
    })()

    def locked_snapshot(sources):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(migration, "_registry_snapshot", locked_snapshot)

    row = {"path": str(source), "workspace_id": "ws_test"}
    with pytest.raises(WorkspaceError, match="locked|in use"):
        migrate_locations(service_stub, [(row, destination)])


import contextlib

@contextlib.contextmanager
def _noop_ctx():
    yield
