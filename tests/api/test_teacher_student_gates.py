"""HTTP gates for the teacher and student roles."""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest


# ---------------------------------------------------------------------------
# Inline isolation fixtures (same as tests/multi_user/conftest.py) so this
# test module can run both alone and together with tests/multi_user.
# ---------------------------------------------------------------------------


@pytest.fixture
def mu_isolated_root(tmp_path, monkeypatch) -> Path:
    """Redirect every ``multi_user`` global path under ``tmp_path``."""
    from deeptutor.multi_user import (
        audit,
        device_credentials,
        grants,
        guardians,
        identity,
        paths,
    )

    project_root = tmp_path
    admin_root = (project_root / "data").resolve()
    users_root = admin_root / "users"
    system_root = admin_root / "system"

    monkeypatch.setattr(paths, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(paths, "USERS_ROOT", users_root)
    monkeypatch.setattr(paths, "SYSTEM_ROOT", system_root)
    monkeypatch.setattr(paths, "ADMIN_WORKSPACE_ROOT", admin_root)
    monkeypatch.setattr(paths, "LEGACY_MULTI_USER_ROOT", project_root / "multi-user")
    monkeypatch.setattr(paths, "_path_services", {})

    monkeypatch.setattr(identity, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(identity, "SYSTEM_ROOT", system_root)
    monkeypatch.setattr(identity, "AUTH_DIR", system_root / "auth")
    monkeypatch.setattr(identity, "USERS_FILE", system_root / "auth" / "users.json")
    monkeypatch.setattr(identity, "SECRET_FILE", system_root / "auth" / "auth_secret")
    monkeypatch.setattr(
        identity,
        "LEGACY_USERS_FILE",
        project_root / "data" / "user" / "auth_users.json",
    )
    monkeypatch.setattr(
        identity,
        "LEGACY_SECRET_FILE",
        project_root / "data" / "user" / "auth_secret",
    )

    monkeypatch.setattr(grants, "GRANTS_DIR", system_root / "grants")
    monkeypatch.setattr(guardians, "GUARDIANS_FILE", system_root / "guardians.json")
    monkeypatch.setattr(audit, "SYSTEM_ROOT", system_root)
    monkeypatch.setattr(
        device_credentials,
        "DEVICE_CREDENTIALS_FILE",
        system_root / "auth" / "device_credentials.json",
    )

    from deeptutor.services import auth as auth_service

    monkeypatch.setattr(auth_service, "AUTH_USERNAME", "")
    monkeypatch.setattr(auth_service, "AUTH_PASSWORD_HASH", "")

    admin_root.mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def seed_user(mu_isolated_root):
    """Create a user record on disk and return the resulting record dict."""

    def _seed(username: str, password: str = "password1234", role: str = "user") -> dict:
        from deeptutor.multi_user.identity import save_user
        from deeptutor.services.auth import hash_password

        return save_user(username, hash_password(password), role=role)  # type: ignore[arg-type]

    return _seed


# ---------------------------------------------------------------------------
# Test fixtures and tests
# ---------------------------------------------------------------------------


@pytest.fixture
def role_env(mu_isolated_root, monkeypatch, seed_user):
    """Seed one account per role and map ``<role>-token`` to each of them."""
    import deeptutor.api.routers.auth as auth_router
    from deeptutor.services.auth import TokenPayload

    users = {role: seed_user(f"{role}1", role=role) for role in ("admin", "teacher", "student", "user")}
    tokens = {
        f"{role}-token": TokenPayload(username=f"{role}1", role=role, user_id=record["id"])
        for role, record in users.items()
    }
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "decode_token", lambda token: tokens.get(token))
    return users, tokens


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def teacher_client(role_env):
    from deeptutor.api.routers.auth import require_teacher_or_admin

    app = FastAPI()

    @app.get("/teach", dependencies=[Depends(require_teacher_or_admin)])
    async def teach() -> dict:
        return {"ok": True}

    return TestClient(app)


@pytest.mark.parametrize(
    ("token", "expected"),
    [("admin-token", 200), ("teacher-token", 200), ("student-token", 403), ("user-token", 403)],
)
def test_require_teacher_or_admin(teacher_client, token, expected):
    assert teacher_client.get("/teach", headers=_bearer(token)).status_code == expected


def test_require_teacher_or_admin_uses_live_role(teacher_client):
    from deeptutor.services.auth import set_role

    set_role("teacher1", "user")
    assert teacher_client.get("/teach", headers=_bearer("teacher-token")).status_code == 403


def test_require_teacher_or_admin_rejects_anonymous(teacher_client):
    assert teacher_client.get("/teach").status_code == 401


@pytest.fixture
def status_client(role_env):
    import deeptutor.api.routers.auth as auth_router

    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    return TestClient(app)


def test_status_reports_teacher(status_client):
    body = status_client.get("/api/auth/status", headers=_bearer("teacher-token")).json()
    assert body["role"] == "teacher"
    assert body["is_teacher"] is True
    assert body["is_admin"] is False
    assert body["student_policy"] is None


def test_status_reports_student_policy(status_client):
    body = status_client.get("/api/auth/status", headers=_bearer("student-token")).json()
    assert body["role"] == "student"
    assert body["is_teacher"] is False
    assert body["student_policy"] == {
        "allow_personal_mastery": True,
        "allow_private_kb": False,
    }


def test_status_role_follows_live_record(status_client):
    from deeptutor.services.auth import set_role

    set_role("user1", "teacher")
    body = status_client.get("/api/auth/status", headers=_bearer("user-token")).json()
    assert body["role"] == "teacher"
    assert body["is_teacher"] is True
