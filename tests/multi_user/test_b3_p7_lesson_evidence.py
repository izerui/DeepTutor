"""B3 P7 / T11-M3: lesson evidence follows a FILE source's doc_id, and a broken lookup degrades."""

from __future__ import annotations

import logging
import sqlite3
from types import SimpleNamespace

import pytest

from deeptutor.knowledge import registry as kb_registry
from deeptutor.learning.lesson_evidence import LessonUnavailable, selected_scope
from deeptutor.learning.models import TopicSource, TopicSourceKind


def _topic(kb_ref: str, path: str, doc_id: str):
    source = TopicSource(
        id="f0",
        kind=TopicSourceKind.FILE,
        source_id=path,
        label="Lesson",
        position=0,
        metadata={"kb_name": kb_ref, "path": path, "doc_id": doc_id},
    )
    point = SimpleNamespace(id="kp1", topic_source_ids=["f0"])
    progress = SimpleNamespace(
        modules=[SimpleNamespace(id="m1", name="Module", knowledge_points=[point])]
    )
    return SimpleNamespace(sources=[source]), progress


@pytest.fixture
def broken_lookup(monkeypatch):
    def fail(kb, doc_id):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("deeptutor.multi_user.knowledge_access.live_document_path", fail)


def test_a_pdf_whose_lookup_fails_is_unavailable(broken_lookup, caplog):
    topic, progress = _topic("kb_x", "notes/lesson.pdf", "doc_1")
    with caplog.at_level(logging.WARNING, logger="deeptutor.learning.lesson_evidence"):
        with pytest.raises(LessonUnavailable):
            selected_scope(topic, progress, "kp1")
    assert any(
        r.levelno == logging.WARNING and r.name == "deeptutor.learning.lesson_evidence"
        for r in caplog.records
    )


def test_a_non_pdf_whose_lookup_fails_keeps_the_text_route(broken_lookup, caplog):
    topic, progress = _topic("kb_x", "notes/lesson.md", "doc_1")
    with caplog.at_level(logging.WARNING, logger="deeptutor.learning.lesson_evidence"):
        assert selected_scope(topic, progress, "kp1") == {}
    # Prove the lookup actually failed and fell back to the captured path
    # (not simply skipped): the warning proves _document_key tried the registry.
    assert any(
        r.levelno == logging.WARNING
        and "doc_1" in r.getMessage()
        and r.name == "deeptutor.learning.lesson_evidence"
        for r in caplog.records
    )


def test_a_moved_pdf_is_found_by_doc_id_in_the_real_registry(
    teaching_users, as_account, make_kb
):
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    from deeptutor.multi_user.paths import get_account_path_service

    with as_account("tina"):
        root = get_account_path_service().get_knowledge_bases_root()
        root.mkdir(parents=True, exist_ok=True)
        folder = make_kb(KnowledgeBaseManager(base_dir=str(root)), "physics")
        raw = folder / "raw"
        (raw / "notes").mkdir()
        (raw / "notes" / "lesson.pdf").write_bytes(b"%PDF-1.4 lesson")
        record = kb_registry.ensure_registered(
            root, "physics", created_by=teaching_users["tina"]["id"]
        )
        kb_registry.refresh_documents(root, "physics")
        lesson = kb_registry.find_document(record.kb_id, "notes/lesson.pdf")

        (raw / "archive").mkdir()
        (raw / "notes" / "lesson.pdf").replace(raw / "archive" / "lesson.pdf")
        kb_registry.move_document_paths(record.kb_id, "notes/lesson.pdf", "archive/lesson.pdf")
        # An unrelated PDF now occupies the captured path.
        (raw / "notes" / "lesson.pdf").write_bytes(b"%PDF-1.4 impostor")
        kb_registry.refresh_documents(root, "physics")
        impostor = kb_registry.find_document(record.kb_id, "notes/lesson.pdf")
        assert impostor.doc_id != lesson.doc_id

    topic, progress = _topic(record.ref, "notes/lesson.pdf", lesson.doc_id)
    assert selected_scope(topic, progress, "kp1") == {record.ref: ["archive/lesson.pdf"]}
