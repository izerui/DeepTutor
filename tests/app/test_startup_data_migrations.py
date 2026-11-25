from __future__ import annotations

import pytest

from deeptutor.app.container import ApplicationContainer


@pytest.mark.asyncio
async def test_startup_data_migrations_run_legacy_then_workspace_upgrade(monkeypatch) -> None:
    container = object.__new__(ApplicationContainer)
    calls: list[str] = []

    async def migrate_legacy() -> list[dict[str, object]]:
        calls.append("legacy")
        return [{"imported": 2}]

    async def migrate_workspace() -> list[dict[str, object]]:
        calls.append("workspace")
        return [{"migrated": 3}]

    container.migrate_all_legacy_chats = migrate_legacy  # type: ignore[method-assign]
    container.migrate_all_workspace_preferences = migrate_workspace  # type: ignore[method-assign]
    monkeypatch.setattr(container, "_local_users", lambda: [])

    reports = await container.run_startup_data_migrations()

    assert calls == ["legacy", "workspace"]
    assert reports == {
        "legacy_chat": [{"imported": 2}],
        "workspace_preferences": [{"migrated": 3}],
        "workspace_data": [],
    }


@pytest.mark.asyncio
async def test_linked_assessment_recovery_visits_every_account_and_workspace(
    monkeypatch, tmp_path
) -> None:
    """Startup recovery used to run only in the admin's default workspace, so
    other learners' unapplied cross-surface attempts were never replayed."""
    from contextlib import contextmanager
    from types import SimpleNamespace

    from deeptutor.services.workspace.models import WorkspaceError

    container = object.__new__(ApplicationContainer)
    users = [SimpleNamespace(id="admin"), SimpleNamespace(id="learner"), SimpleNamespace(id="busy")]
    monkeypatch.setattr(container, "_local_users", lambda: users)
    current = {"user": "", "workspace": ""}

    @contextmanager
    def user_context(user):
        current["user"] = user.id
        yield

    @contextmanager
    def workspace_context(workspace_id):
        if workspace_id == "ws-gone":
            raise WorkspaceError("gone")
        current["workspace"] = workspace_id
        yield

    def acquire_activity():
        if current["user"] == "busy":
            raise WorkspaceError("busy")
        return SimpleNamespace(close=lambda: None)

    folder = tmp_path / "folder"
    folder.mkdir()
    catalogs = {
        "admin": [],
        "learner": [
            {"workspace_id": "ws-1", "kind": "workspace", "path": str(folder)},
            {"workspace_id": "ws-gone", "kind": "workspace", "path": str(folder)},
            {"workspace_id": "chat", "kind": "session", "path": str(folder)},
        ],
    }
    visited: list[tuple[str, str]] = []

    async def reconcile():
        visited.append((current["user"], current["workspace"]))
        return (1, 0) if current["workspace"] == "ws-1" else (0, 0)

    monkeypatch.setattr("deeptutor.multi_user.paths.user_context", user_context)
    monkeypatch.setattr("deeptutor.services.workspace.context.workspace_context", workspace_context)
    monkeypatch.setattr(
        "deeptutor.services.workspace.activity.acquire_activity", acquire_activity
    )
    monkeypatch.setattr(
        "deeptutor.services.workspace.get_content_workspace_service",
        lambda: SimpleNamespace(_catalog=lambda: catalogs[current["user"]]),
    )
    monkeypatch.setattr(
        "deeptutor.services.session.get_sqlite_session_store",
        lambda: SimpleNamespace(db_path=f"{current['user']}/{current['workspace']}"),
    )
    monkeypatch.setattr("deeptutor.learning.assessment.reconcile_linked_assessments", reconcile)

    totals = await container.reconcile_all_linked_assessments()

    assert visited == [("admin", ""), ("learner", ""), ("learner", "ws-1")]
    assert totals == (1, 0)
