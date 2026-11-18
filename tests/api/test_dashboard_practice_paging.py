import pytest

from deeptutor.api.routers import dashboard


@pytest.mark.asyncio
async def test_practice_listing_does_one_bounded_search_and_preserves_page_metadata(monkeypatch):
    calls = []

    class Store:
        async def list_notebook_entries(self, **kwargs):
            calls.append(kwargs)
            return {"items": [{"id": 251, "question": "needle", "created_at": 5.0}], "total": 12000}

    monkeypatch.setattr("deeptutor.services.session.get_sqlite_session_store", lambda: Store())

    async def scoped(reader):
        return [{**row, "content_workspace_id": "school"} for row in await reader()], []

    monkeypatch.setattr("deeptutor.services.workspace.navigation.read_workspace_indexes", scoped)
    result = await dashboard.get_source_library("practice", limit=50, offset=0, search="needle")
    assert calls == [{"limit": 50, "offset": 0, "search": "needle"}]
    assert result["items"][0]["content_workspace_id"] == "school"
    assert result["total"] == 12000 and result["has_more"] is True
    assert result["offset"] == 0


@pytest.mark.asyncio
async def test_practice_pages_span_every_workspace_in_workspace_order(monkeypatch):
    """Develop lists the question bank account-wide, workspace by workspace:
    each workspace supplies its first ``offset + limit`` rows, totals add up,
    and the page is cut from the concatenation."""
    pages = {
        "": [{"id": 4, "created_at": 40.0}, {"id": 2, "created_at": 20.0}],
        "school": [{"id": 3, "created_at": 30.0}, {"id": 1, "created_at": 10.0}],
    }
    calls = []
    current = {"workspace": ""}

    class Store:
        async def list_notebook_entries(self, **kwargs):
            calls.append(kwargs)
            rows = pages[current["workspace"]]
            return {"items": rows[: kwargs["limit"]], "total": len(rows)}

    monkeypatch.setattr("deeptutor.services.session.get_sqlite_session_store", lambda: Store())

    async def scoped(reader):
        rows = []
        for workspace in pages:
            current["workspace"] = workspace
            rows.extend({**row, "content_workspace_id": workspace} for row in await reader())
        return rows, []

    monkeypatch.setattr("deeptutor.services.workspace.navigation.read_workspace_indexes", scoped)
    result = await dashboard.get_learning_library("practice", limit=2, offset=1, search="")
    assert calls == [{"limit": 3, "offset": 0, "search": ""}] * 2
    assert [row["id"] for row in result["items"]] == [2, 3]
    assert result["total"] == 4 and result["has_more"] is True


@pytest.mark.asyncio
async def test_practice_paging_rejects_unbounded_requests():
    with pytest.raises(dashboard.HTTPException):
        await dashboard.get_learning_library("practice", limit=500)
