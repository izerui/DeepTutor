"""Capabilities settings endpoint.

Surfaces the per-capability tunables (temperature, max_tokens, stage
budgets, iteration limits) currently scattered across
``data/user/settings/agents.yaml`` and ``data/user/settings/main.yaml``.

Mirrors the pattern used by ``/api/memory/settings``:

* ``GET  /settings``  → returns the full schema with defaults merged in.
* ``PUT  /settings``  → merges payload back into both YAML files and
                        returns the new state. Admin only: the files are
                        shared by the whole deployment.

Validation lives in
:mod:`deeptutor.services.config.capabilities_settings` so the API stays
a thin transport layer.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from deeptutor.api.routers.auth import require_admin

router = APIRouter()


@router.get("/settings")
async def get_capabilities_settings_endpoint() -> dict[str, Any]:
    from deeptutor.services.config.capabilities_settings import capabilities_settings_dict

    return capabilities_settings_dict()


@router.put("/settings", dependencies=[Depends(require_admin)])
async def put_capabilities_settings(payload: dict[str, Any]) -> dict[str, Any]:
    from deeptutor.services.config.capabilities_settings import save_capabilities_settings

    return save_capabilities_settings(payload)


# ── Video Lesson administration (spec 2026-11-20 §2–§4) ──────────────────
# Admin-only: the runtime, the npm mirror and the browser check are
# deployment settings learners never see.


@router.get("/video-lesson/settings", dependencies=[Depends(require_admin)])
async def get_video_lesson_settings() -> dict[str, Any]:
    from deeptutor.services.config.capabilities_settings import (
        get_video_lesson_admin_settings,
    )

    return get_video_lesson_admin_settings()


@router.put("/video-lesson/settings", dependencies=[Depends(require_admin)])
async def put_video_lesson_settings(payload: dict[str, Any]) -> dict[str, Any]:
    from deeptutor.services.config.capabilities_settings import (
        InvalidNpmRegistry,
        save_video_lesson_admin_settings,
    )

    try:
        return save_video_lesson_admin_settings(payload)
    except InvalidNpmRegistry as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _video_lesson_status() -> dict[str, Any]:
    from deeptutor.agents.video_lesson.layout_check import chrome_path
    from deeptutor.agents.video_lesson.runtime import MIN_NODE_MAJOR, runtime_status
    from deeptutor.services.config.capabilities_settings import (
        VIDEO_LESSON_MAX_ATTEMPTS_LIMIT,
        video_lesson_npm_registry,
    )

    browser = chrome_path()
    return {
        "runtime": runtime_status().to_dict(),
        "min_node_major": MIN_NODE_MAJOR,
        "browser": {"available": browser is not None, "path": browser},
        "npm_registry": video_lesson_npm_registry(),
        "max_attempts_limit": VIDEO_LESSON_MAX_ATTEMPTS_LIMIT,
    }


@router.get("/video-lesson/status", dependencies=[Depends(require_admin)])
async def get_video_lesson_status() -> dict[str, Any]:
    return await asyncio.to_thread(_video_lesson_status)


@router.post("/video-lesson/install", dependencies=[Depends(require_admin)])
async def install_video_lesson_runtime() -> dict[str, Any]:
    """Install the shared renderer packages now instead of on the first lesson."""
    from deeptutor.agents.video_lesson.runtime import (
        VideoLessonRuntimeError,
        ensure_runtime,
    )
    from deeptutor.services.config.capabilities_settings import video_lesson_npm_registry

    try:
        await ensure_runtime(registry=video_lesson_npm_registry())
    except VideoLessonRuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return await asyncio.to_thread(_video_lesson_status)
