"""B2-12: closed path + ?mastery_template falls back to normal auth, not 403.

End-to-end HTTP route tests: when the teacher path is closed (archived,
revoked, not assigned) the ``mastery_template`` query parameter is silently
ignored and the route's own authorization rules decide the response status.
"""

from __future__ import annotations

from urllib.parse import quote

import pytest

from deeptutor.knowledge import registry as kb_registry
from deeptutor.teaching.service import TeachingService


def _auth(name: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {name}"}


def _files_url(ref: str, rel_path: str) -> str:
    return f"/api/knowledge-bases/{quote(ref, safe='')}/files/{rel_path}"


@pytest.fixture
def public_and_private_path(
    teaching_users, as_account, make_kb, teacher_draft, publish_template, quiet_change_model
):
    """Tina's two KBs — one public, one private — published to Sara then archived."""
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    from deeptutor.learning.models import TopicSource, TopicSourceKind
    from deeptutor.multi_user.paths import get_account_path_service

    with as_account("tina"):
        root = get_account_path_service().get_knowledge_bases_root()
        root.mkdir(parents=True, exist_ok=True)
        mgr = KnowledgeBaseManager(base_dir=str(root))
        make_kb(mgr, "pub-docs", text="public content here")
        pub_rec = kb_registry.ensure_registered(
            root, "pub-docs", created_by=teaching_users["tina"]["id"]
        )
        pub_rec = kb_registry.set_visibility(pub_rec.kb_id, "public")
        make_kb(mgr, "priv-docs", text="private content here")
        priv_rec = kb_registry.ensure_registered(
            root, "priv-docs", created_by=teaching_users["tina"]["id"]
        )
    assert pub_rec.visibility == "public"
    assert priv_rec.visibility == "private"

    teacher_draft(
        sources=(
            TopicSource(
                id="s0",
                kind=TopicSourceKind.KNOWLEDGE_BASE,
                source_id=pub_rec.ref,
                label="Public docs",
                position=0,
            ),
            TopicSource(
                id="s1",
                kind=TopicSourceKind.KNOWLEDGE_BASE,
                source_id=priv_rec.ref,
                label="Private docs",
                position=1,
            ),
        )
    )
    result = publish_template(
        students=("sara",),
        kb_choices={pub_rec.kb_id: "reference_only", priv_rec.kb_id: "reference_only"},
    )
    template_id = result["template"]["id"]
    # Archive the path so it is closed.
    with as_account("tina"):
        TeachingService().archive(template_id)

    return {
        "template_id": template_id,
        "pub_rec": pub_rec,
        "priv_rec": priv_rec,
    }


def test_closed_path_public_kb_returns_200(public_and_private_path, teaching_http):
    """A public KB document is independently readable — closed path must not block it."""
    p = public_and_private_path
    url = _files_url(p["pub_rec"].ref, "doc.md")
    scoped_url = f"{url}?mastery_template={p['template_id']}"

    plain = teaching_http.client.get(url, headers=_auth("sara"))
    scoped = teaching_http.client.get(scoped_url, headers=_auth("sara"))
    assert plain.status_code == 200, f"plain route should be 200 for public KB, got {plain.status_code}"
    assert scoped.status_code == 200, f"scoped route should fall back to 200, got {scoped.status_code}"


def test_closed_path_private_kb_matches_plain_route(public_and_private_path, teaching_http):
    """A private KB of someone else: closed path + template param yields the same
    status as the plain route without the parameter (no existence leak)."""
    p = public_and_private_path
    url = _files_url(p["priv_rec"].ref, "doc.md")
    scoped_url = f"{url}?mastery_template={p['template_id']}"

    plain = teaching_http.client.get(url, headers=_auth("sara"))
    scoped = teaching_http.client.get(scoped_url, headers=_auth("sara"))
    assert scoped.status_code == plain.status_code, (
        f"closed path + private KB should match plain route ({plain.status_code}), "
        f"got {scoped.status_code}"
    )


def test_open_path_entitled_doc_still_200(
    teaching_users, as_account, make_kb, teacher_draft, publish_template,
    quiet_change_model, teaching_http,
):
    """While the path is open, an entitled document returns 200 (no regression)."""
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    from deeptutor.learning.models import TopicSource, TopicSourceKind
    from deeptutor.multi_user.paths import get_account_path_service

    with as_account("tina"):
        root = get_account_path_service().get_knowledge_bases_root()
        root.mkdir(parents=True, exist_ok=True)
        mgr = KnowledgeBaseManager(base_dir=str(root))
        make_kb(mgr, "open-kb", text="open path content")
        rec = kb_registry.ensure_registered(
            root, "open-kb", created_by=teaching_users["tina"]["id"]
        )

    teacher_draft(
        path_id="topic_open",
        sources=(
            TopicSource(
                id="s0",
                kind=TopicSourceKind.KNOWLEDGE_BASE,
                source_id=rec.ref,
                label="Open KB",
                position=0,
            ),
        ),
    )
    result = publish_template(
        path_id="topic_open",
        students=("sara",),
        kb_choices={rec.kb_id: "reference_only"},
    )
    template_id = result["template"]["id"]
    url = _files_url(rec.ref, "doc.md")
    scoped_url = f"{url}?mastery_template={template_id}"

    opened = teaching_http.client.get(scoped_url, headers=_auth("sara"))
    assert opened.status_code == 200, f"open path + entitled doc should be 200, got {opened.status_code}"
    assert "open path content" in opened.text


def test_open_path_non_existent_doc_returns_404(
    teaching_users, as_account, make_kb, teacher_draft, publish_template,
    quiet_change_model, teaching_http,
):
    """While the path is open, a non-existent file in an entitled KB returns 404."""
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    from deeptutor.learning.models import TopicSource, TopicSourceKind
    from deeptutor.multi_user.paths import get_account_path_service

    with as_account("tina"):
        root = get_account_path_service().get_knowledge_bases_root()
        root.mkdir(parents=True, exist_ok=True)
        mgr = KnowledgeBaseManager(base_dir=str(root))
        make_kb(mgr, "entitled-kb", text="entitled content")
        rec = kb_registry.ensure_registered(
            root, "entitled-kb", created_by=teaching_users["tina"]["id"]
        )

    teacher_draft(
        path_id="topic_nonexist",
        sources=(
            TopicSource(
                id="s0",
                kind=TopicSourceKind.KNOWLEDGE_BASE,
                source_id=rec.ref,
                label="Entitled KB",
                position=0,
            ),
        ),
    )
    result = publish_template(
        path_id="topic_nonexist",
        students=("sara",),
        kb_choices={rec.kb_id: "reference_only"},
    )
    template_id = result["template"]["id"]
    url = _files_url(rec.ref, "no-such-file.md")
    scoped_url = f"{url}?mastery_template={template_id}"

    response = teaching_http.client.get(scoped_url, headers=_auth("sara"))
    assert response.status_code == 404, (
        f"open path + non-existent doc should be 404, got {response.status_code}"
    )
