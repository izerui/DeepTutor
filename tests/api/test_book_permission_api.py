from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from deeptutor.api.routers.auth import require_admin
from deeptutor.book.models import Book
from deeptutor.book.storage import BookStorage


@pytest.fixture
def permission_client(tmp_path, monkeypatch):
    from deeptutor.api.routers import multi_user as router
    from deeptutor.book import engine as engine_module
    from deeptutor.book import storage as storage_module
    from deeptutor.multi_user import audit, grants, identity, paths
    from deeptutor.multi_user.paths import get_admin_path_service

    admin_root = (tmp_path / "data").resolve()
    system_root = admin_root / "system"
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(paths, "ADMIN_WORKSPACE_ROOT", admin_root)
    monkeypatch.setattr(paths, "USERS_ROOT", admin_root / "users")
    monkeypatch.setattr(paths, "SYSTEM_ROOT", system_root)
    monkeypatch.setattr(paths, "LEGACY_MULTI_USER_ROOT", tmp_path / "multi-user")
    monkeypatch.setattr(paths, "_path_services", {})
    monkeypatch.setattr(identity, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(identity, "SYSTEM_ROOT", system_root)
    monkeypatch.setattr(identity, "AUTH_DIR", system_root / "auth")
    monkeypatch.setattr(identity, "USERS_FILE", system_root / "auth" / "users.json")
    monkeypatch.setattr(identity, "SECRET_FILE", system_root / "auth" / "auth_secret")
    monkeypatch.setattr(identity, "LEGACY_USERS_FILE", tmp_path / "missing-users.json")
    monkeypatch.setattr(identity, "LEGACY_SECRET_FILE", tmp_path / "missing-secret")
    monkeypatch.setattr(grants, "GRANTS_DIR", system_root / "grants")
    monkeypatch.setattr(audit, "SYSTEM_ROOT", system_root)
    storage_module._storages.clear()
    engine_module._engines.clear()

    identity.save_user("root", "hash", role="admin")
    alice = identity.save_user("alice", "hash", role="user")
    BookStorage(path_service=get_admin_path_service()).save_book(
        Book(id="bk_shared", title="Shared")
    )

    app = FastAPI()
    app.include_router(router.router, prefix="/api/multi-user")
    app.dependency_overrides[require_admin] = lambda: object()
    return TestClient(app), alice


def test_retired_catalog_and_create_only_permission(permission_client) -> None:
    client, alice = permission_client
    catalog = client.get("/api/multi-user/admin/books")
    assert catalog.status_code == 404

    response = client.put(
        f"/api/multi-user/users/{alice['id']}/book-permission",
        json={
            "create": False,
            "default": "none",
            "books": {"bk_shared": "edit"},
        },
    )
    assert response.status_code == 200
    assert response.json()["permission"] == {
        "create": False,
    }
    assert client.get(
        f"/api/multi-user/users/{alice['id']}/book-permission"
    ).json()["permission"] == {"create": False}


def test_permission_api_ignores_retired_book_levels(permission_client) -> None:
    client, alice = permission_client
    unknown = client.put(
        f"/api/multi-user/users/{alice['id']}/book-permission",
        json={"books": {"bk_missing": "read"}},
    )
    assert unknown.status_code == 200
    assert unknown.json()["permission"] == {"create": True}

    delete = client.put(
        f"/api/multi-user/users/{alice['id']}/book-permission",
        json={"books": {"bk_shared": "delete"}},
    )
    assert delete.status_code == 200
    assert delete.json()["permission"] == {"create": True}


def test_permission_api_ignores_retired_default(permission_client) -> None:
    client, alice = permission_client
    response = client.put(
        f"/api/multi-user/users/{alice['id']}/book-permission",
        json={"default": "edit"},
    )
    assert response.status_code == 200
    assert response.json()["permission"] == {"create": True}


def test_policy_request_cannot_add_legacy_grants(permission_client):
    from deeptutor.multi_user import identity

    client, alice = permission_client
    users = identity.load_users()
    users["alice"]["book_permission"] = {"default": "none", "books": {"bk_old": "read"}}
    identity._write_users(users)
    url = f"/api/multi-user/users/{alice['id']}/book-permission"
    assert client.put(url, json={
        "create": False, "default": "read", "books": {"bk_new": "edit"},
    }).json()["permission"] == {"create": False}
    assert identity.load_users()["alice"]["book_permission"] == {
        "create": False, "default": "none", "books": {"bk_old": "read"},
    }
    assert client.put(url, json={"create": "false"}).status_code == 422
