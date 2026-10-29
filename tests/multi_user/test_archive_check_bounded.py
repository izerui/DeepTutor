"""B1-3: archive / connector checks must not re-read every account's state per resolution."""

from __future__ import annotations

import json
import os
from pathlib import Path
import time

from fastapi import HTTPException
import pytest

from deeptutor.knowledge import registry
from deeptutor.multi_user.knowledge_access import current_kb_manager, resolve_kb
from deeptutor.services.workspace import ContentWorkspaceService
from deeptutor.services.workspace.context import workspace_context

ACCOUNTS = 50


def _age(path: Path) -> None:
    """Make a synthetic file look like long-lived account state."""
    old = time.time() - 3600
    os.utime(path, (old, old))


@pytest.fixture(autouse=True)
def _an_hour_later(monkeypatch):
    """``utime`` cannot age ctime, which the racy check also honors (B3-17).

    Read every file "an hour later" so aged files count as settled; the
    fingerprint (which includes ctime) still detects every rewrite.
    """
    from types import SimpleNamespace

    from deeptutor.knowledge import file_snapshot

    monkeypatch.setattr(
        file_snapshot, "time", SimpleNamespace(time_ns=lambda: time.time_ns() + 3600 * 10**9)
    )


def _accounts_with_catalogs(as_user) -> list[Path]:
    catalogs = []
    for index in range(ACCOUNTS):
        with as_user(f"student{index:02d}"):
            service = ContentWorkspaceService()
            service.create_workspace("Notes")
            catalogs.append(service._catalog_file())
    return catalogs


def _teacher_kb(as_user, make_kb):
    with as_user("teacher"):
        service = ContentWorkspaceService()
        workspace = service.create_workspace("Course")["workspace_id"]
        with workspace_context(workspace):
            manager = current_kb_manager()
            make_kb(manager, "lectures")
            record = registry.register_kb(
                base_dir=manager.base_dir, name="lectures", kind="uploaded",
                created_by="teacher", visibility="public",
            )
        catalog = service._catalog_file()
    return manager, workspace, record, catalog


def _count_catalog_opens(monkeypatch) -> list[str]:
    from deeptutor.knowledge import registry_scope

    opened: list[str] = []
    original = registry_scope.sqlite3.connect

    def connect(database, *args, **kwargs):
        if "workspaces.sqlite3" in str(database):
            opened.append(str(database))
        return original(database, *args, **kwargs)

    monkeypatch.setattr(registry_scope.sqlite3, "connect", connect)
    return opened


@pytest.mark.parametrize("caller", ["teacher", "root"])
def test_resolution_opens_bounded_catalogs_and_archive_applies_immediately(
        as_user, make_kb, monkeypatch, caller,
):
    catalogs = _accounts_with_catalogs(as_user)
    _, workspace, record, teacher_catalog = _teacher_kb(as_user, make_kb)
    for catalog in [*catalogs, teacher_catalog]:
        _age(catalog)
    role = "admin" if caller == "root" else "user"
    from deeptutor.publishing.admin import admin_retiring

    # spec 2026-10-29 D10 (P4): an admin writes someone else's public KB only while retiring it.
    opened = _count_catalog_opens(monkeypatch)
    with as_user(caller, role=role), admin_retiring(role == "admin"):
        # Warm-up may read every catalog once per process.
        assert not resolve_kb(record.ref, require_write=True).read_only
        opened.clear()
        for _ in range(5):
            assert not resolve_kb(record.ref, require_write=True).read_only
    assert len(opened) <= 1, f"{len(opened)} catalog opens for {ACCOUNTS} accounts"

    # Archiving blocks the very next write, reading only the changed catalog.
    with as_user("teacher"):
        ContentWorkspaceService().update_workspace(workspace, archived=True)
    opened.clear()
    with as_user(caller, role=role), admin_retiring(role == "admin"):
        assert resolve_kb(record.ref).read_only
        with pytest.raises(HTTPException) as denied:
            resolve_kb(record.ref, require_write=True)
        assert denied.value.status_code == 403
    # Only the just-written (hence not yet trusted) catalog is re-read.
    assert opened and all(teacher_catalog.as_uri() in path for path in opened)

    # Restoring takes effect immediately too.
    with as_user("teacher"):
        ContentWorkspaceService().update_workspace(workspace, archived=False)
    with as_user(caller, role=role), admin_retiring(role == "admin"):
        assert not resolve_kb(record.ref, require_write=True).read_only


def test_rewrite_keeping_size_and_mtime_is_not_served_from_cache(as_user, make_kb):
    """A commit that keeps size and (coarse) mtime must not serve stale state."""
    from deeptutor.knowledge import registry_scope

    _, workspace, record, catalog = _teacher_kb(as_user, make_kb)
    _age(catalog)
    aged = catalog.stat()
    with as_user("teacher"):
        assert not resolve_kb(record.ref, require_write=True).read_only
        ContentWorkspaceService().update_workspace(workspace, archived=True)
    assert catalog.stat().st_size == aged.st_size
    # Simulate a filesystem whose timestamp tick hides the archive commit.
    os.utime(catalog, ns=(aged.st_atime_ns, aged.st_mtime_ns))
    assert registry_scope.source_is_archived(record.base_dir)
    with as_user("teacher"):
        with pytest.raises(HTTPException):
            resolve_kb(record.ref, require_write=True)


def test_connector_classification_follows_config_changes(as_user, make_kb, monkeypatch):
    manager, _, record, _ = _teacher_kb(as_user, make_kb)
    config = Path(manager.base_dir) / "kb_config.json"
    _age(config)
    assert registry.config_kind(record) == registry.UPLOADED_KIND
    assert not registry.is_connector(record)

    parses = []
    original = registry.json.loads

    class CountingJson:
        @staticmethod
        def loads(text, *args, **kwargs):
            parses.append(1)
            return original(text, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(json, name)

    monkeypatch.setattr(registry, "json", CountingJson())
    for _ in range(10):
        assert registry.config_kind(record) == registry.UPLOADED_KIND
    assert len(parses) <= 1

    payload = json.loads(config.read_text(encoding="utf-8"))
    entry = payload["knowledge_bases"]["lectures"]
    aged = config.stat()
    entry["type"] = sorted(registry.CONNECTOR_KINDS)[0]
    config.write_text(json.dumps(payload), encoding="utf-8")
    # Even when the timestamp tick hides the rewrite, classification follows it.
    os.utime(config, ns=(aged.st_atime_ns, aged.st_mtime_ns))
    assert registry.is_connector(record)
    assert registry.config_kind(record) in registry.CONNECTOR_KINDS
    entry.pop("type")
    config.write_text(json.dumps(payload), encoding="utf-8")
    assert not registry.is_connector(record)
    assert registry.config_kind(record) == registry.UPLOADED_KIND
