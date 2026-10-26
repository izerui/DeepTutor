from __future__ import annotations

import asyncio

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
import pytest

from deeptutor.api.routers import book, mastery_path, reading, sessions, video_learning, workspace
from deeptutor.api.routers.auth import _install_request_workspace
from deeptutor.book.models import Book
from deeptutor.book.storage import get_book_storage
from deeptutor.services.session import get_sqlite_session_store
from deeptutor.services.workspace.activity import WorkspaceActivityMiddleware
from deeptutor.services.workspace.context import workspace_context
from deeptutor.video_learning.service import get_timed_media_store
from tests.services.workspace.test_data_scope import account as account


@pytest.fixture
def scoped_client(account):
    async def select(request: Request):
        _install_request_workspace(request)

    app = FastAPI(dependencies=[Depends(select)])
    app.add_middleware(WorkspaceActivityMiddleware)
    app.include_router(book.router, prefix="/api/book")
    app.include_router(mastery_path.router, prefix="/api/mastery-paths")
    app.include_router(reading.router, prefix="/api/reading")
    app.include_router(video_learning.router, prefix="/api/video-learning")
    app.include_router(sessions.router, prefix="/api/sessions")
    app.include_router(workspace.settings_router, prefix="/api/settings/workspace")
    with TestClient(app) as client:
        yield client


def test_http_lists_direct_access_and_uploads_are_isolated(account, scoped_client):
    client = scoped_client
    a = account.create_workspace("A")["workspace_id"]
    b = account.create_workspace("B")["workspace_id"]
    headers = {"X-DeepTutor-Workspace": a}
    uploaded = client.post(
        "/api/reading/materials",
        headers=headers,
        files={"file": ("lesson.txt", b"A private lesson about vectors.", "text/plain")},
    )
    assert uploaded.status_code == 200, uploaded.text
    material_id = uploaded.json()["material_id"]
    topic = client.post(
        "/api/mastery-paths/topics",
        headers=headers,
        json={"name": "Private path", "goal": "Learn vectors"},
    )
    assert topic.status_code == 200, topic.text
    topic_id = topic.json()["path_id"]
    with workspace_context(a):
        get_book_storage().save_book(Book(id="bk_scope", title="Private Book"))
        get_timed_media_store().save(
            {
                "material_id": "0123456789abcdef",
                "type": "timed_media",
                "metadata": {"title": "Private Video"},
            }
        )
        session = asyncio.run(get_sqlite_session_store().create_session("Private conversation"))
    for endpoint in (
        f"/api/mastery-paths/topics/{topic_id}",
        f"/api/reading/materials/{material_id}",
        "/api/video-learning/materials/0123456789abcdef",
        f"/api/sessions/{session['id']}",
    ):
        response = client.get(endpoint, headers=headers)
        assert response.status_code == 200, response.text
        for wid in ("", b):
            response = client.get(endpoint, headers={"X-DeepTutor-Workspace": wid})
            assert response.status_code == 404, (endpoint, response.text)
    # Registered books have account-independent identities. Their owner/admin
    # can follow either reference from another workspace; files stay at source.
    assert client.get("/api/book/books/bk_scope", headers=headers).status_code == 200
    for wid in ("", b):
        for ref in ("bk_scope", "book:bk_scope"):
            response = client.get(
                f"/api/book/books/{ref}", headers={"X-DeepTutor-Workspace": wid},
            )
            assert response.status_code == 200, response.text
            assert response.json()["book"]["title"] == "Private Book"
            assert response.json()["book"]["ref"] == "book:bk_scope"
        with workspace_context(wid):
            assert get_book_storage().load_book("bk_scope") is None
    assert client.get("/api/reading/materials", headers={"X-DeepTutor-Workspace": b}).json() == []
    assert client.get("/api/book/books", headers={"X-DeepTutor-Workspace": b}).json()["books"] == []
    conflict = client.get(
        "/api/reading/materials?dt_workspace=" + a, headers={"X-DeepTutor-Workspace": b}
    )
    assert conflict.status_code == 400


def test_registered_private_book_is_not_readable_by_another_account(
    account, scoped_client, tmp_path,
):
    from deeptutor.multi_user.models import CurrentUser, UserScope
    from deeptutor.multi_user.paths import user_context

    source = account.create_workspace("Private source")["workspace_id"]
    with workspace_context(source):
        get_book_storage().save_book(Book(id="bk_private_scope", title="Private Book"))
    assert scoped_client.get(
        "/api/book/books/bk_private_scope", params={"dt_workspace": source},
    ).status_code == 200
    reader = CurrentUser(
        "foreign-reader", "reader", "user",
        UserScope("user", "foreign-reader", tmp_path / "foreign-reader"),
    )
    with user_context(reader):
        for ref in ("bk_private_scope", "book:bk_private_scope"):
            response = scoped_client.get(f"/api/book/books/{ref}")
            assert response.status_code == 404, response.text
        assert scoped_client.get("/api/book/books").json()["books"] == []


def test_recovery_settings_remain_available_with_invalid_active_workspace(account, scoped_client):
    response = scoped_client.get("/api/settings/workspace/data/operations?dt_workspace=ws_missing")
    assert response.status_code == 200, response.text
