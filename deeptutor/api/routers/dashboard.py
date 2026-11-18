"""Dashboard API — what the home screen shows before a conversation starts.

Recent activity comes from the unified SQLite session store; the starter lines
come from :mod:`deeptutor.services.suggestions`, which reads memory.

Route order matters here: ``/{entry_id}`` at the bottom of this module matches
any single segment, so every literal path must be declared above it or it will
never be reached.
"""

from typing import Any

from fastapi import APIRouter, HTTPException

from deeptutor.services.session import get_session_store

router = APIRouter()


@router.get("/recent")
async def get_recent_activities(limit: int = 50, type: str | None = None):
    store = get_session_store()
    sessions = await store.list_sessions(limit=limit, offset=0)
    activities: list[dict[str, Any]] = []

    for session in sessions:
        capability = str(session.get("capability") or "chat")
        activity_type = capability.replace("deep_", "")
        if type is not None and activity_type != type:
            continue
        activities.append(
            {
                "id": session.get("session_id"),
                "type": activity_type,
                "capability": capability,
                "title": session.get("title", "Untitled"),
                "timestamp": session.get("updated_at", session.get("created_at", 0)),
                "summary": (session.get("last_message") or "")[:160],
                "session_ref": f"sessions/{session.get('session_id')}",
                "message_count": session.get("message_count", 0),
                "status": session.get("status", "idle"),
                "active_turn_id": session.get("active_turn_id"),
            }
        )

    return activities[:limit]


@router.get("/suggestions")
async def get_starter_suggestions():
    """The three starting points for the home composer.

    Returns immediately, even when the set is stale — regeneration happens
    behind the response. An empty ``suggestions`` list means there is nothing
    in memory to ground a suggestion in, and the client renders nothing.

    No language parameter: the output language is the learner's own
    model-output setting, resolved server-side. See
    :mod:`deeptutor.services.suggestions`.
    """
    from deeptutor.services.suggestions import get_suggestions

    return await get_suggestions()


@router.post("/suggestions/refresh")
async def refresh_starter_suggestions():
    """Generate a new set now. Backs the reroll control.

    Synchronous, unlike the read: a human clicked and is waiting for a
    different set.
    """
    from deeptutor.services.suggestions import refresh_suggestions

    result = await refresh_suggestions()
    return {**result.to_dict(), "stale": False}


@router.get("/learning-index")
async def get_learning_index():
    """Read-only, account-wide entry points. Feature libraries remain scoped."""
    from deeptutor.api.routers.book import list_books
    from deeptutor.api.routers.mastery_path import list_topics
    from deeptutor.api.routers.reading import list_workspaces
    from deeptutor.services.workspace.navigation import read_workspace_indexes

    async def books():
        return (await list_books())["books"]

    async def mastery():
        return (await list_topics())["topics"]

    async def reading():
        return (await list_workspaces(search=""))["workspaces"]

    async def watching():
        from deeptutor.services.session.organization import list_all_sessions_snapshot

        rows = await list_all_sessions_snapshot(get_session_store())
        return [
            row
            for row in rows
            if (row.get("preferences") or {}).get("workspace_mode") == "immersive_watching"
            or (row.get("preferences") or {}).get("capability") == "immersive_watching"
        ]

    result = {}
    failed = []
    for kind, reader in (
        ("books", books),
        ("mastery", mastery),
        ("reading", reading),
        ("watching", watching),
    ):
        result[kind], unavailable = await read_workspace_indexes(reader)
        if kind == "books":
            from deeptutor.multi_user.book_access import dedupe_account_book_rows

            # B3-P16: an own public book in another workspace is listed once.
            result[kind] = dedupe_account_book_rows(result[kind])
        if unavailable:
            failed.append(kind)
    return {"sources": result, "failed": failed}


@router.get("/source-library/{kind}")
async def get_source_library(kind: str, limit: int = 50, offset: int = 0, search: str = ""):
    """Read account-owned source indexes; preserve the workspace on every row."""
    from deeptutor.services.workspace.navigation import read_workspace_indexes

    async def read():
        if kind == "notebooks":
            from deeptutor.services.notebook import notebook_manager

            return notebook_manager.list_notebooks()
        if kind == "chats":
            from deeptutor.services.session.organization import list_all_sessions_snapshot

            return await list_all_sessions_snapshot(get_session_store())
        if kind == "drafts":
            from deeptutor.api.routers.co_writer import list_documents

            return [row.model_dump() for row in (await list_documents())["documents"]]
        raise HTTPException(404, "Unknown source library")

    if kind == "knowledge":
        from deeptutor.services.workspace.knowledge import knowledge_catalog

        items = [
            {
                **row,
                "content_workspace_id": row.get("workspace_id", ""),
                "content_workspace_name": row.get("provenance_label", ""),
            }
            for row in knowledge_catalog()
        ]
        return {"items": items, "unavailable_workspaces": []}
    if kind in {"books", "practice"}:
        return await get_learning_library(kind, limit=limit, offset=offset, search=search)
    if kind not in {"notebooks", "chats", "drafts"}:
        raise HTTPException(404, "Unknown source library")
    items, unavailable = await read_workspace_indexes(read)
    return {"items": items, "unavailable_workspaces": unavailable}


@router.get("/learning-library/{kind}")
async def get_learning_library(kind: str, limit: int = 50, offset: int = 0, search: str = ""):
    """Account-wide discovery. Items retain their source for scoped follow-up requests."""
    from deeptutor.services.workspace.navigation import read_workspace_indexes

    can_create = True
    total = 0
    if not 1 <= limit <= 200 or offset < 0 or len(search) > 500:
        raise HTTPException(
            422, "Use limit 1–200, a nonnegative offset, and search up to 500 characters."
        )

    async def read():
        nonlocal can_create, total
        if kind == "books":
            from deeptutor.api.routers.book import list_books

            result = await list_books()
            can_create = result["can_create"]
            return result["books"]
        if kind == "mastery":
            from deeptutor.api.routers.mastery_path import list_topics

            return (await list_topics())["topics"]
        if kind == "reading":
            from deeptutor.api.routers.reading import list_workspaces

            return (await list_workspaces(search=""))["workspaces"]
        if kind == "materials":
            from deeptutor.api.routers.reading import list_library_materials

            return (await list_library_materials(search="", status=None, library_filter="all"))[
                "materials"
            ]
        if kind == "practice":
            from deeptutor.services.session import get_sqlite_session_store

            store = get_sqlite_session_store()
            # Pages span every workspace of the account, so each one supplies
            # its newest ``offset + limit`` rows and the merge below cuts the page.
            result = await store.list_notebook_entries(
                limit=offset + limit, offset=0, search=search
            )
            total += int(result["total"])
            return result["items"]
        from deeptutor.services.session.organization import list_all_sessions_snapshot

        rows = await list_all_sessions_snapshot(get_session_store())
        return [
            row
            for row in rows
            if not (row.get("preferences") or {}).get("archived")
            and (
                (row.get("preferences") or {}).get("workspace_mode") == "immersive_watching"
                or (row.get("preferences") or {}).get("capability") == "immersive_watching"
            )
        ]

    if kind not in {"books", "mastery", "reading", "materials", "practice", "watching"}:
        raise HTTPException(status_code=404, detail="Unknown learning library")
    rows, unavailable = await read_workspace_indexes(read)
    if kind == "books":
        from deeptutor.multi_user.book_access import dedupe_account_book_rows

        # B3-P16: an own public book in another workspace is listed once.
        rows = dedupe_account_book_rows(rows)
    if kind == "practice":
        # The account-wide list is every workspace's bank in workspace order.
        # Each workspace returned its first ``offset + limit`` rows, which is
        # all this page can need from it, so the concatenation is cut here.
        rows = rows[offset : offset + limit]
    return {
        "items": rows,
        "unavailable_workspaces": unavailable,
        "can_create": can_create,
        **(
            {
                "total": total,
                "offset": offset,
                "limit": limit,
                "has_more": offset + len(rows) < total,
            }
            if kind == "practice"
            else {}
        ),
    }


@router.get("/{entry_id}")
async def get_activity_entry(entry_id: str):
    store = get_session_store()
    session = await store.get_session_with_messages(entry_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Entry not found")

    capability = str(session.get("capability") or "chat")
    return {
        "id": session.get("session_id"),
        "type": capability.replace("deep_", ""),
        "capability": capability,
        "title": session.get("title"),
        "timestamp": session.get("updated_at", session.get("created_at")),
        "content": {
            "messages": session.get("messages", []),
            "active_turns": session.get("active_turns", []),
            "status": session.get("status", "idle"),
            "summary": session.get("compressed_summary", ""),
        },
    }
