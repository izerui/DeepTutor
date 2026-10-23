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


def test_course_kb_resources_are_registry_references(as_user, make_kb, tmp_path):
    from deeptutor.services.courses import CourseService

    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        make_kb(manager, "physics")
        list_visible_knowledge_bases()
        record = registry.find_by_location(manager.base_dir, "physics")
        service = CourseService(root=tmp_path / "courses")
        course = service.create(name="Mechanics")
        resource = service.attach_resource(course.id, kind="knowledge_base", ref_id="physics")
        assert resource.ref_id == record.ref
        assert resource.label == "physics"
        [reference] = registry.list_references(record.kb_id)
        assert (reference.referrer_kind, reference.referrer_id) == ("course", course.id)
        assert reference.doc_ids is None and reference.created_by == "t1"
        assert service.attach_resource(
            course.id, kind="knowledge_base", ref_id=record.ref,
        ).id == resource.id
        assert len(service.get(course.id).resources) == 1

        service.detach_resource(course.id, resource.id)
        assert registry.list_references(record.kb_id) == []
        service.attach_resource(course.id, kind="knowledge_base", ref_id=record.ref)
        service.delete(course.id)
        assert registry.list_references(record.kb_id) == []


def test_another_owners_course_blocks_withdrawing_a_public_kb(as_user, make_kb, tmp_path):
    from fastapi import HTTPException

    from deeptutor.api.routers.knowledge import _delete_kb
    from deeptutor.services.courses import CourseService

    record = _public_physics(as_user, make_kb)
    with as_user("u2"):
        service = CourseService(root=tmp_path / "u2-courses")
        course = service.create(name="Borrowed")
        service.attach_resource(course.id, kind="knowledge_base", ref_id=record.ref)
    with as_user("t1", role="teacher"):
        with pytest.raises(registry.KbVisibilityError, match="other accounts"):
            registry.set_visibility(record.kb_id, "private")
        with pytest.raises(HTTPException) as error:
            _delete_kb(record.ref)
        assert error.value.status_code == 409
        assert f"course:{course.id}" in error.value.detail
    with as_user("u2"):
        service.delete(course.id)
    with as_user("t1", role="teacher"):
        registry.set_visibility(record.kb_id, "private")


def test_courses_skip_connectors_unreadable_and_missing_refs(as_user, make_kb, tmp_path):
    from deeptutor.multi_user.context import get_current_user
    from deeptutor.services.courses import CourseService

    record = _public_physics(as_user, make_kb)
    drafts = registry.find_by_location(record.base_dir, "drafts")
    with as_user("u1"):
        manager = current_kb_manager()
        make_kb(manager, "vault", entry={"type": "obsidian"})
        list_visible_knowledge_bases()
        connector = registry.find_by_location(manager.base_dir, "vault")
        service = CourseService(root=tmp_path / "courses")
        course = service.create(name="Notes")
        for ref in ("vault", drafts.ref, "kb:missing", "missing"):
            service.attach_resource(course.id, kind="knowledge_base", ref_id=ref)
        service.attach_resource(course.id, kind="book", ref_id=record.ref)
        for kb in (connector, drafts, record):
            assert registry.list_references(kb.kb_id) == []
        assert not registry.can_read(drafts, get_current_user())
        assert "course" not in registry._readers


def test_course_legacy_aliases_are_preserved_and_deduplicated(as_user, make_kb, tmp_path):
    from deeptutor.multi_user.context import get_current_user
    from deeptutor.services.courses import CourseResource, CourseService

    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        make_kb(manager, "physics")
        list_visible_knowledge_bases()
        record = registry.find_by_location(manager.base_dir, "physics")
        service = CourseService(root=tmp_path / "courses")
        course = service.create(name="Legacy")
        course.resources = [
            CourseResource("old1", "knowledge_base", "physics", "Physics", 0, 1.0),
            CourseResource("old2", "knowledge_base", "user:kb:physics", "Physics", 1, 2.0),
        ]
        service._save([course])
        service.attach_resource(course.id, kind="notebook", ref_id="notes")
        assert len(registry.list_references(record.kb_id)) == 1
        assert [item.ref_id for item in service.get(course.id).resources][:2] == [
            "physics", "user:kb:physics",
        ]
        assert service.attach_resource(
            course.id, kind="knowledge_base", ref_id=record.ref,
        ).id == "old1"
        service.detach_resource(course.id, "old1")
        assert len(registry.list_references(record.kb_id)) == 1
    with as_user("u2"):
        assert not registry.can_read(record, get_current_user())
    with as_user("t1", role="teacher"):
        service.detach_resource(course.id, "old2")
        assert registry.list_references(record.kb_id) == []


@pytest.mark.parametrize("operation", ["attach", "detach", "delete"])
@pytest.mark.parametrize("failure", ["registry", "save"])
def test_course_mutation_failure_preserves_resources_and_references(
    as_user, make_kb, tmp_path, monkeypatch, operation, failure,
):
    from contextlib import contextmanager

    from deeptutor.services.courses import CourseService

    record = _public_physics(as_user, make_kb)
    with as_user("u2"):
        service = CourseService(root=tmp_path / "courses")
        course = service.create(name="Borrowed")
        resource = service.attach_resource(
            course.id, kind="knowledge_base", ref_id=record.ref,
        )
        before = service.index_file.read_bytes()
        references = registry.list_references(record.kb_id)

        def fail(*args, **kwargs):
            raise OSError("injected failure")

        if failure == "save":
            monkeypatch.setattr(service, "_save", fail)
        else:
            write = registry._write

            @contextmanager
            def failed_commit():
                with write() as connection:
                    yield connection
                    fail()

            monkeypatch.setattr(registry, "_write", failed_commit)
        with pytest.raises(OSError, match="injected failure"):
            if operation == "attach":
                service.attach_resource(course.id, kind="notebook", ref_id="notes")
            elif operation == "detach":
                service.detach_resource(course.id, resource.id)
            else:
                service.delete(course.id)
        assert service.index_file.read_bytes() == before
        assert registry.list_references(record.kb_id) == references


def test_failed_course_kb_attach_is_not_saved(as_user, make_kb, tmp_path, monkeypatch):
    from deeptutor.services.courses import CourseService

    record = _public_physics(as_user, make_kb)
    with as_user("u2"):
        service = CourseService(root=tmp_path / "courses")
        course = service.create(name="Borrowed")

        def fail(*args, **kwargs):
            raise OSError("registry unavailable")

        monkeypatch.setattr(registry, "replace_references", fail)
        with pytest.raises(OSError, match="registry unavailable"):
            service.attach_resource(course.id, kind="knowledge_base", ref_id=record.ref)
        assert service.get(course.id).resources == []
        assert registry.list_references(record.kb_id) == []


def test_course_rollback_failure_is_reported(as_user, make_kb, tmp_path, monkeypatch, caplog):
    from deeptutor.services import courses

    record = _public_physics(as_user, make_kb)
    with as_user("u2"):
        service = courses.CourseService(root=tmp_path / "courses")
        course = service.create(name="Borrowed")

        def fail_sync(*args, **kwargs):
            raise OSError("registry unavailable")

        def fail_rollback(*args, **kwargs):
            raise OSError("disk unavailable")

        monkeypatch.setattr(registry, "replace_references", fail_sync)
        monkeypatch.setattr(courses, "atomic_write_text", fail_rollback)
        with pytest.raises(OSError, match="disk unavailable") as error:
            service.attach_resource(course.id, kind="knowledge_base", ref_id=record.ref)
        assert str(error.value.__context__) == "registry unavailable"
        assert f"Could not roll back course {course.id}" in caplog.text


@pytest.mark.parametrize("operation", ["detach", "delete"])
def test_course_sync_serializes_with_concurrent_mutations(
    as_user, make_kb, tmp_path, monkeypatch, operation,
):
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context
    import threading

    from deeptutor.services import courses

    record = _public_physics(as_user, make_kb)
    with as_user("u2"):
        service = courses.CourseService(root=tmp_path / "courses")
        other = courses.CourseService(root=service.root)
        course = service.create(name="Borrowed")
        entered = threading.Event()
        release = threading.Event()
        sync = courses._sync_kb_references

        def paused_sync(course_id, value):
            if not entered.is_set():
                entered.set()
                assert release.wait(5)
            sync(course_id, value)

        monkeypatch.setattr(courses, "_sync_kb_references", paused_sync)

        def remove():
            if operation == "detach":
                resource = other.get(course.id).resources[0]
                other.detach_resource(course.id, resource.id)
            else:
                other.delete(course.id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            attached = pool.submit(
                copy_context().run, service.attach_resource, course.id,
                kind="knowledge_base", ref_id=record.ref,
            )
            try:
                assert entered.wait(5)
                acquired = other._lock.acquire(blocking=False)
                if acquired:
                    other._lock.release()
                assert not acquired, "course lock must cover registry synchronization"
                removed = pool.submit(copy_context().run, remove)
            finally:
                release.set()
            attached.result(timeout=5)
            removed.result(timeout=5)
        assert registry.list_references(record.kb_id) == []
        if operation == "detach":
            assert other.get(course.id).resources == []
        else:
            assert other.list_courses() == []
