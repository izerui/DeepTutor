"""Video lesson for learners: the admin switch, availability, and AI daily usage.

Spec 2026-11-20 §2: every learner may use it by default and it counts toward
their AI daily usage; an administrator can turn it off for learners.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from deeptutor.agents.video_lesson import capability as capability_module
from deeptutor.services.config import capabilities_settings


@pytest.fixture
def switch(monkeypatch: pytest.MonkeyPatch):
    params = {"temperature": 0.5, "max_tokens": 24000, "learners_allowed": True, "max_attempts": 3}
    monkeypatch.setattr(capabilities_settings, "get_video_lesson_params", lambda: dict(params))
    return params


def test_learners_are_allowed_by_default(as_account, switch) -> None:
    with as_account("sara"):
        assert capability_module.acting_learner()
        capability_module.ensure_allowed()


def test_switch_off_refuses_learners_with_a_clear_error_but_not_admins(as_account, switch) -> None:
    switch["learners_allowed"] = False
    for learner in ("sara", "uma", "tina"):
        with as_account(learner):
            with pytest.raises(capability_module.VideoLessonDisabled) as caught:
                capability_module.ensure_allowed()
            assert "turned off for learners" in str(caught.value)
            assert caught.value.code == "video_lesson_disabled"
    with as_account("adam"):
        assert not capability_module.acting_learner()
        capability_module.ensure_allowed()


def test_availability_explains_the_switch_and_the_runtime(as_account, switch, monkeypatch) -> None:
    from deeptutor.agents.video_lesson import runtime
    from deeptutor.app.facade import DeepTutorApp

    app = SimpleNamespace(resolve_capability=lambda value: value)
    monkeypatch.setattr(runtime, "tools_present", lambda: True)
    with as_account("sara"):
        assert DeepTutorApp.get_capability_availability(app, "video_lesson").available
    switch["learners_allowed"] = False
    with as_account("sara"):
        availability = DeepTutorApp.get_capability_availability(app, "video_lesson")
        assert not availability.available and "turned off" in availability.install_hint
    with as_account("adam"):
        assert DeepTutorApp.get_capability_availability(app, "video_lesson").available
        monkeypatch.setattr(runtime, "tools_present", lambda: False)
        availability = DeepTutorApp.get_capability_availability(app, "video_lesson")
        assert not availability.available
        assert "contact your administrator" in availability.install_hint


def test_runtime_status_is_identical_for_every_account(as_account, monkeypatch, mu_isolated_root) -> None:
    """One shared install under data/system, not one per user."""
    from deeptutor.agents.video_lesson import runtime

    monkeypatch.delenv("DEEPTUTOR_REMOTION_RUNTIME", raising=False)
    seen = set()
    for name in ("adam", "sara", "uma"):
        with as_account(name):
            seen.add(runtime.runtime_dir())
    assert seen == {mu_isolated_root / "data" / "system" / "runtime" / "remotion"}


# ── AI daily usage ───────────────────────────────────────────────────


def _limit(name: str, tokens: int) -> None:
    from deeptutor.multi_user.grants import load_grant, save_grant
    from deeptutor.multi_user.identity import get_user

    uid = get_user(name)["id"]
    save_grant(uid, {**load_grant(uid), "ai_daily_tokens": tokens})


def _spend(tokens: int) -> None:
    import time
    import uuid

    from deeptutor.services.llm.usage_ledger import ledger_path, record_call

    record_call(
        ledger_path(), {"call_id": uuid.uuid4().hex, "total_tokens": tokens},
        started_at=time.time(), source="chat",
    )


@pytest.fixture
def fake_provider(monkeypatch: pytest.MonkeyPatch):
    """A provider below the real factory, so the real usage metering runs."""
    from deeptutor.services.llm import factory
    from deeptutor.services.llm.provider_core.base import LLMProvider, LLMResponse

    seen: dict[str, Any] = {}

    class Provider(LLMProvider):
        provider_name = "fake"

        async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7, **kw):
            seen.update(max_tokens=max_tokens, temperature=temperature, messages=messages)
            return LLMResponse(
                content="```tsx\nexport const Lesson = () => null;\n```",
                usage={"prompt_tokens": 700, "completion_tokens": 300, "total_tokens": 1000},
            )

        def get_default_model(self) -> str:
            return "fake-model"

    @contextmanager
    def acquire(config=None):
        yield Provider()

    monkeypatch.setattr(factory, "acquire_runtime_provider", acquire)
    monkeypatch.setattr(
        "deeptutor.agents.base_agent.get_agent_params",
        lambda _name: {"temperature": 0.5, "max_tokens": 24000},
    )
    monkeypatch.setattr(
        "deeptutor.agents.base_agent.get_llm_config",
        lambda: SimpleNamespace(
            api_key="k", base_url="http://127.0.0.1:9", model="fake-model",
            api_version=None, binding="openai",
        ),
    )
    return seen


def _write_once(max_tokens: int = 24000):
    from deeptutor.agents.video_lesson.lesson_writer import LessonWriterAgent
    from deeptutor.services.llm import metrics

    async def run():
        collector = metrics.TurnUsage()
        token = metrics.current_usage.set(collector)
        try:
            writer = LessonWriterAgent(language="en")
            writer.start(user_input="Explain a cobweb plot", history="")
            return await writer.write(attempt=1, max_tokens=max_tokens, temperature=0.5)
        finally:
            metrics.current_usage.reset(token)

    return asyncio.run(run())


def test_writing_a_lesson_is_counted_in_the_learners_daily_usage(as_account, fake_provider) -> None:
    from deeptutor.services.llm.usage_limit import status_for_current_user

    _limit("sara", 100_000)
    with as_account("sara"):
        before = status_for_current_user()["used"]
        draft = _write_once(max_tokens=9000)
        after = status_for_current_user()["used"]
    assert draft.code.startswith("export const Lesson")
    assert after - before == 1000
    # The admin's writer budget reaches the provider.
    assert fake_provider["max_tokens"] == 9000


def test_a_learner_over_todays_limit_cannot_start_a_lesson(as_account, fake_provider) -> None:
    from deeptutor.services.llm.usage_limit import AiLimitReached

    _limit("sara", 1000)
    with as_account("sara"):
        _spend(1000)
        with pytest.raises(AiLimitReached):
            _write_once()
    assert "messages" not in fake_provider


# ── admin endpoints (batch 4) ────────────────────────────────────────


@pytest.fixture
def admin_http(teaching_users, monkeypatch, tmp_path):
    """The capability settings router behind real auth; settings files under tmp."""
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    import deeptutor.api.routers.auth as auth_router
    from deeptutor.api.routers import capabilities_settings as settings_router
    from deeptutor.services.auth import TokenPayload
    from deeptutor.utils.config_manager import ConfigManager

    root = tmp_path / "settings-home"
    settings_dir = root / "data" / "user" / "settings"
    settings_dir.mkdir(parents=True)
    monkeypatch.setattr(capabilities_settings, "PROJECT_ROOT", root)
    manager = ConfigManager()
    monkeypatch.setattr(manager, "config_path", settings_dir / "main.yaml")
    monkeypatch.setattr(manager, "_config_cache", {})
    monkeypatch.setattr(manager, "_last_mtime", 0.0)
    monkeypatch.delenv("DEEPTUTOR_NPM_REGISTRY", raising=False)

    tokens = {
        name: TokenPayload(username=name, role=record["role"], user_id=record["id"])
        for name, record in teaching_users.items()
    }
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "decode_token", lambda token: tokens.get(token))
    app = FastAPI()
    app.include_router(
        settings_router.router,
        prefix="/api/capabilities",
        dependencies=[Depends(auth_router.require_auth)],
    )
    client = TestClient(app)

    def call(name: str, method: str, url: str, **kwargs):
        return client.request(method, url, headers={"Authorization": f"Bearer {name}"}, **kwargs)

    return call


@pytest.fixture
def fake_runtime(monkeypatch, tmp_path):
    from deeptutor.agents.video_lesson import layout_check, runtime

    state = {"installs": [], "installed": False}

    def status():
        return runtime.RuntimeStatus(
            available=True, reason="ok", node="/usr/bin/node", node_version="v22.22.0",
            npm="/usr/bin/npm", installed=state["installed"], runtime_dir=str(tmp_path / "rt"),
        )

    async def ensure_runtime(*, registry=None):
        state["installs"].append(registry)
        state["installed"] = True
        return tmp_path / "rt"

    monkeypatch.setattr(runtime, "runtime_status", status)
    monkeypatch.setattr(runtime, "ensure_runtime", ensure_runtime)
    monkeypatch.setattr(layout_check, "chrome_path", lambda: None)
    return state


VIDEO_LESSON_ADMIN_CALLS = [
    ("GET", "/api/capabilities/video-lesson/settings", None),
    ("PUT", "/api/capabilities/video-lesson/settings", {"learners_allowed": False}),
    ("GET", "/api/capabilities/video-lesson/status", None),
    ("POST", "/api/capabilities/video-lesson/install", None),
]


@pytest.mark.parametrize("learner", ["sara", "uma", "tina"])
@pytest.mark.parametrize("method,url,body", VIDEO_LESSON_ADMIN_CALLS)
def test_learners_cannot_see_or_change_video_lesson_admin_settings(
    admin_http, fake_runtime, learner, method, url, body
) -> None:
    response = admin_http(learner, method, url, json=body)
    assert response.status_code == 403
    assert fake_runtime["installs"] == []
    assert capabilities_settings.get_video_lesson_params()["learners_allowed"] is True


def test_admin_manages_video_lesson_and_learners_feel_it(
    admin_http, fake_runtime, as_account
) -> None:
    status = admin_http("adam", "GET", "/api/capabilities/video-lesson/status").json()
    assert status["runtime"]["available"] is True
    assert status["runtime"]["installed"] is False
    assert status["browser"] == {"available": False, "path": None}
    assert status["min_node_major"] == 22

    saved = admin_http(
        "adam",
        "PUT",
        "/api/capabilities/video-lesson/settings",
        json={
            "learners_allowed": False,
            "max_tokens": 8000,
            "max_attempts": 2,
            "layout_check": True,
            "npm_registry": "https://registry.npmmirror.com",
        },
    )
    assert saved.status_code == 200
    assert saved.json()["npm_registry"] == "https://registry.npmmirror.com"
    assert admin_http("adam", "GET", "/api/capabilities/video-lesson/settings").json() == saved.json()

    bad = admin_http(
        "adam", "PUT", "/api/capabilities/video-lesson/settings", json={"npm_registry": "ftp://x"}
    )
    assert bad.status_code == 422

    installed = admin_http("adam", "POST", "/api/capabilities/video-lesson/install")
    assert installed.status_code == 200 and installed.json()["runtime"]["installed"] is True
    assert fake_runtime["installs"] == ["https://registry.npmmirror.com"]

    # The settings bind on the server: the learner switch refuses learners.
    with as_account("sara"):
        with pytest.raises(capability_module.VideoLessonDisabled):
            capability_module.ensure_allowed()
    params = capabilities_settings.get_video_lesson_params()
    assert params["max_tokens"] == 8000 and params["max_attempts"] == 2
    assert params["layout_check"] is True


def test_a_failed_install_is_reported_to_the_admin(admin_http, monkeypatch) -> None:
    from deeptutor.agents.video_lesson import runtime

    async def boom(*, registry=None):
        raise runtime.VideoLessonRuntimeUnavailable("Node.js 22+ is not installed.")

    monkeypatch.setattr(runtime, "ensure_runtime", boom)
    response = admin_http("adam", "POST", "/api/capabilities/video-lesson/install")
    assert response.status_code == 503
    assert "Node.js" in response.json()["detail"]


def test_the_catalog_says_why_a_learner_cannot_start_it(as_account, switch, monkeypatch) -> None:
    """Batch 5: the composer shows a note where the entry would be, not nothing."""
    from deeptutor.agents.video_lesson import runtime

    monkeypatch.setattr(runtime, "tools_present", lambda: True)
    with as_account("sara"):
        assert capability_module.unavailable_reason() == ""
    switch["learners_allowed"] = False
    with as_account("sara"):
        assert capability_module.unavailable_reason() == "disabled_by_admin"
    with as_account("adam"):
        assert capability_module.unavailable_reason() == ""
        monkeypatch.setattr(runtime, "tools_present", lambda: False)
        assert capability_module.unavailable_reason() == "runtime_unavailable"
