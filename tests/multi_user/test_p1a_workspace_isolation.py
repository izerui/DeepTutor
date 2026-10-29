"""P1a / S4: one storage folder belongs to one account (spec 2026-10-29 §4 rule 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from deeptutor.services.workspace import ContentWorkspaceService, WorkspaceError
from deeptutor.services.workspace.context import resolve_workspace_scope


@pytest.fixture
def shared(tmp_path, monkeypatch):
    folder = (tmp_path / "shared").resolve()
    (folder / "outputs").mkdir(parents=True)
    monkeypatch.setenv("DEEPTUTOR_WORKSPACE_ROOT", str(folder))
    monkeypatch.setenv("DEEPTUTOR_WORKSPACE_ALLOWED_ROOTS", str(folder))
    return folder


def _by_path(rows):
    return {Path(row["path"]).resolve(): row for row in rows}


def _register(service, workspace_id, path):
    """Write a catalog row directly, as releases before P1a did."""
    from deeptutor.services.workspace.catalog import _owner_id

    service.list_workspaces()
    return service._save_registration({
        "workspace_id": workspace_id,
        "owner_id": _owner_id(),
        "path": str(path),
        "display_name": "workspace",
        "kind": "workspace",
        "follows_root": False,
        "archived": False,
        "created_at": "2026-10-01T00:00:00+00:00",
    })


def test_deployment_folder_is_not_adopted_by_any_account(as_user, shared):
    for uid in ("alice", "bob"):
        with as_user(uid):
            assert shared not in _by_path(ContentWorkspaceService().list_workspaces())


def test_an_adopted_deployment_folder_cannot_hold_learning_data(as_user, shared):
    with as_user("alice"):
        service = ContentWorkspaceService()
        row = _register(service, "ws_legacy_alice", shared)
        listed = _by_path(service.list_workspaces())[shared]
        assert listed["status"] == "invalid"
        assert "shared content folder" in listed["error"]
        with pytest.raises(WorkspaceError, match="shared content folder"):
            resolve_workspace_scope(row["workspace_id"])


def test_a_folder_registered_by_one_account_is_refused_to_another(as_user, shared):
    folder = shared / "notes"
    folder.mkdir()
    with as_user("alice"):
        ContentWorkspaceService().create_workspace("Notes", str(folder))
    with as_user("bob"):
        with pytest.raises(WorkspaceError, match="another account"):
            ContentWorkspaceService().create_workspace("Notes", str(folder))


def test_existing_double_claims_are_blocked_for_both_accounts(as_user, shared):
    folder = shared / "notes"
    folder.mkdir()
    ids = {}
    for uid in ("alice", "bob"):
        with as_user(uid):
            ids[uid] = _register(ContentWorkspaceService(), f"ws_notes_{uid}", folder)["workspace_id"]
    for uid in ("alice", "bob"):
        with as_user(uid):
            with pytest.raises(WorkspaceError, match="another account"):
                resolve_workspace_scope(ids[uid])


def test_admin_cannot_register_another_accounts_storage(as_user, mu_isolated_root):
    from deeptutor.multi_user import paths

    bob_folder = paths.USERS_ROOT / "bob" / "stuff"
    bob_folder.mkdir(parents=True)
    system_folder = paths.SYSTEM_ROOT / "stuff"
    system_folder.mkdir(parents=True)
    with as_user("boss", role="admin"):
        for folder in (bob_folder, system_folder):
            with pytest.raises(WorkspaceError, match="another account"):
                ContentWorkspaceService().create_workspace("X", str(folder))


def test_an_account_may_still_register_its_own_folder_twice_by_name(as_user, shared):
    folder = shared / "mine"
    folder.mkdir()
    with as_user("alice"):
        service = ContentWorkspaceService()
        first = service.create_workspace("Mine", str(folder))
        again = service.create_workspace("Mine again", str(folder))
        assert again["workspace_id"] == first["workspace_id"]
        resolve_workspace_scope(first["workspace_id"])
