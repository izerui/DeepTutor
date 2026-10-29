"""Connected local agents are closed (spec 2026-10-29, P1c).

Claude Code, Codex and the other agent harnesses run as programs on the server
with access to its files, so no account may connect, detect, sync or consult
them. Partners and partner discussion groups are DeepTutor's own delegates and
stay available.
"""

from __future__ import annotations

from typing import NoReturn

from fastapi import HTTPException

CONNECTED_AGENTS_DISABLED = "Connected local agents are not available."


def refuse_connected_agents() -> NoReturn:
    raise HTTPException(status_code=403, detail=CONNECTED_AGENTS_DISABLED)
