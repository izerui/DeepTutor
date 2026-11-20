"""Video lesson capability: a Remotion explainer the learner plays in the browser.

Ported from upstream (``6b7897a32`` .. ``c35eb7266``) with the develop rules:

* administrators decide whether learners may use it, and cap the writer's
  ``max_tokens`` and the number of write/fix attempts (enforced here, on the
  server, not only in the UI);
* every LLM call goes through ``BaseAgent.stream_llm`` and is therefore
  counted in the learner's AI daily usage like any other capability;
* a server without Node.js fails fast with a clear "runtime unavailable"
  error before a single token is spent;
* the browser layout check (upstream ``d6fd29428``) runs only when an
  administrator turned it on; it then loads the page isolated (see
  ``layout_check.py``), is skipped when no Chrome is installed, and its repair
  rounds share the same ``max_attempts`` budget.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import time
from typing import Any

from deeptutor.agents._shared.capability_result import emit_capability_result
from deeptutor.core.capability_protocol import CapabilityManifest, TurnCapability
from deeptutor.core.context import UnifiedContext
from deeptutor.core.trace import merge_trace_metadata
from deeptutor.i18n import StatusI18n
from deeptutor.runtime.agentic.usage import UsageTracker
from deeptutor.runtime.request_contracts import get_capability_request_schema
from deeptutor.runtime.stream_bus import StreamBus

# Rounds of "your layout collides here" feedback after a lesson already builds.
MAX_LAYOUT_ROUNDS = 2

DISABLED_FOR_LEARNERS_MESSAGE = (
    "Video Lesson has been turned off for learners by an administrator."
)


class VideoLessonDisabled(RuntimeError):
    """An administrator turned the capability off for learners."""

    code = "video_lesson_disabled"

    def __init__(self) -> None:
        super().__init__(DISABLED_FOR_LEARNERS_MESSAGE)


def acting_learner() -> bool:
    """True when the caller is a learner (not an administrator).

    No request scope (CLI, background jobs) acts as the deployment, i.e. admin.
    The stored role is consulted, not only the token's, like the AI limit does.
    """
    from deeptutor.multi_user.context import get_current_user_or_none

    user = get_current_user_or_none()
    if user is None or user.is_admin:
        return False
    try:
        from deeptutor.multi_user.roles import current_role

        return current_role() != "admin"
    except Exception:
        return True


def ensure_allowed(params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Raise :class:`VideoLessonDisabled` for learners when the admin switch is off."""
    from deeptutor.services.config.capabilities_settings import get_video_lesson_params

    params = params if params is not None else get_video_lesson_params()
    if not params.get("learners_allowed", True) and acting_learner():
        raise VideoLessonDisabled()
    return params


class VideoLessonCapability(TurnCapability):
    manifest = CapabilityManifest(
        name="video_lesson",
        description=(
            "Generate a narrated, captioned 1-2 minute explainer animation (Remotion) "
            "that plays in the browser."
        ),
        stages=["code_generation", "render_check", "render_output"],
        tools_used=[],
        cli_aliases=["lesson"],
        request_schema=get_capability_request_schema("video_lesson"),
    )

    async def run(self, context: UnifiedContext, stream: StreamBus) -> None:
        from deeptutor.agents.video_lesson.lesson_writer import LessonWriterAgent
        from deeptutor.agents.video_lesson.narration import narrate, voice_module
        from deeptutor.agents.video_lesson.runtime import (
            MIN_NODE_MAJOR,
            UNAVAILABLE_MESSAGE,
            VideoLessonRuntimeUnavailable,
            build_lesson,
            ensure_runtime,
            prepare_workdir,
            tools_present,
            write_page,
        )
        from deeptutor.services.config.capabilities_settings import video_lesson_npm_registry
        from deeptutor.services.llm.config import get_llm_config
        from deeptutor.services.model_selection.task_agent import task_model_agent
        from deeptutor.services.path_service import get_path_service

        # Admin policy and the toolchain are checked before any token is spent.
        params = ensure_allowed()
        if not tools_present():
            raise VideoLessonRuntimeUnavailable(UNAVAILABLE_MESSAGE.format(major=MIN_NODE_MAJOR))
        max_attempts = int(params["max_attempts"])
        layout_check = bool(params.get("layout_check", False))
        max_tokens = int(params["max_tokens"])
        temperature = float(params["temperature"])

        llm_config = get_llm_config()
        usage = UsageTracker(model=getattr(llm_config, "model", None))
        i18n = StatusI18n(self.name, context.language, module="video_lesson")
        paths = get_path_service()
        turn_id = str(context.metadata.get("turn_id", "") or context.session_id or "video-lesson")
        base_dir = paths.get_agent_dir("video_lesson") / turn_id
        history = str(context.metadata.get("conversation_context_text", "") or "")

        writer = LessonWriterAgent(
            api_key=llm_config.api_key,
            base_url=llm_config.base_url,
            api_version=llm_config.api_version,
            language=context.language,
        )
        writer.set_trace_callback(self._build_trace_bridge(stream, i18n))
        writer.start(user_input=context.user_message, history=history)
        # Fixes edit a lesson that already exists; the fast task model does them in a
        # fraction of the main model's time. Without one, the main writer fixes too.
        fixer = (
            task_model_agent(lambda **kw: LessonWriterAgent(language=context.language, **kw))
            or writer
        )
        if fixer is not writer:
            fixer.set_trace_callback(self._build_trace_bridge(stream, i18n))
            fixer.share_conversation(writer)

        # npm install (first use only) overlaps the first, much longer, LLM call.
        runtime_ready = asyncio.create_task(
            ensure_runtime(registry=video_lesson_npm_registry())
        )
        timings: dict[str, float] = {"code_generation": 0.0, "render_check": 0.0}
        voice_cache: dict = {}
        narrated = False
        attempts: list[dict[str, Any]] = []
        draft = None
        build = None
        good = None
        good_bundle = base_dir / "source" / "player.good.js"
        layout_rounds = 0
        try:
            for attempt in range(1, max_attempts + 1):
                started = time.perf_counter()
                async with stream.stage("code_generation", source=self.name):
                    await stream.progress(
                        message=i18n.t("writing", "Writing the explainer animation."),
                        source=self.name,
                        stage="code_generation",
                    )
                    draft = await (writer if attempt == 1 else fixer).write(
                        attempt=attempt, max_tokens=max_tokens, temperature=temperature
                    )
                timings["code_generation"] += round(time.perf_counter() - started, 3)
                if not draft.code:
                    attempts.append(
                        {"attempt": attempt, "stage": "parse", "errors": ["no ```tsx block"]}
                    )
                    writer.add_feedback(
                        previous=draft.raw,
                        stage="parse",
                        errors=["The reply had no ```tsx code block."],
                    )
                    continue

                started = time.perf_counter()
                async with stream.stage("render_check", source=self.name):
                    if not runtime_ready.done():
                        await stream.progress(
                            message=i18n.t("preparing_runtime", "Preparing the Remotion runtime."),
                            source=self.name,
                            stage="render_check",
                        )
                    if draft.narration:
                        await stream.progress(
                            message=i18n.t("narrating", "Recording the narration."),
                            source=self.name,
                            stage="render_check",
                        )
                    clips, _ = await asyncio.gather(
                        narrate(draft.narration, context.language, voice_cache), runtime_ready
                    )
                    narrated = any(clip.audio for clip in clips.values())
                    await stream.progress(
                        message=i18n.t(
                            "checking", "Compiling the animation and checking its frames."
                        ),
                        source=self.name,
                        stage="render_check",
                    )
                    lesson = prepare_workdir(
                        base_dir / "source", draft.code, voice=voice_module(clips)
                    )
                    build = await build_lesson(lesson, base_dir / "source" / "player.js")
                timings["render_check"] += round(time.perf_counter() - started, 3)
                attempts.append({"attempt": attempt, "stage": build.stage, "errors": build.errors})
                if build.ok:
                    if not layout_check:
                        break
                    # Keep the working bundle: a layout fix that then fails to compile
                    # must not cost the learner a lesson that already played.
                    shutil.copyfile(base_dir / "source" / "player.js", good_bundle)
                    good = (draft, build)
                    if attempt >= max_attempts or layout_rounds >= MAX_LAYOUT_ROUNDS:
                        break
                    layout_rounds += 1
                    await stream.progress(
                        message=i18n.t("layout", "Checking each scene's layout for overlaps."),
                        source=self.name,
                        stage="render_check",
                    )
                    started = time.perf_counter()
                    issues = await asyncio.to_thread(
                        self._layout_issues, base_dir, build, context.language
                    )
                    timings["render_check"] += round(time.perf_counter() - started, 3)
                    if issues is None:
                        attempts.append(
                            {"attempt": attempt, "stage": "layout_unavailable", "errors": []}
                        )
                        break
                    if not issues:
                        break
                    attempts.append({"attempt": attempt, "stage": "layout", "errors": issues})
                    await stream.progress(
                        message=i18n.t(
                            "layout_retry",
                            f"Layout check found {len(issues)} overlap(s); repositioning.",
                            count=len(issues),
                        ),
                        source=self.name,
                        stage="render_check",
                        metadata={"trace_layer": "raw"},
                    )
                    writer.add_feedback(previous=draft.raw, stage="layout", errors=issues)
                    continue
                await stream.progress(
                    message=i18n.t(
                        "retry",
                        f"Check {attempt} failed: {build.errors[0] if build.errors else build.stage}",
                        attempt=attempt,
                        error=build.errors[0] if build.errors else build.stage,
                    ),
                    source=self.name,
                    stage="render_check",
                    metadata={"trace_layer": "raw"},
                )
                writer.add_feedback(previous=draft.raw, stage=build.stage, errors=build.errors)
        finally:
            if not runtime_ready.done():
                runtime_ready.cancel()
            await asyncio.gather(runtime_ready, return_exceptions=True)

        if good is not None and (build is None or not build.ok):
            draft, build = good
            shutil.copyfile(good_bundle, base_dir / "source" / "player.js")
        good_bundle.unlink(missing_ok=True)

        if draft is None or build is None or not build.ok:
            last = attempts[-1]["errors"] if attempts else []
            raise RuntimeError(
                f"The video lesson did not pass its checks after {max_attempts} attempts: "
                + "; ".join(last[:3])
            )

        async with stream.stage("render_output", source=self.name):
            title = draft.title or context.user_message.strip()[:60]
            page = write_page(
                base_dir / "source" / "player.js",
                base_dir / "artifacts" / "lesson.html",
                title=title,
                language=context.language,
            )
            (base_dir / "source" / "player.js").unlink(missing_ok=True)
            fps, frames = 30, max((f.get("frame", 0) for f in build.frames), default=0) + 1
            seconds = round(frames / fps)
            await stream.progress(
                message=i18n.t("ready", f"Video lesson ready ({seconds} s).", seconds=seconds),
                source=self.name,
                stage="render_output",
            )
            if draft.summary:
                await stream.content(draft.summary, source=self.name, stage="render_output")

        rel = page.resolve().relative_to(paths.user_data_dir.resolve()).as_posix()
        await emit_capability_result(
            stream,
            {
                "response": draft.summary,
                "title": title,
                "artifacts": [
                    {
                        "type": "html",
                        "url": f"/files/outputs/{rel}",
                        "filename": page.name,
                        "content_type": "text/html",
                        "label": title,
                    }
                ],
                "code": {"language": "tsx", "content": draft.code},
                "render": {
                    "attempts": attempts,
                    "duration_seconds": seconds,
                    "narrated": narrated,
                },
                "timings": timings,
            },
            source=self.name,
            usage=usage,
        )

    @staticmethod
    def _layout_issues(base_dir: Path, build: Any, language: str) -> list[str] | None:
        """Browser layout check of the fresh bundle; empty when clean, None when unavailable."""
        from deeptutor.agents.video_lesson.layout_check import check_layout, sample_frames
        from deeptutor.agents.video_lesson.runtime import write_page

        total = max((f.get("frame", 0) for f in build.frames), default=0) + 1
        page = base_dir / "source" / "layout-check.html"
        try:
            write_page(
                base_dir / "source" / "player.js", page, title="layout check", language=language
            )
            return check_layout(page, sample_frames(total))
        except Exception:  # the check is advisory; a broken probe must not fail the lesson
            return None
        finally:
            page.unlink(missing_ok=True)

    def _build_trace_bridge(self, stream: StreamBus, i18n: StatusI18n):
        async def _trace_bridge(update: dict[str, Any]) -> None:
            if str(update.get("event", "") or "") != "llm_call":
                return
            stage = str(update.get("phase") or update.get("stage") or "code_generation")
            base_metadata = {
                key: value
                for key, value in update.items()
                if key
                not in {"event", "state", "response", "chunk", "result", "tool_name", "tool_args"}
            }
            state = str(update.get("state", "running"))
            label = str(base_metadata.get("label", "") or stage.replace("_", " ").title())
            if state in {"running", "complete"}:
                await stream.progress(
                    message=label,
                    source=self.name,
                    stage=stage,
                    metadata=merge_trace_metadata(
                        base_metadata, {"trace_kind": "call_status", "call_state": state}
                    ),
                )
            elif state == "streaming":
                chunk = str(update.get("chunk", "") or "")
                if chunk:
                    await stream.thinking(
                        chunk,
                        source=self.name,
                        stage=stage,
                        metadata=merge_trace_metadata(base_metadata, {"trace_kind": "llm_chunk"}),
                    )
            elif state == "error":
                await stream.error(
                    str(
                        update.get("response", "") or i18n.t("llm_call_failed", "LLM call failed.")
                    ),
                    source=self.name,
                    stage=stage,
                    metadata=merge_trace_metadata(
                        base_metadata, {"trace_kind": "call_status", "call_state": "error"}
                    ),
                )

        return _trace_bridge


__all__ = [
    "DISABLED_FOR_LEARNERS_MESSAGE",
    "MAX_LAYOUT_ROUNDS",
    "VideoLessonCapability",
    "VideoLessonDisabled",
    "acting_learner",
    "ensure_allowed",
]
