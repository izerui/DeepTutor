"""The video lesson pipeline: admin budgets, attempts, the stored result and its URL.

Node, npm, the LLM and TTS are replaced by fakes; the real kit is copied and the
real player page is written, so the emitted URL is checked against the real
public-output allowlist.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from deeptutor.agents.video_lesson import capability as capability_module
from deeptutor.agents.video_lesson import runtime
from deeptutor.agents.video_lesson.capability import VideoLessonCapability
from deeptutor.agents.video_lesson.lesson_writer import parse_draft
from deeptutor.core.context import UnifiedContext
from deeptutor.core.stream import StreamEvent, StreamEventType
from deeptutor.runtime.stream_bus import StreamBus
from deeptutor.services.config import capabilities_settings
from deeptutor.services.path_service import PathService

GOOD_REPLY = """```json
{"title": "Golden ratio", "summary": "Cobweb first, then the proof.",
 "narration": [{"id": "s1", "text": "First line."}]}
```
```tsx
export const LESSON = {durationInFrames: 300, fps: 30, width: 1920, height: 1080};
export const Lesson = () => null;
```"""


async def _collect(run) -> list[StreamEvent]:
    bus = StreamBus()
    events: list[StreamEvent] = []

    async def consume() -> None:
        async for event in bus.subscribe():
            events.append(event)

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0)
    try:
        await run(bus)
    finally:
        await asyncio.sleep(0)
        await bus.close()
        await consumer
    return events


class FakeWriter:
    instances: list["FakeWriter"] = []
    replies: list[str] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.calls: list[dict[str, Any]] = []
        self.feedback: list[tuple[str, list[str]]] = []
        FakeWriter.instances.append(self)

    def set_trace_callback(self, callback) -> None:
        pass

    def start(self, *, user_input: str, history: str) -> None:
        self.user_input = user_input

    def share_conversation(self, other) -> None:
        pass

    def add_feedback(self, *, previous: str, stage: str, errors: list[str]) -> None:
        self.feedback.append((stage, errors))

    async def write(self, *, attempt: int, max_tokens=None, temperature=None):
        self.calls.append({"attempt": attempt, "max_tokens": max_tokens, "temperature": temperature})
        reply = FakeWriter.replies[min(attempt, len(FakeWriter.replies)) - 1]
        return parse_draft(reply)


@pytest.fixture
def pipeline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Fakes for everything outside the pipeline; returns a knob dict."""
    FakeWriter.instances = []
    FakeWriter.replies = [GOOD_REPLY]
    state: dict[str, Any] = {
        "params": {
            "temperature": 0.5,
            "max_tokens": 24000,
            "learners_allowed": True,
            "max_attempts": 3,
            "layout_check": False,
        },
        "layout": [],
        "layout_calls": 0,
        "builds": [],
        "build_ok": [True],
        "installs": 0,
    }
    paths = PathService(workspace_root=tmp_path / "data")

    monkeypatch.setattr(
        capabilities_settings, "get_video_lesson_params", lambda: dict(state["params"])
    )
    monkeypatch.setattr(
        "deeptutor.agents.video_lesson.lesson_writer.LessonWriterAgent", FakeWriter
    )
    monkeypatch.setattr(
        "deeptutor.services.model_selection.task_agent.task_model_agent", lambda factory: None
    )
    monkeypatch.setattr(
        "deeptutor.services.llm.config.get_llm_config",
        lambda: SimpleNamespace(api_key="k", base_url="u", api_version=None, model="m"),
    )
    monkeypatch.setattr("deeptutor.services.path_service.get_path_service", lambda: paths)
    monkeypatch.setattr(runtime, "tools_present", lambda: True)
    monkeypatch.setattr(
        capabilities_settings, "video_lesson_npm_registry", lambda: "https://mirror.example/npm/"
    )

    async def ensure_runtime(**kw):
        state["installs"] += 1
        state["registry"] = kw.get("registry")
        return tmp_path / "rt"

    async def build_lesson(lesson: Path, bundle: Path):
        index = len(state["builds"])
        state["builds"].append(lesson.read_text(encoding="utf-8"))
        ok = state["build_ok"][min(index, len(state["build_ok"]) - 1)]
        if ok:
            bundle.write_text('console.log("player")', encoding="utf-8")
            return runtime.BuildResult(True, "done", [], [{"frame": 299, "chars": 900}])
        return runtime.BuildResult(False, "render", ["frame 0: boom"], [])

    async def narrate(lines, language, cache=None):
        from deeptutor.agents.video_lesson.narration import estimate

        return {line.id: estimate(line.text, language) for line in lines}

    def check_layout(page: Path, frames: list[int]):
        assert page.name == "layout-check.html" and page.exists()
        index = state["layout_calls"]
        state["layout_calls"] += 1
        replies = state["layout"]
        return replies[min(index, len(replies) - 1)] if replies else []

    monkeypatch.setattr(
        "deeptutor.agents.video_lesson.layout_check.check_layout", check_layout
    )
    monkeypatch.setattr(runtime, "ensure_runtime", ensure_runtime)
    monkeypatch.setattr(runtime, "build_lesson", build_lesson)
    monkeypatch.setattr("deeptutor.agents.video_lesson.narration.narrate", narrate)
    state["paths"] = paths
    return state


def _context() -> UnifiedContext:
    return UnifiedContext(
        session_id="s1",
        user_message="Explain the golden ratio",
        active_capability="video_lesson",
        language="en",
        metadata={"turn_id": "turn-1", "conversation_context_text": ""},
    )


@pytest.mark.asyncio
async def test_success_stores_the_page_and_emits_a_playable_result(pipeline) -> None:
    events = await _collect(lambda bus: VideoLessonCapability().run(_context(), bus))

    assert [e.stage for e in events if e.type == StreamEventType.STAGE_START] == [
        "code_generation",
        "render_check",
        "render_output",
    ]
    result = next(e for e in events if e.type == StreamEventType.RESULT).metadata
    artifact = result["artifacts"][0]
    assert artifact["type"] == "html"
    assert artifact["url"] == "/files/outputs/workspace/chat/video_lesson/turn-1/artifacts/lesson.html"
    paths: PathService = pipeline["paths"]
    rel = artifact["url"].removeprefix("/files/outputs/")
    page = paths.resolve_public_output_path(rel)
    assert page is not None and 'console.log("player")' in page.read_text(encoding="utf-8")
    # The intermediate bundle is not left beside the source.
    assert not (page.parent.parent / "source" / "player.js").exists()
    assert result["render"]["duration_seconds"] == 10
    assert result["code"]["language"] == "tsx"
    assert any(
        e.type == StreamEventType.CONTENT and "Cobweb first" in e.content for e in events
    )
    writer = FakeWriter.instances[0]
    assert writer.calls == [{"attempt": 1, "max_tokens": 24000, "temperature": 0.5}]
    # The first install uses the admin's npm mirror.
    assert pipeline["registry"] == "https://mirror.example/npm/"


@pytest.mark.asyncio
async def test_admin_budget_and_attempts_are_enforced(pipeline) -> None:
    pipeline["params"].update(max_tokens=9000, max_attempts=2)
    pipeline["build_ok"] = [False]
    with pytest.raises(RuntimeError, match="after 2 attempts"):
        await _collect(lambda bus: VideoLessonCapability().run(_context(), bus))
    writer = FakeWriter.instances[0]
    assert [c["attempt"] for c in writer.calls] == [1, 2]
    assert {c["max_tokens"] for c in writer.calls} == {9000}
    assert len(pipeline["builds"]) == 2
    assert writer.feedback[0][0] == "render"


@pytest.mark.asyncio
async def test_a_fix_after_a_failed_check_is_used(pipeline) -> None:
    pipeline["build_ok"] = [False, True]
    events = await _collect(lambda bus: VideoLessonCapability().run(_context(), bus))
    result = next(e for e in events if e.type == StreamEventType.RESULT).metadata
    assert [a["stage"] for a in result["render"]["attempts"]] == ["render", "done"]


@pytest.mark.asyncio
async def test_missing_node_fails_before_any_model_call(pipeline, monkeypatch) -> None:
    monkeypatch.setattr(runtime, "tools_present", lambda: False)
    with pytest.raises(runtime.VideoLessonRuntimeUnavailable, match="contact your administrator"):
        await _collect(lambda bus: VideoLessonCapability().run(_context(), bus))
    assert FakeWriter.instances == []
    assert pipeline["installs"] == 0


@pytest.mark.asyncio
async def test_learners_switch_off_refuses_learners_only(pipeline, monkeypatch) -> None:
    pipeline["params"]["learners_allowed"] = False
    monkeypatch.setattr(capability_module, "acting_learner", lambda: True)
    with pytest.raises(capability_module.VideoLessonDisabled, match="turned off"):
        await _collect(lambda bus: VideoLessonCapability().run(_context(), bus))
    assert FakeWriter.instances == []

    monkeypatch.setattr(capability_module, "acting_learner", lambda: False)
    events = await _collect(lambda bus: VideoLessonCapability().run(_context(), bus))
    assert any(e.type == StreamEventType.RESULT for e in events)


# ── layout check (batch 3) ───────────────────────────────────────


@pytest.mark.asyncio
async def test_layout_check_is_off_by_default_and_never_opens_a_browser(pipeline) -> None:
    pipeline["layout"] = [["text overlaps"]]
    events = await _collect(lambda bus: VideoLessonCapability().run(_context(), bus))
    assert pipeline["layout_calls"] == 0
    result = next(e for e in events if e.type == StreamEventType.RESULT).metadata
    assert [a["stage"] for a in result["render"]["attempts"]] == ["done"]


@pytest.mark.asyncio
async def test_layout_issues_are_fed_back_when_the_admin_turned_it_on(pipeline) -> None:
    pipeline["params"]["layout_check"] = True
    pipeline["layout"] = [['text "a" overlaps text "b"'], []]
    events = await _collect(lambda bus: VideoLessonCapability().run(_context(), bus))
    result = next(e for e in events if e.type == StreamEventType.RESULT).metadata
    assert [a["stage"] for a in result["render"]["attempts"]] == ["done", "layout", "done"]
    writer = FakeWriter.instances[0]
    assert writer.feedback == [("layout", ['text "a" overlaps text "b"'])]
    assert pipeline["layout_calls"] == 2
    paths: PathService = pipeline["paths"]
    source = paths.get_agent_dir("video_lesson") / "turn-1" / "source"
    assert not (source / "player.good.js").exists()
    assert not (source / "layout-check.html").exists()


@pytest.mark.asyncio
async def test_no_chrome_skips_the_layout_check(pipeline) -> None:
    pipeline["params"]["layout_check"] = True
    pipeline["layout"] = [None]
    events = await _collect(lambda bus: VideoLessonCapability().run(_context(), bus))
    result = next(e for e in events if e.type == StreamEventType.RESULT).metadata
    assert [a["stage"] for a in result["render"]["attempts"]] == ["done", "layout_unavailable"]
    assert len(FakeWriter.instances[0].calls) == 1


@pytest.mark.asyncio
async def test_layout_rounds_share_the_attempt_budget_and_keep_the_good_lesson(pipeline) -> None:
    pipeline["params"].update(layout_check=True, max_attempts=2)
    pipeline["layout"] = [["overlap"]]
    # Attempt 2 (the layout fix) no longer compiles: the lesson from attempt 1 ships.
    pipeline["build_ok"] = [True, False]
    events = await _collect(lambda bus: VideoLessonCapability().run(_context(), bus))
    result = next(e for e in events if e.type == StreamEventType.RESULT).metadata
    assert [a["stage"] for a in result["render"]["attempts"]] == ["done", "layout", "render"]
    assert len(FakeWriter.instances[0].calls) == 2
    page = pipeline["paths"].resolve_public_output_path(
        result["artifacts"][0]["url"].removeprefix("/files/outputs/")
    )
    assert 'console.log("player")' in page.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_a_broken_probe_does_not_fail_the_lesson(pipeline, monkeypatch) -> None:
    pipeline["params"]["layout_check"] = True

    def boom(page, frames):
        raise RuntimeError("probe crashed")

    monkeypatch.setattr("deeptutor.agents.video_lesson.layout_check.check_layout", boom)
    events = await _collect(lambda bus: VideoLessonCapability().run(_context(), bus))
    result = next(e for e in events if e.type == StreamEventType.RESULT).metadata
    assert result["render"]["attempts"][-1]["stage"] == "layout_unavailable"


# ── admin settings storage ───────────────────────────────────────────


@pytest.fixture
def settings_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from deeptutor.utils.config_manager import ConfigManager

    root = tmp_path / "home"
    settings_dir = root / "data" / "user" / "settings"
    settings_dir.mkdir(parents=True)
    monkeypatch.setattr(capabilities_settings, "PROJECT_ROOT", root)
    manager = ConfigManager()
    monkeypatch.setattr(manager, "config_path", settings_dir / "main.yaml")
    monkeypatch.setattr(manager, "_config_cache", {})
    monkeypatch.setattr(manager, "_last_mtime", 0.0)
    return settings_dir


def test_defaults_are_lower_than_upstream(settings_files: Path) -> None:
    from deeptutor.services.setup.init import DEFAULT_AGENTS_SETTINGS

    assert capabilities_settings.get_video_lesson_params() == {
        "temperature": 0.5,
        "max_tokens": 24000,
        "learners_allowed": True,
        "max_attempts": 3,
        "layout_check": False,
    }
    assert (
        DEFAULT_AGENTS_SETTINGS["plugins"]["video_lesson"]
        == capabilities_settings._SIMPLE_LLM_DEFAULTS["video_lesson"]
    )


def test_admin_settings_round_trip_and_clamp(settings_files: Path) -> None:
    saved = capabilities_settings.save_capabilities_settings(
        {"video_lesson": {"learners_allowed": False, "max_attempts": 99, "max_tokens": 8000}}
    )
    assert saved["video_lesson"]["learners_allowed"] is False
    assert saved["video_lesson"]["max_attempts"] == capabilities_settings.VIDEO_LESSON_MAX_ATTEMPTS_LIMIT
    assert saved["video_lesson"]["max_tokens"] == 8000
    agents = yaml.safe_load((settings_files / "agents.yaml").read_text(encoding="utf-8"))
    assert agents["plugins"]["video_lesson"]["max_tokens"] == 8000
    main = yaml.safe_load((settings_files / "main.yaml").read_text(encoding="utf-8"))
    assert main["capabilities"]["video_lesson"]["learners_allowed"] is False
    # A partial update keeps the other knob.
    capabilities_settings.save_capabilities_settings({"video_lesson": {"max_attempts": 0}})
    params = capabilities_settings.get_video_lesson_params()
    assert params["max_attempts"] == 1 and params["learners_allowed"] is False
    assert params["layout_check"] is False
    capabilities_settings.save_capabilities_settings({"video_lesson": {"layout_check": True}})
    params = capabilities_settings.get_video_lesson_params()
    assert params["layout_check"] is True and params["max_attempts"] == 1


def test_agent_params_read_the_video_lesson_section(settings_files: Path, monkeypatch) -> None:
    from deeptutor.services.config import loader

    monkeypatch.setattr(loader, "PROJECT_ROOT", settings_files.parent.parent.parent)
    (settings_files / "agents.yaml").write_text(
        yaml.safe_dump({"plugins": {"video_lesson": {"max_tokens": 12345}}}), encoding="utf-8"
    )
    assert loader.get_agent_params("video_lesson")["max_tokens"] == 12345


# ── npm mirror (batch 4) ─────────────────────────────────────────────


def test_npm_registry_is_an_admin_setting_with_env_fallback(settings_files, monkeypatch) -> None:
    monkeypatch.setenv("DEEPTUTOR_NPM_REGISTRY", "https://env.example/npm/")
    assert capabilities_settings.get_video_lesson_admin_settings()["npm_registry"] == ""
    assert capabilities_settings.video_lesson_npm_registry() == "https://env.example/npm/"

    saved = capabilities_settings.save_video_lesson_admin_settings(
        {
            "npm_registry": "  https://registry.npmmirror.com  ",
            "learners_allowed": False,
            "layout_check": True,
            "max_tokens": 9000,
            "unknown": "dropped",
        }
    )
    assert saved == {
        "temperature": 0.5,
        "max_tokens": 9000,
        "learners_allowed": False,
        "max_attempts": 3,
        "layout_check": True,
        "npm_registry": "https://registry.npmmirror.com",
    }
    assert capabilities_settings.video_lesson_npm_registry() == "https://registry.npmmirror.com"
    # Learners can read /api/capabilities/settings; the mirror is not in it.
    shared = capabilities_settings.capabilities_settings_dict()["video_lesson"]
    assert "npm_registry" not in shared
    # A partial save keeps the mirror; clearing it falls back to the env var.
    capabilities_settings.save_video_lesson_admin_settings({"max_attempts": 2})
    assert capabilities_settings.video_lesson_npm_registry() == "https://registry.npmmirror.com"
    capabilities_settings.save_video_lesson_admin_settings({"npm_registry": ""})
    assert capabilities_settings.video_lesson_npm_registry() == "https://env.example/npm/"


@pytest.mark.parametrize(
    "value",
    [
        "registry.npmmirror.com",
        "file:///etc/passwd",
        "https://user:secret@registry.example",
        "https://reg.example/ --foo",
        "https://",
        "https://x.example/" + "a" * 600,
    ],
)
def test_invalid_npm_registry_is_refused(settings_files, value) -> None:
    with pytest.raises(capabilities_settings.InvalidNpmRegistry):
        capabilities_settings.save_video_lesson_admin_settings(
            {"npm_registry": value, "learners_allowed": False}
        )
    # Nothing was written when the payload was refused.
    assert capabilities_settings.get_video_lesson_params()["learners_allowed"] is True
