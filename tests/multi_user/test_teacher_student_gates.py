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

    set_role("teacher1", "student")
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


# ------------------------------------------------- student write gates


@pytest.fixture
def gate_client(role_env):
    from deeptutor.api.routers import knowledge, mastery_path
    from deeptutor.api.routers.auth import require_auth

    app = FastAPI()
    auth = [Depends(require_auth)]
    app.include_router(knowledge.router, prefix="/api", dependencies=auth)
    app.include_router(mastery_path.router, prefix="/api/mastery-paths", dependencies=auth)
    return TestClient(app), role_env[0]


def test_student_cannot_create_knowledge_base_by_default(gate_client):
    client, _users = gate_client
    response = client.post(
        "/api/knowledge-bases",
        headers=_bearer("student-token"),
        data={"name": "notes"},
    )
    assert response.status_code == 403
    assert "knowledge base" in response.json()["detail"].lower()


def test_student_gate_is_installed_on_kb_routes(gate_client):
    from deeptutor.api.routers.knowledge import deny_student_kb_writes

    client, _users = gate_client
    # Newer FastAPI wraps included routers as _IncludedRouter; look inside.
    all_routes = list(client.app.routes)
    for r in client.app.routes:
        orig = getattr(r, "original_router", None)
        if orig:
            all_routes.extend(orig.routes)
    route_deps = {
        dep.call
        for route in all_routes
        if getattr(route, "path", "") == "/knowledge-bases/list"
        for dep in route.dependant.dependencies
    }
    # The gate is installed on every route, but it must not block GETs.
    assert deny_student_kb_writes in route_deps


@pytest.mark.asyncio
async def test_deny_student_kb_writes_respects_policy(role_env, as_user):
    from types import SimpleNamespace

    from fastapi import HTTPException

    from deeptutor.api.routers.knowledge import deny_student_kb_writes
    from deeptutor.multi_user.grants import save_grant

    users, _tokens = role_env
    student = users["student"]
    with as_user(student["id"], role="student", username="student1"):
        await deny_student_kb_writes(SimpleNamespace(method="GET"))
        with pytest.raises(HTTPException) as exc:
            await deny_student_kb_writes(SimpleNamespace(method="POST"))
        assert exc.value.status_code == 403
        save_grant(student["id"], {"student_policy": {"allow_private_kb": True}})
        await deny_student_kb_writes(SimpleNamespace(method="DELETE"))
    teacher = users["teacher"]
    with as_user(teacher["id"], role="teacher", username="teacher1"):
        await deny_student_kb_writes(SimpleNamespace(method="POST"))


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/mastery-paths/topics/draft", {"name": "x", "goal": "y"}),
        ("post", "/api/mastery-paths/topics", {"name": "x", "goal": "y"}),
        ("post", "/api/mastery-paths/progress/p1/init-modules", {"modules": []}),
        ("post", "/api/mastery-paths/progress/p1/import-from-book", {"chapters": []}),
        ("post", "/api/mastery-paths/progress/p1/generate-from-notebook", {"records": []}),
        ("post", "/api/mastery-paths/progress/p1/generate-from-reading", {}),
    ],
)
def test_student_without_personal_mastery_is_blocked(gate_client, method, path, body):
    from deeptutor.multi_user.grants import save_grant

    client, users = gate_client
    save_grant(users["student"]["id"], {"student_policy": {"allow_personal_mastery": False}})
    response = getattr(client, method)(path, headers=_bearer("student-token"), json=body)
    assert response.status_code == 403
    assert "mastery path" in response.json()["detail"].lower()


def test_student_with_default_policy_passes_mastery_gate(role_env, as_user):
    from deeptutor.api.routers.mastery_path import require_personal_mastery_creation

    users, _tokens = role_env
    with as_user(users["student"]["id"], role="student", username="student1"):
        assert require_personal_mastery_creation() is None
