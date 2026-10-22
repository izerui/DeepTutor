"""HTTP gates for the teacher and student roles."""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest


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
