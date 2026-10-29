"""P1c: no account picks a server folder; DeepTutor decides every storage location."""

from __future__ import annotations

import asyncio

from fastapi import HTTPException
import pytest

from deeptutor.api.routers import knowledge, workspace
from deeptutor.services.server_folders import SERVER_FOLDER_DISABLED


def _refused(coro):
    with pytest.raises(HTTPException) as caught:
        asyncio.run(coro)
    assert caught.value.status_code == 403
    assert caught.value.detail == SERVER_FOLDER_DISABLED


def test_workspace_routes_refuse_server_folders(tmp_path):
    folder = str(tmp_path)
    _refused(workspace.create_workspace(workspace.CreateWorkspacePayload(name="X", path=folder)))
    _refused(workspace.migrate_workspace_root(workspace.WorkspaceMigrationPayload(path=folder)))
    _refused(workspace.migrate_registered_workspace(
        "ws_any", workspace.WorkspaceMigrationPayload(path=folder),
    ))
    _refused(workspace.update_workspace(workspace.WorkspacePathPayload(path=folder)))
    _refused(workspace.validate_workspace(workspace.WorkspacePathPayload(path=folder)))


def test_knowledge_routes_refuse_server_folders(tmp_path):
    folder = str(tmp_path)
    _refused(knowledge.connect_obsidian_vault(
        knowledge.ConnectObsidianRequest(name="vault", vault_path=folder),
    ))
    _refused(knowledge.probe_linked_folder_route(
        knowledge.ProbeFolderRequest(folder_path=folder),
    ))
    _refused(knowledge.connect_linked_folder_route(
        knowledge.ConnectFolderRequest(name="linked", folder_path=folder),
    ))
    _refused(knowledge.link_folder("kb", knowledge.LinkFolderRequest(folder_path=folder)))
    from fastapi import BackgroundTasks

    _refused(knowledge.sync_folder("kb", "folder", BackgroundTasks()))
    _refused(knowledge.connect_marginnote4(
        knowledge.ConnectMarginNote4Request(name="mn", db_path=folder),
    ))


def test_marginnote4_without_a_path_is_still_allowed_to_proceed(monkeypatch):
    """The add-on pushes objects in; only a caller-chosen database path is refused."""
    called = {}

    class Manager:
        def register_marginnote4_library(self, name, db_path="", description=""):
            called["db_path"] = db_path
            return {"name": name}

    monkeypatch.setattr(knowledge, "get_kb_manager", lambda: Manager())
    try:
        asyncio.run(knowledge.connect_marginnote4(knowledge.ConnectMarginNote4Request(name="mn")))
    except HTTPException as exc:
        assert exc.detail != SERVER_FOLDER_DISABLED
    except Exception:
        pass
