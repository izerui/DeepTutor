"""Spec 2026-11-06: learners keep their personal settings; shared config is admin-only.

* Saving memory or capability settings rewrites the deployment's shared YAML,
  and the connection tests spend shared provider credentials, so a learner is
  refused (403) while an admin passes.
* "Learning restrictions" are gone: an account whose grant still stores a
  ``learning_policy`` uses the market, courses, practice sets and every
  personal settings page like any other learner.
"""

from __future__ import annotations

import json

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

#: A legacy grant written before 2026-11-06, with the tightest old restriction.
LEGACY_POLICY = {
    "learning_policy": {
        "age_band": "9-12",
        "locked_persona": "teacher",
        "allowed_capabilities": ["chat"],
        "default_capability": "chat",
        "allowed_surfaces": ["reading"],
        "reading": {"allow_upload": False, "material_ids": [], "extensions": []},
    }
}


@pytest.fixture
def http(teaching_users, monkeypatch):
    """The production routers behind the same auth dependencies ``main.py`` mounts."""
    import deeptutor.api.routers.auth as auth_router
    from deeptutor.api.routers import (
        capabilities_settings,
        catalog,
        courses,
        memory,
        practice_sets,
        settings,
        system,
    )
    from deeptutor.multi_user.grants import grant_path
    from deeptutor.services.auth import TokenPayload

    tokens = {
        name: TokenPayload(username=name, role=record["role"], user_id=record["id"])
        for name, record in teaching_users.items()
    }
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "decode_token", lambda token: tokens.get(token))
    # Sam is a legacy restricted learner: the stored policy must be ignored.
    path = grant_path(teaching_users["sam"]["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(LEGACY_POLICY), encoding="utf-8")

    auth = [Depends(auth_router.require_auth)]
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    app.include_router(memory.router, prefix="/api/memory", dependencies=auth)
    app.include_router(
        capabilities_settings.router, prefix="/api/capabilities", dependencies=auth
    )
    app.include_router(system.router, prefix="/api/system", dependencies=auth)
    app.include_router(settings.router, prefix="/api/settings", dependencies=auth)
    app.include_router(catalog.router, prefix="/api/catalog", dependencies=auth)
    app.include_router(courses.router, prefix="/api/courses", dependencies=auth)
    app.include_router(practice_sets.router, prefix="/api/practice-sets", dependencies=auth)
    client = TestClient(app)

    def call(name: str, method: str, url: str, **kwargs):
        headers = {"Authorization": f"Bearer {name}"}
        return client.request(method, url, headers=headers, **kwargs)

    return call


@pytest.fixture
def stub_shared_writes(monkeypatch):
    """Keep the admin-side saves off the real shared YAML files."""
    saved: dict[str, object] = {}

    def save_memory(payload):
        saved["memory"] = payload

    def save_capabilities(payload):
        saved["capabilities"] = payload
        return {"saved": True}

    monkeypatch.setattr(
        "deeptutor.services.memory.settings.save_memory_settings", save_memory
    )
    monkeypatch.setattr(
        "deeptutor.services.memory.settings.memory_settings_dict", lambda: {"saved": True}
    )
    monkeypatch.setattr(
        "deeptutor.services.config.capabilities_settings.save_capabilities_settings",
        save_capabilities,
    )
    return saved


@pytest.mark.parametrize("learner", ["sara", "sam", "tina", "uma"])
def test_learners_cannot_change_shared_memory_or_capability_settings(
    http, stub_shared_writes, learner
):
    assert http(learner, "PUT", "/api/memory/settings", json={}).status_code == 403
    assert http(learner, "PUT", "/api/capabilities/settings", json={}).status_code == 403
    assert stub_shared_writes == {}


def test_admin_changes_shared_memory_and_capability_settings(http, stub_shared_writes):
    assert http("adam", "PUT", "/api/memory/settings", json={"a": 1}).status_code == 200
    assert http("adam", "PUT", "/api/capabilities/settings", json={"b": 2}).status_code == 200
    assert stub_shared_writes == {"memory": {"a": 1}, "capabilities": {"b": 2}}


@pytest.mark.parametrize("service", ["llm", "embeddings", "search"])
def test_connection_tests_spend_shared_credentials_only_for_admins(
    http, monkeypatch, service
):
    from deeptutor.api.routers import system

    def boom():
        raise ValueError("synthetic: not configured")

    monkeypatch.setattr(system, "get_llm_config", boom)
    monkeypatch.setattr(system, "get_embedding_config", boom)
    monkeypatch.setattr(system, "resolve_search_runtime_config", boom)
    url = f"/api/system/test/{service}"
    assert http("sam", "POST", url).status_code == 403
    assert http("sara", "POST", url).status_code == 403
    response = http("adam", "POST", url)
    assert response.status_code == 200
    assert response.json()["success"] is False


def test_legacy_restricted_learner_uses_market_courses_and_practice_sets(http):
    for url in (
        "/api/catalog/taxonomy",
        "/api/catalog/items",
        "/api/courses",
        "/api/practice-sets/assigned",
    ):
        response = http("sam", "GET", url)
        assert response.status_code == 200, (url, response.text)


def test_legacy_restricted_learner_keeps_every_personal_settings_page(http):
    """General, appearance, learner profile, conversation, tools, voice playback."""
    status = http("sam", "GET", "/api/auth/status").json()
    assert status["preset"] == "standard"
    assert "learning_policy" not in status

    checks = [
        ("GET", "/api/settings", None),
        ("PUT", "/api/settings/ui", {"language": "zh", "theme": "dark"}),
        ("PUT", "/api/settings/language", {"language": "en"}),
        ("PUT", "/api/settings/theme", {"theme": "light"}),
        ("GET", "/api/settings/chat-starters", None),
        ("PUT", "/api/settings/chat-starters", {"trace_count": 3}),
        ("PUT", "/api/settings/chat-response-timeout", {"chat_response_timeout": 120}),
        ("PUT", "/api/settings/enabled-tools", {"enabled_tools": []}),
        ("PUT", "/api/settings/voice-autoplay", {"voice_autoplay": True}),
        ("PUT", "/api/settings/voice-math-speak", {"voice_math_speak": False}),
        ("GET", "/api/auth/profile/learner-profile", None),
        ("PUT", "/api/auth/profile/learner-profile", {"grade_level": "primary_4"}),
    ]
    for method, url, body in checks:
        response = http("sam", method, url, json=body) if body is not None else http(
            "sam", method, url
        )
        assert response.status_code == 200, (method, url, response.text)
    profile = http("sam", "GET", "/api/auth/profile/learner-profile").json()
    assert profile["learner_profile"]["grade_level"] == "primary_4"
