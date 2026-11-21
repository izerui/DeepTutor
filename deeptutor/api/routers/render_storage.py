"""Authenticated render-file cleanup for published Math Animator artifacts.

Ported from upstream 38cf3979d without the desktop parts (server file path,
"show in file manager"): develop manages every storage location itself.
Workspace ids are derived from the caller's account, so another account's
item resolves to nothing here (404/409) exactly as it does for reading it.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from deeptutor.api.routers.auth import require_auth
from deeptutor.services.auth import TokenPayload
from deeptutor.services.workspace import WorkspaceError, get_content_workspace_service
from deeptutor.services.workspace.render_storage import (
    clean_render_storage,
    inspect_render_storage,
)

router = APIRouter()


class RenderCleanupPayload(BaseModel):
    """Bind cleanup to the storage inventory reviewed by the caller."""

    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.get("/{workspace_id}/{workspace_item_id}/render-storage")
async def render_storage(
    workspace_id: str,
    workspace_item_id: str,
    _auth: TokenPayload | None = Depends(require_auth),
) -> dict:
    """Return the size of the final output and of its rebuildable render files."""
    try:
        return await asyncio.to_thread(
            inspect_render_storage, get_content_workspace_service(), workspace_id, workspace_item_id
        )
    except (WorkspaceError, OSError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{workspace_id}/{workspace_item_id}/clean-render-cache")
async def clean_render_cache(
    workspace_id: str,
    workspace_item_id: str,
    payload: RenderCleanupPayload,
    request: Request,
    _auth: TokenPayload | None = Depends(require_auth),
) -> dict:
    """Clean a reviewed render directory while excluding active writers."""
    from deeptutor.services.session import get_session_store
    from deeptutor.services.workspace.activity import data_activity
    from deeptutor.services.workspace.data_migration import assert_no_pending_recovery

    # The request's own shared lease has to go before the exclusive one that
    # keeps renders and migrations out can be taken.
    lease = getattr(request.state, "workspace_activity", None)
    if lease is not None:
        lease.close()
        del request.state.workspace_activity

    async def perform() -> dict:
        with data_activity(exclusive=True):
            assert_no_pending_recovery(reject_unreadable=True)
            if await get_session_store().list_nonterminal_turns():
                raise WorkspaceError("Wait for running conversations to finish before cleanup.")
            return await asyncio.to_thread(
                clean_render_storage,
                get_content_workspace_service(),
                workspace_id,
                workspace_item_id,
                payload.fingerprint,
            )

    task = asyncio.create_task(perform())
    task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
    try:
        return await asyncio.shield(task)
    except (WorkspaceError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
