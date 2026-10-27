"""B2-12: closed path + ?mastery_template falls back to normal auth, not 403.

Red-first: ``enter_mastery_template_scope`` must silently return (ignore the
template parameter) when ``entitled_materials`` returns None (path closed,
not assigned, archived) so independent readers are not blocked.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from deeptutor.api.routers.mastery_teaching import enter_mastery_template_scope


def _make_request(
    *,
    template_id: str = "tmpl-1",
    method: str = "GET",
    path_params: "dict[str, str] | None" = None,
    query_params: "dict[str, str] | None" = None,
):
    """Build a minimal fake Request with the fields the function reads."""
    qp = {"mastery_template": template_id}
    if query_params:
        qp.update(query_params)

    class FakeQueryParams(dict):
        def get(self, key, default=None):
            return super().get(key, default)

    class FakeHeaders(dict):
        def get(self, key, default=None):
            return super().get(key, default)

    return SimpleNamespace(
        query_params=FakeQueryParams(qp),
        headers=FakeHeaders({}),
        method=method,
        path_params=path_params or {"kb_name": "my-kb", "filename": "doc.pdf"},
    )


@pytest.mark.asyncio
async def test_closed_path_does_not_raise_403():
    """When the path is closed, the template param is ignored — no 403."""
    request = _make_request()
    with (
        patch(
            "deeptutor.api.routers.auth._resolved_route_path",
            return_value="/api/knowledge-bases/{kb_name}/files/{filename:path}",
        ),
        patch(
            "deeptutor.teaching.access.entitled_materials",
            return_value=None,
        ),
    ):
        # Must NOT raise — should silently return so normal auth takes over.
        await enter_mastery_template_scope(request)


@pytest.mark.asyncio
async def test_open_path_still_works():
    """Sanity: an open path with a valid grant still sets context vars."""
    from deeptutor.teaching.access import OriginalGrant, active_mastery_template

    fake_materials = SimpleNamespace(
        template_id="tmpl-1",
        kbs={"kb1": None},
        book_kbs={},
        cites_kb=lambda kb_id: True,
    )
    grant = OriginalGrant(rel_path="doc.pdf", studying=True)
    request = _make_request()
    with (
        patch(
            "deeptutor.api.routers.auth._resolved_route_path",
            return_value="/api/knowledge-bases/{kb_name}/files/{filename:path}",
        ),
        patch(
            "deeptutor.teaching.access.entitled_materials",
            return_value=fake_materials,
        ),
        patch(
            "deeptutor.teaching.access.kb_original_grant",
            return_value=grant,
        ),
    ):
        await enter_mastery_template_scope(request)
        assert active_mastery_template.get() == "tmpl-1"


@pytest.mark.asyncio
async def test_closed_path_private_kb_no_existence_leak():
    """Closed path + private KB the user cannot read: the template param is
    ignored, so the request falls through to normal auth (which would deny it
    on its own — but the scope function must not inject a *different* error)."""
    request = _make_request()
    with (
        patch(
            "deeptutor.api.routers.auth._resolved_route_path",
            return_value="/api/knowledge-bases/{kb_name}/files/{filename:path}",
        ),
        patch(
            "deeptutor.teaching.access.entitled_materials",
            return_value=None,
        ),
    ):
        # Must NOT raise 403 — normal route auth handles the denial.
        await enter_mastery_template_scope(request)
