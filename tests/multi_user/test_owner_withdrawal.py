"""Rule R1 (B2-19): an owner may always withdraw or delete a referenced KB / book.

Other accounts' course / partner references are revoked and their referrers
see the material as unavailable. Published teacher paths need one explicit
confirmation: the first request answers 409 with aggregate counts only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deeptutor.knowledge import registry as kb_registry
from deeptutor.learning.models import TopicSource, TopicSourceKind

CODE = "referenced_by_teacher_paths"


def _auth(name: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {name}"}


def _kb_source(record) -> TopicSource:
    return TopicSource(
        id="s0", kind=TopicSourceKind.KNOWLEDGE_BASE, source_id=record.ref,
        label="Algebra notes", position=0,
    )


@pytest.fixture
def toms_kb(teaching_users, as_account, make_kb):
    """Tom's (teacher, not admin) public KB in his account library."""
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    from deeptutor.multi_user.paths import get_account_path_service

    with as_account("tom"):
        root = get_account_path_service().get_knowledge_bases_root()
        root.mkdir(parents=True, exist_ok=True)
        make_kb(KnowledgeBaseManager(base_dir=str(root)), "algebra")
        record = kb_registry.ensure_registered(
            root, "algebra", created_by=teaching_users["tom"]["id"]
        )
        kb_registry.set_visibility(record.kb_id, "public")
    return kb_registry.get_kb(record.kb_id)


@pytest.fixture
def cited_by_tinas_path(toms_kb, teacher_draft, publish_template):
    """Tina's published path cites Tom's public KB; Sara and Sam study it."""
    teacher_draft(sources=(_kb_source(toms_kb),))
    result = publish_template(students=("sara", "sam"))
    assert result["warnings"] == []
    return result["template"]["id"]


def _foreign_ids(teaching_users, *extra: str) -> list[str]:
    ids = [teaching_users[name]["id"] for name in ("tina", "sara", "sam")]
    return [*ids, "tina", "sara", "sam", *extra]


def _assert_counts_only(detail, teaching_users, template_id: str, *, paths: int, students: int):
    assert detail["code"] == CODE and detail["requires_confirmation"] is True
    assert (detail["affected_paths"], detail["affected_students"]) == (paths, students)
    text = json.dumps(detail)
    for foreign in _foreign_ids(teaching_users, template_id):
        assert f'"{foreign}"' not in text and f":{foreign}" not in text and f" {foreign}" not in text


def _materials_unavailable(teaching_http, template_id: str) -> list[str]:
    rows = teaching_http.client.get(
        "/api/mastery-paths/teaching/templates", headers=_auth("tina")
    ).json()
    rows = rows.get("templates", rows) if isinstance(rows, dict) else rows
    [row] = [row for row in rows if row["id"] == template_id]
    return row["materials_unavailable"]


# ---- 1. course / partner references never block the owner -------------------


@pytest.mark.parametrize("operation", ["private", "delete"])
def test_course_and_partner_references_do_not_block_the_owner(
    toms_kb, teaching_http, teaching_users, operation
):
    tina = teaching_users["tina"]["id"]
    kb_registry.add_reference(toms_kb.kb_id, referrer_kind="course", referrer_id="c1", created_by=tina)
    kb_registry.add_reference(toms_kb.kb_id, referrer_kind="partner", referrer_id="p1", created_by=tina)
    client = teaching_http.client
    if operation == "private":
        response = client.put(
            f"/api/knowledge-bases/{toms_kb.ref}/visibility",
            json={"visibility": "private"}, headers=_auth("tom"),
        )
        reason = "made_private"
    else:
        response = client.delete(f"/api/knowledge-bases/{toms_kb.ref}", headers=_auth("tom"))
        reason = "kb_deleted"
    assert response.status_code == 200, response.text
    assert kb_registry.list_references(toms_kb.kb_id) == []
    for kind, referrer in (("course", "c1"), ("partner", "p1")):
        [revoked] = kb_registry.list_unavailable_references(kind, referrer)
        assert (revoked.kb_id, revoked.reason, revoked.kb_name) == (toms_kb.kb_id, reason, "algebra")


def test_partner_lists_a_withdrawn_kb_as_unavailable_and_can_dismiss_it(
    toms_kb, teaching_users, as_account
):
    from deeptutor.services.partners import workspace

    tina = teaching_users["tina"]["id"]
    kb_registry.add_reference(toms_kb.kb_id, referrer_kind="partner", referrer_id="p1", created_by=tina)
    with as_account("tom"):
        kb_registry.set_visibility(toms_kb.kb_id, "private")
    assets = workspace.list_assets("p1")
    [item] = [kb for kb in assets["knowledge_bases"] if kb.get("ref") == toms_kb.ref]
    assert item["unavailable"] is True and item["reason"] == "made_private"
    assert item["name"] == "algebra"
    assert workspace.remove_asset("p1", "knowledge_base", toms_kb.ref) is True
    assert kb_registry.list_unavailable_references("partner", "p1") == []
    assert not [kb for kb in workspace.list_assets("p1")["knowledge_bases"] if kb.get("ref") == toms_kb.ref]


def test_course_marks_a_withdrawn_kb_unavailable_with_its_reason(
    toms_kb, teaching_users, as_account
):
    from deeptutor.services import courses_state

    tina = teaching_users["tina"]["id"]
    kb_registry.add_reference(toms_kb.kb_id, referrer_kind="course", referrer_id="c1", created_by=tina)
    with as_account("tom"):
        kb_registry.set_visibility(toms_kb.kb_id, "private")
    reasons = courses_state.withdrawn_kb_reasons("c1")
    assert reasons == {toms_kb.kb_id: "made_private"}


# ---- 3. published teacher paths: counts-only 409, then confirm ----------------


def test_withdrawing_a_kb_cited_by_a_teacher_path_needs_confirmation(
    cited_by_tinas_path, toms_kb, teaching_http, teaching_users
):
    template_id = cited_by_tinas_path
    client = teaching_http.client
    url = f"/api/knowledge-bases/{toms_kb.ref}/visibility"
    first = client.put(url, json={"visibility": "private"}, headers=_auth("tom"))
    assert first.status_code == 409
    _assert_counts_only(first.json()["detail"], teaching_users, template_id, paths=1, students=2)
    assert kb_registry.get_kb(toms_kb.kb_id).visibility == "public"
    assert kb_registry.list_unavailable_references("mastery_template", template_id) == []
    assert _materials_unavailable(teaching_http, template_id) == []

    second = client.put(url, json={"visibility": "private", "confirm": True}, headers=_auth("tom"))
    assert second.status_code == 200, second.text
    assert kb_registry.get_kb(toms_kb.kb_id).visibility == "private"
    [revoked] = kb_registry.list_unavailable_references("mastery_template", template_id)
    assert revoked.reason == "made_private"
    assert _materials_unavailable(teaching_http, template_id) == ["Algebra notes"]


@pytest.mark.parametrize("body_route", [False, True])
def test_deleting_a_kb_cited_by_a_teacher_path_needs_confirmation(
    cited_by_tinas_path, toms_kb, teaching_http, teaching_users, body_route
):
    template_id = cited_by_tinas_path
    client = teaching_http.client

    def delete(confirm: bool):
        if body_route:
            return client.post(
                "/api/knowledge-bases/delete",
                json={"name": toms_kb.ref, "confirm": confirm}, headers=_auth("tom"),
            )
        suffix = "?confirm=true" if confirm else ""
        return client.delete(f"/api/knowledge-bases/{toms_kb.ref}{suffix}", headers=_auth("tom"))

    first = delete(False)
    assert first.status_code == 409
    _assert_counts_only(first.json()["detail"], teaching_users, template_id, paths=1, students=2)
    assert kb_registry.get_kb(toms_kb.kb_id) is not None
    assert Path(toms_kb.base_dir, toms_kb.dir_name).is_dir()

    second = delete(True)
    assert second.status_code == 200, second.text
    assert kb_registry.get_kb(toms_kb.kb_id) is None
    assert not Path(toms_kb.base_dir, toms_kb.dir_name).exists()
    [revoked] = kb_registry.list_unavailable_references("mastery_template", template_id)
    assert revoked.reason == "kb_deleted"
    assert _materials_unavailable(teaching_http, template_id) == ["Algebra notes"]


def test_owners_own_teacher_path_counts_for_delete_but_not_for_withdrawal(
    teaching_users, as_account, make_kb, teacher_draft, publish_template, teaching_http
):
    """Tina's own path keeps reading her private KB, so only deletion affects it."""
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    from deeptutor.multi_user.paths import get_account_path_service

    with as_account("tina"):
        root = get_account_path_service().get_knowledge_bases_root()
        root.mkdir(parents=True, exist_ok=True)
        make_kb(KnowledgeBaseManager(base_dir=str(root)), "keys")
        record = kb_registry.ensure_registered(root, "keys", created_by=teaching_users["tina"]["id"])
        kb_registry.set_visibility(record.kb_id, "public")
    teacher_draft(sources=(_kb_source(record),))
    template_id = publish_template(students=("sara",))["template"]["id"]
    client = teaching_http.client
    response = client.put(
        f"/api/knowledge-bases/{record.ref}/visibility",
        json={"visibility": "private"}, headers=_auth("tina"),
    )
    assert response.status_code == 200, response.text
    assert kb_registry.list_unavailable_references("mastery_template", template_id) == []
    first = client.delete(f"/api/knowledge-bases/{record.ref}", headers=_auth("tina"))
    assert first.status_code == 409
    detail = first.json()["detail"]
    assert (detail["code"], detail["affected_paths"], detail["affected_students"]) == (CODE, 1, 1)
    assert client.delete(
        f"/api/knowledge-bases/{record.ref}?confirm=true", headers=_auth("tina")
    ).status_code == 200


def test_admin_force_still_withdraws_without_a_second_request(
    cited_by_tinas_path, toms_kb, teaching_http
):
    response = teaching_http.client.put(
        f"/api/knowledge-bases/{toms_kb.ref}/visibility",
        json={"visibility": "private", "force": True}, headers=_auth("adam"),
    )
    assert response.status_code == 200, response.text
    assert kb_registry.list_unavailable_references("mastery_template", cited_by_tinas_path)


def test_unpublished_template_references_do_not_ask_for_confirmation(
    toms_kb, teaching_http, teaching_users
):
    """A stale reference of an unknown/unpublished path has nobody to warn."""
    kb_registry.add_reference(
        toms_kb.kb_id, referrer_kind="mastery_template", referrer_id="tpl_gone",
        created_by=teaching_users["tina"]["id"],
    )
    response = teaching_http.client.put(
        f"/api/knowledge-bases/{toms_kb.ref}/visibility",
        json={"visibility": "private"}, headers=_auth("tom"),
    )
    assert response.status_code == 200, response.text
    assert kb_registry.list_unavailable_references("mastery_template", "tpl_gone")


# ---- 4. other accounts' referrer ids never reach the owner -------------------


def test_references_endpoint_lists_only_the_owners_own_references(
    cited_by_tinas_path, toms_kb, teaching_http, teaching_users
):
    tom = teaching_users["tom"]["id"]
    tina = teaching_users["tina"]["id"]
    kb_registry.add_reference(toms_kb.kb_id, referrer_kind="partner", referrer_id="toms_p", created_by=tom)
    kb_registry.add_reference(
        toms_kb.kb_id, referrer_kind="course", referrer_id="course_alpha", created_by=tina
    )
    body = teaching_http.client.get(
        f"/api/knowledge-bases/{toms_kb.ref}/references", headers=_auth("tom")
    ).json()
    assert [(r["referrer_kind"], r["referrer_id"]) for r in body["references"]] == [
        ("partner", "toms_p")
    ]
    assert body["other_references"] == {
        "total": 2, "by_kind": {"course": 1, "mastery_template": 1},
    }
    text = json.dumps(body)
    assert cited_by_tinas_path not in text and tina not in text and "course_alpha" not in text


@pytest.mark.asyncio
async def test_document_delete_conflicts_hide_other_accounts_referrers(
    toms_kb, teaching_users, as_account
):
    from fastapi import HTTPException

    from deeptutor.api.routers import knowledge

    tina = teaching_users["tina"]["id"]
    kb_registry.refresh_documents(toms_kb.base_dir, toms_kb.name)
    kb_registry.add_reference(
        toms_kb.kb_id, referrer_kind="course", referrer_id="course_alpha", created_by=tina
    )
    with as_account("tom"):
        with pytest.raises(HTTPException) as error:
            await knowledge.delete_kb_file(toms_kb.ref, "doc.md")
    detail = error.value.detail
    assert error.value.status_code == 409 and detail["code"] == "requires_confirmation"
    assert detail["references"] == []
    assert detail["other_references"] == {"total": 1, "by_kind": {"course": 1}}
    assert "course_alpha" not in json.dumps(detail) and tina not in json.dumps(detail)
