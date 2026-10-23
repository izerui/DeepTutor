"""Registry-aware course candidates and book fingerprints."""

from __future__ import annotations

import pytest

from deeptutor.knowledge import registry
from deeptutor.multi_user.knowledge_access import current_kb_manager, list_visible_knowledge_bases


def _public_physics(as_user, make_kb):
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        make_kb(manager, "physics", text="waves")
        list_visible_knowledge_bases()
        record = registry.find_by_location(manager.base_dir, "physics")
        assert record is not None
        registry.set_visibility(record.kb_id, "public")
        make_kb(manager, "drafts")
        list_visible_knowledge_bases()
    return record


@pytest.mark.asyncio
async def test_course_kb_index_uses_the_unified_list(as_user, make_kb):
    from deeptutor.services.courses_state import _knowledge_base_index

    record = _public_physics(as_user, make_kb)
    with as_user("t1", role="teacher"):
        own = await _knowledge_base_index()
        assert own["physics"]["ref"] == record.ref
        assert own["user:kb:physics"]["visibility"] == "public"
    with as_user("s1", role="student"):
        index = await _knowledge_base_index()
        assert index[record.ref]["name"] == "physics"
        assert "physics" not in index
        assert all(detail["name"] != "drafts" for detail in index.values())
        make_kb(current_kb_manager(), "physics", text="my notes")
        index = await _knowledge_base_index()
        assert index["physics"]["ref"] != record.ref
        assert index["physics"]["ref"] == index["user:kb:physics"]["ref"]
        assert record.ref in index


@pytest.mark.asyncio
async def test_course_candidates_offer_each_kb_once_by_kb_ref(as_user, make_kb):
    from deeptutor.services.courses_state import build_course_resource_candidates

    record = _public_physics(as_user, make_kb)
    with as_user("t1", role="teacher"):
        rows = (await build_course_resource_candidates())["knowledge_base"]
    assert [row["ref_id"] for row in rows if row["label"] == "physics"] == [record.ref]
    assert len(rows) == 2
    assert all(row["ref_id"].startswith("kb:") for row in rows)
    with as_user("s1", role="student"):
        rows = (await build_course_resource_candidates())["knowledge_base"]
    assert rows == [{"ref_id": record.ref, "label": "physics"}]


@pytest.mark.asyncio
async def test_courses_exclude_personal_connectors(as_user, make_kb):
    from deeptutor.services.courses_state import (
        _knowledge_base_index,
        build_course_resource_candidates,
    )

    with as_user("t1", role="teacher"):
        make_kb(current_kb_manager(), "vault", entry={"type": "obsidian"})
        assert await _knowledge_base_index() == {}
        assert (await build_course_resource_candidates())["knowledge_base"] == []


def test_book_fingerprints_follow_kb_refs(as_user, make_kb):
    from deeptutor.book.kb_health import (
        fingerprint_kb_documents,
        fingerprint_kb_documents_batch,
        fingerprint_kbs,
    )

    record = _public_physics(as_user, make_kb)
    drafts = registry.find_by_location(record.base_dir, "drafts")
    assert drafts is not None
    with as_user("s1", role="student"):
        documents = fingerprint_kb_documents(record.ref)
        assert list(documents) == ["doc.md"]
        assert documents["doc.md"].startswith("sha256:")
        assert fingerprint_kb_documents(drafts.ref) == {}
        assert fingerprint_kb_documents("kb:missing") == {}
        assert fingerprint_kb_documents("physics") == {}
        refs = [record.ref, drafts.ref, "missing"]
        assert fingerprint_kb_documents_batch(refs) == {
            record.ref: documents, drafts.ref: {}, "missing": {},
        }
        fingerprints = fingerprint_kbs(refs)
        assert fingerprints[record.ref]
        assert fingerprints[drafts.ref] == fingerprints["missing"] == ""
    with as_user("t1", role="teacher"):
        assert fingerprint_kb_documents("physics") == documents
        assert fingerprint_kb_documents("physics", manager=current_kb_manager()) == documents
        registry.set_visibility(record.kb_id, "private")
    with as_user("s1", role="student"):
        assert fingerprint_kb_documents(record.ref) == {}
