"""B2-13: cross-check kb_id between dependency and endpoint resolvers.

When ``enter_mastery_template_scope`` entitles a specific kb_id via
``registry.resolve_ref``, the endpoint's ``resolve_kb`` must serve from the
same physical KB.  A mismatch is refused with 404.
"""

from __future__ import annotations

from urllib.parse import quote

import pytest

from deeptutor.knowledge import registry as kb_registry


def _auth(name: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {name}"}


def _files_url(ref: str, rel_path: str) -> str:
    return f"/api/knowledge-bases/{quote(ref, safe='')}/files/{rel_path}"


@pytest.fixture
def open_path_with_kb(
    teaching_users, as_account, make_kb, teacher_draft, publish_template,
    quiet_change_model,
):
    """Tina publishes a path with one KB, assigned to Sara — path stays open."""
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    from deeptutor.learning.models import TopicSource, TopicSourceKind
    from deeptutor.multi_user.paths import get_account_path_service

    with as_account("tina"):
        root = get_account_path_service().get_knowledge_bases_root()
        root.mkdir(parents=True, exist_ok=True)
        mgr = KnowledgeBaseManager(base_dir=str(root))
        make_kb(mgr, "cross-kb", text="cross-check content")
        rec = kb_registry.ensure_registered(
            root, "cross-kb", created_by=teaching_users["tina"]["id"]
        )

    teacher_draft(
        path_id="topic_cross",
        sources=(
            TopicSource(
                id="s0",
                kind=TopicSourceKind.KNOWLEDGE_BASE,
                source_id=rec.ref,
                label="Cross KB",
                position=0,
            ),
        ),
    )
    result = publish_template(
        path_id="topic_cross",
        students=("sara",),
        kb_choices={rec.kb_id: "reference_only"},
    )
    return {
        "template_id": result["template"]["id"],
        "rec": rec,
    }


def test_matching_kb_id_serves_normally(open_path_with_kb, teaching_http):
    """When the two resolvers agree the document is served (baseline)."""
    p = open_path_with_kb
    url = _files_url(p["rec"].ref, "doc.md")
    scoped_url = f"{url}?mastery_template={p['template_id']}"
    resp = teaching_http.client.get(scoped_url, headers=_auth("sara"))
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}"


def test_mismatched_kb_id_returns_404(open_path_with_kb, teaching_http, monkeypatch):
    """When resolve_kb resolves to a KB with a different registry identity, refuse."""
    from deeptutor.knowledge import registry

    p = open_path_with_kb
    real_find = registry.find_by_location

    def patched_find_by_location(base_dir, name):
        """Return a record with a tampered kb_id to simulate mismatch."""
        from dataclasses import replace
        record = real_find(base_dir, name)
        if record is not None:
            record = replace(record, kb_id="tampered-kb-id-that-does-not-match")
        return record

    monkeypatch.setattr(
        "deeptutor.knowledge.registry.find_by_location",
        patched_find_by_location,
    )

    url = _files_url(p["rec"].ref, "doc.md")
    scoped_url = f"{url}?mastery_template={p['template_id']}"
    resp = teaching_http.client.get(scoped_url, headers=_auth("sara"))
    assert resp.status_code == 404, (
        f"mismatched kb_id should refuse with 404, got {resp.status_code}"
    )


def test_no_template_param_unaffected_by_cross_check(open_path_with_kb, teaching_http, monkeypatch):
    """Without mastery_template param the cross-check is not active."""
    from deeptutor.knowledge import registry

    p = open_path_with_kb
    real_find = registry.find_by_location

    def patched_find_by_location(base_dir, name):
        from dataclasses import replace
        record = real_find(base_dir, name)
        if record is not None:
            record = replace(record, kb_id="tampered-kb-id-that-does-not-match")
        return record

    monkeypatch.setattr(
        "deeptutor.knowledge.registry.find_by_location",
        patched_find_by_location,
    )

    # Request without mastery_template — cross-check should not trigger.
    # Use a public KB so Sara can access without the template.
    # Make the KB public so Sara can read it without the template.
    kb_registry.set_visibility(p["rec"].kb_id, "public")
    url = _files_url(p["rec"].ref, "doc.md")
    resp = teaching_http.client.get(url, headers=_auth("sara"))
    # The request should succeed (200) or fail for other reasons, but NOT
    # because of the cross-check.  The patched find_by_location would cause
    # a 404 only if the cross-check fires.
    assert resp.status_code == 200, (
        f"without template param, cross-check should not fire; got {resp.status_code}"
    )
