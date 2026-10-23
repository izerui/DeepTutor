"""Registry-aware course candidates and book fingerprints."""

from __future__ import annotations

import pytest

from deeptutor.knowledge import registry
from deeptutor.multi_user.knowledge_access import current_kb_manager, list_visible_knowledge_bases


@pytest.mark.parametrize("whole_catalog", [False, True])
def test_shared_legacy_consumer_follows_move_and_current_permissions(
    as_user, make_kb, whole_catalog,
):
    from fastapi import HTTPException

    from deeptutor.book.kb_health import fingerprint_kb_documents
    from deeptutor.multi_user.knowledge_access import resolve_kb
    from deeptutor.services.workspace import ContentWorkspaceService, data_migration, kb_move
    from deeptutor.services.workspace.context import workspace_context
    from deeptutor.services.workspace.knowledge import qualified_kb_id

    with as_user("root", role="admin"):
        manager = current_kb_manager()
        make_kb(manager, "atlas", text="shared history")
        record = registry.register_kb(
            base_dir=manager.base_dir, name="atlas", kind="uploaded",
            created_by="root", visibility="public",
        )
        destination = ContentWorkspaceService().create_workspace("Research")["workspace_id"]
        with workspace_context(destination):
            target = current_kb_manager().base_dir
    with as_user("reader"):
        saved = "admin:kb:atlas"
        assert resolve_kb(saved).base_dir == manager.base_dir
        fingerprints = fingerprint_kb_documents(saved)
        assert fingerprints
        selected = ContentWorkspaceService().create_workspace(
            "Reader", resources={"knowledge_bases": []},
        )["workspace_id"]
    with as_user("root", role="admin"):
        if whole_catalog:
            data_migration.migrate_data("", destination, ["knowledge_bases"])
        else:
            kb_move.move_kb(qualified_kb_id("atlas"), destination)
    with as_user("reader"):
        for workspace in ("", selected):
            with workspace_context(workspace):
                assert resolve_kb(saved).base_dir == target
                assert fingerprint_kb_documents(saved) == fingerprints
                assert resolve_kb("atlas").base_dir == target
                with pytest.raises(HTTPException):
                    resolve_kb(saved, require_write=True)
                for private_alias in (
                    "user:kb:atlas", "account:kb:atlas",
                    qualified_kb_id("atlas", destination),
                ):
                    with pytest.raises(HTTPException):
                        resolve_kb(private_alias)
    with as_user("root", role="admin"):
        registry.set_visibility(record.kb_id, "private", force=True)
    with as_user("reader"):
        for workspace in ("", selected):
            with workspace_context(workspace):
                for ref in (saved, "atlas", record.ref):
                    with pytest.raises(HTTPException):
                        resolve_kb(ref)
                assert fingerprint_kb_documents(saved) == {}


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


@pytest.mark.parametrize("whole_catalog", [False, True])
@pytest.mark.parametrize("failure", [None, "registry", "after_registry"])
def test_kb_move_registry_and_files_commit_together(
    as_user, make_kb, monkeypatch, whole_catalog, failure,
):
    from pathlib import Path

    from deeptutor.multi_user.knowledge_access import resolve_kb
    from deeptutor.services.workspace import ContentWorkspaceService
    from deeptutor.services.workspace import data_migration, kb_move
    from deeptutor.services.workspace.context import workspace_context
    from deeptutor.services.workspace.knowledge import move_aliases, qualified_kb_id

    with as_user("alice"):
        manager = current_kb_manager()
        source = manager.base_dir
        make_kb(manager, "atlas", text="retained source")
        record = registry.register_kb(
            base_dir=source, name="atlas", kind="uploaded",
            created_by="alice", visibility="public",
        )
        service = ContentWorkspaceService()
        destination = service.create_workspace("Research")["workspace_id"]
        with workspace_context(destination):
            target = current_kb_manager().base_dir
        source_config = (source / "kb_config.json").read_bytes()
        assert not (target / "kb_config.json").exists()
        relocate_name = "relocate_base_dir" if whole_catalog else "relocate_kb"
        relocate = getattr(registry, relocate_name)

        def fail_relocation(*args, **kwargs):
            if failure == "after_registry":
                relocate(*args, **kwargs)
            raise registry.KbRegistryError("injected registry failure")

        with monkeypatch.context() as patch:
            if failure:
                # Fail only the forward relocation; rollback must remain usable.
                def forward_only(*args, **kwargs):
                    if Path(args[1]).resolve() == target.resolve():
                        return fail_relocation(*args, **kwargs)
                    return relocate(*args, **kwargs)

                patch.setattr(registry, relocate_name, forward_only)

            def run():
                if whole_catalog:
                    return data_migration.migrate_data("", destination, ["knowledge_bases"])
                return kb_move.move_kb(qualified_kb_id("atlas"), destination)

            if failure:
                with pytest.raises(registry.KbRegistryError, match="injected"):
                    run()
            else:
                assert run()["status"] == ("completed" if whole_catalog else "moved")
        expected = source if failure else target
        moved = registry.get_kb(record.kb_id)
        assert moved.base_dir == str(expected.resolve())
        assert moved.visibility == "public"
        assert moved.created_by == "alice"
        assert resolve_kb(record.ref).base_dir == expected.resolve()
        assert (expected / "atlas/raw/doc.md").read_text() == "retained source"
        assert not ((target if failure else source) / "atlas").exists()
        if failure:
            assert (source / "kb_config.json").read_bytes() == source_config
            assert move_aliases() == {}
            if whole_catalog:
                operation = data_migration.operations()[0]
                assert operation["status"] == "failed"
                assert data_migration.recover_operation(operation["id"])["status"] == "recovered"
            else:
                assert not (target / "kb_config.json").exists()


def test_kb_grants_no_longer_block_moves_or_migrations(
    seed_user, as_user, make_kb, monkeypatch,
):
    from deeptutor.multi_user.grants import load_grant, save_grant
    from deeptutor.services import auth
    from deeptutor.services.workspace import ContentWorkspaceService
    from deeptutor.services.workspace.data_migration import _shared_source_blockers
    from deeptutor.services.workspace.kb_move import preview_kb_move
    from deeptutor.services.workspace.knowledge import qualified_kb_id

    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    seed_user("boss", role="admin")
    alice = seed_user("alice")
    entries = [{"resource_id": "admin:kb:atlas", "legacy": {"label": "Atlas"}}]
    save_grant(alice["id"], {"knowledge_bases": entries})
    assert load_grant(alice["id"])["knowledge_bases"] == entries
    with as_user("root", role="admin"):
        make_kb(current_kb_manager(), "atlas")
        destination = ContentWorkspaceService().create_workspace("Research")["workspace_id"]
        assert preview_kb_move(qualified_kb_id("atlas"), destination)["blockers"] == []
        assert _shared_source_blockers("", {"knowledge_bases"}) == []


@pytest.mark.asyncio
async def test_admin_kb_grants_are_retired_but_payload_round_trips(
    seed_user, as_user, monkeypatch,
):
    from deeptutor.api.routers import multi_user

    assert not hasattr(multi_user, "_admin_kb_summary")
    seed_user("boss", role="admin")
    alice = seed_user("alice")
    audits = []
    monkeypatch.setattr(
        multi_user, "log_admin_action", lambda *args, **kwargs: audits.append(kwargs),
    )
    with as_user("root", role="admin"):
        entries = [{"resource_id": "admin:kb:atlas", "name": "atlas"}]
        response = await multi_user.put_user_grants(
            alice["id"], multi_user.GrantPayload(grant={"knowledge_bases": entries}), None,
        )
        assert response["grant"]["knowledge_bases"] == entries
        assert (await multi_user.get_user_grants(alice["id"], None))["grant"][
            "knowledge_bases"
        ] == entries
        assert "kb_count" not in audits[-1]["summary"]
        from deeptutor.multi_user.context import get_current_user

        report = await multi_user.guardian_report(alice["id"], get_current_user())
        assert report["grant_summary"]["knowledge_base_count"] == 0
        assert "knowledge_bases" not in await multi_user.admin_resources(None)


@pytest.mark.asyncio
async def test_admin_migration_results_lists_typed_items():
    from deeptutor.api.routers import multi_user

    registry.add_migration_item(
        "kb_made_public", "kb1", {"kb_id": "kb1", "name": "atlas", "users": ["alice"]},
    )
    registry.add_migration_item("book_auto_public", "b1", {"title": "Plan D adds kinds"})
    body = await multi_user.admin_migration_results(None)
    assert [(item["kind"], item["key"]) for item in body["items"]] == [
        ("kb_made_public", "kb1"), ("book_auto_public", "b1"),
    ]
    assert body["items"][0]["data"]["users"] == ["alice"]
    route = next(
        route for route in multi_user.router.routes
        if route.path.endswith("/admin/migration-results")
    )
    assert any(
        dependency.call is multi_user.require_admin
        for dependency in route.dependant.dependencies
    )


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


@pytest.mark.parametrize("reason,cleanup", [
    ("made_private", "detach"), ("made_private", "delete"), ("made_private", "restore"),
    ("kb_deleted", "detach"), ("kb_deleted", "delete"),
])
def test_course_retains_revocations_until_resource_removed_or_restored(
    as_user, make_kb, tmp_path, reason, cleanup,
):
    from deeptutor.services.courses import CourseService

    record = _public_physics(as_user, make_kb)
    with as_user("u2"):
        service = CourseService(root=tmp_path / "courses")
        course = service.create(name="Borrowed")
        resource = service.attach_resource(course.id, kind="knowledge_base", ref_id=record.ref)
    with as_user("admin", role="admin"):
        if reason == "made_private":
            registry.set_visibility(record.kb_id, "private", force=True)
        else:
            registry.unregister_kb(record.kb_id)
    [revoked] = registry.list_unavailable_references("course", course.id)
    assert revoked.reason == reason
    with as_user("u2"):
        notebook = service.attach_resource(course.id, kind="notebook", ref_id="notes")
        assert registry.list_unavailable_references("course", course.id) == [revoked]
        service.detach_resource(course.id, notebook.id)
        assert registry.list_unavailable_references("course", course.id) == [revoked]
        assert service.get(course.id).resources[0].id == resource.id
        assert registry.list_references(record.kb_id) == []
        if cleanup == "detach":
            service.detach_resource(course.id, resource.id)
        elif cleanup == "delete":
            service.delete(course.id)
        else:
            with as_user("t1", role="teacher"):
                registry.set_visibility(record.kb_id, "public")
            service.attach_resource(course.id, kind="notebook", ref_id="restored")
            [reference] = registry.list_references(record.kb_id)
            assert reference.referrer_id == course.id
        assert registry.list_unavailable_references("course", course.id) == []


@pytest.mark.parametrize("existing", [False, True])
def test_course_revalidates_visibility_before_replacing_references(
    as_user, make_kb, tmp_path, monkeypatch, existing,
):
    from deeptutor.services.courses import CourseService

    record = _public_physics(as_user, make_kb)
    with as_user("u2"):
        service = CourseService(root=tmp_path / "courses")
        course = service.create(name="Borrowed")
        if existing:
            service.attach_resource(course.id, kind="knowledge_base", ref_id=record.ref)
        before = service.index_file.read_bytes()
        replace = registry.replace_references
        withdrawn = []

        def withdraw_then_replace(*args, **kwargs):
            # Deterministically commit withdrawal after course validation but
            # before replacement acquires its registry write transaction.
            assert args[2] == [(record.kb_id, None)]
            with as_user("admin" if existing else "t1", role="admin" if existing else "teacher"):
                registry.set_visibility(record.kb_id, "private", force=existing)
            withdrawn.extend(registry.list_unavailable_references("course", course.id))
            return replace(*args, **kwargs)

        monkeypatch.setattr(registry, "replace_references", withdraw_then_replace)
        with pytest.raises(registry.KbReferenceError, match="read"):
            service.attach_resource(
                course.id,
                kind="notebook" if existing else "knowledge_base",
                ref_id="notes" if existing else record.ref,
            )
        assert service.index_file.read_bytes() == before
        assert registry.list_references(record.kb_id) == []
        assert registry.list_unavailable_references("course", course.id) == withdrawn
        assert len(withdrawn) == int(existing)


@pytest.mark.parametrize("aliases", [
    ("physics",), ("user:kb:physics",), ("physics", "user:kb:physics"),
])
@pytest.mark.parametrize("recreate", [False, True])
@pytest.mark.parametrize("cleanup", ["detach", "delete"])
def test_course_legacy_deleted_identity_retains_revocation(
    as_user, make_kb, tmp_path, aliases, recreate, cleanup,
):
    from pathlib import Path

    from deeptutor.api.routers.knowledge import _delete_kb
    from deeptutor.services.courses import CourseResource, CourseService
    from deeptutor.services.file_io import atomic_write_json

    foreign = _public_physics(as_user, make_kb)
    with as_user("u2"):
        manager = current_kb_manager()
        make_kb(manager, "physics")
        list_visible_knowledge_bases()
        original = registry.find_by_location(manager.base_dir, "physics")
        service = CourseService(root=tmp_path / "courses")
        course = service.create(name="Legacy")
        course.resources = [
            CourseResource(f"old{i}", "knowledge_base", alias, "Physics", i, 1.0)
            for i, alias in enumerate(aliases)
        ]
        # Simulate old JSON without identity metadata.
        payload = course.to_dict()
        for resource in payload["resources"]:
            resource.pop("kb_id", None)
        atomic_write_json(service.index_file, {"courses": [payload]})
        notebook = service.attach_resource(course.id, kind="notebook", ref_id="notes")
        assert len(registry.list_references(original.kb_id)) == 1
        assert registry.list_references(foreign.kb_id) == []

    with as_user("admin", role="admin"):
        _delete_kb(original.ref, force=True)
    assert not (Path(original.base_dir) / original.dir_name).exists()
    assert registry.get_kb(original.kb_id) is None
    [revoked] = registry.list_unavailable_references("course", course.id)
    assert revoked.reason == "kb_deleted"

    with as_user("u2"):
        assert all(registry.resolve_ref(alias) is None for alias in aliases)
        replacement = None
        if recreate:
            make_kb(manager, "physics")
            list_visible_knowledge_bases()
            replacement = registry.find_by_location(manager.base_dir, "physics")
            assert replacement.kb_id not in (original.kb_id, foreign.kb_id)
        # Reload from disk to ensure the identity survives service lifetimes.
        service = CourseService(root=service.root)
        service.detach_resource(course.id, notebook.id)
        assert registry.list_unavailable_references("course", course.id) == [revoked]
        service.attach_resource(course.id, kind="notebook", ref_id="other")
        assert registry.list_unavailable_references("course", course.id) == [revoked]
        resources = service.get(course.id).resources[:len(aliases)]
        assert [item.ref_id for item in resources] == list(aliases)
        assert all(item.kb_id == original.kb_id for item in resources)
        assert registry.list_references(foreign.kb_id) == []
        if replacement is not None:
            assert registry.list_references(replacement.kb_id) == []
            attached = service.attach_resource(
                course.id, kind="knowledge_base", ref_id=replacement.ref,
            )
            assert attached.id not in {item.id for item in resources}
            assert len(registry.list_references(replacement.kb_id)) == 1
            assert registry.list_unavailable_references("course", course.id) == [revoked]
        if cleanup == "delete":
            service.delete(course.id)
        else:
            for index, resource in enumerate(resources):
                service.detach_resource(course.id, resource.id)
                assert registry.list_unavailable_references("course", course.id) == (
                    [revoked] if index < len(resources) - 1 else []
                )
            if replacement is not None:
                assert len(registry.list_references(replacement.kb_id)) == 1
        assert registry.list_unavailable_references("course", course.id) == []


def test_course_legacy_identity_save_rolls_back_on_sync_failure(
    as_user, make_kb, tmp_path, monkeypatch,
):
    from deeptutor.services.courses import CourseResource, CourseService

    with as_user("u2"):
        manager = current_kb_manager()
        make_kb(manager, "physics")
        list_visible_knowledge_bases()
        record = registry.find_by_location(manager.base_dir, "physics")
        service = CourseService(root=tmp_path / "courses")
        course = service.create(name="Legacy")
        course.resources = [
            CourseResource("old", "knowledge_base", "physics", "Physics", 0, 1.0),
        ]
        service._save([course])
        before = service.index_file.read_bytes()

        def fail_sync(*args, **kwargs):
            saved = service.get(course.id).resources[0]
            assert saved.ref_id == "physics"
            assert saved.kb_id == record.kb_id
            raise registry.KbReferenceError("injected sync failure")

        with monkeypatch.context() as patch:
            patch.setattr(registry, "replace_references", fail_sync)
            with pytest.raises(registry.KbReferenceError, match="injected"):
                service.attach_resource(course.id, kind="notebook", ref_id="notes")
        assert service.index_file.read_bytes() == before
        assert registry.list_references(record.kb_id) == []
        service.attach_resource(course.id, kind="notebook", ref_id="notes")
        assert service.get(course.id).resources[0].kb_id == record.kb_id
        assert len(registry.list_references(record.kb_id)) == 1


def test_course_does_not_sync_legacy_identity_unresolved_at_save(
    as_user, make_kb, tmp_path, monkeypatch,
):
    from deeptutor.services.courses import CourseResource, CourseService

    with as_user("u2"):
        manager = current_kb_manager()
        make_kb(manager, "physics")
        list_visible_knowledge_bases()
        record = registry.find_by_location(manager.base_dir, "physics")
        service = CourseService(root=tmp_path / "courses")
        course = service.create(name="Legacy")
        course.resources = [
            CourseResource("old", "knowledge_base", "physics", "Physics", 0, 1.0),
        ]
        service._save([course])
        resolve = registry.resolve_ref
        calls = []

        def initially_missing(ref):
            calls.append(ref)
            return None if len(calls) == 1 else resolve(ref)

        with monkeypatch.context() as patch:
            patch.setattr(registry, "resolve_ref", initially_missing)
            service.attach_resource(course.id, kind="notebook", ref_id="notes")
        assert service.get(course.id).resources[0].kb_id == ""
        assert registry.list_references(record.kb_id) == []
        service.attach_resource(course.id, kind="notebook", ref_id="other")
        assert service.get(course.id).resources[0].kb_id == record.kb_id
        assert len(registry.list_references(record.kb_id)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery_fails", [False, True])
async def test_kb_migration_commit_failure_restores_registry_sessions_and_files(
    as_user, make_kb, monkeypatch, recovery_fails,
):
    from deeptutor.services.session import get_sqlite_session_store
    from deeptutor.services.workspace import ContentWorkspaceService, WorkspaceError
    from deeptutor.services.workspace import data_migration
    from deeptutor.services.workspace.context import workspace_context

    with as_user("alice"):
        manager = current_kb_manager()
        source = manager.base_dir
        make_kb(manager, "atlas")
        record = registry.register_kb(
            base_dir=source, name="atlas", kind="uploaded", created_by="alice",
        )
        source_store = get_sqlite_session_store()
        session = await source_store.create_session("Retain conversation")
        await source_store.add_message(session["id"], "user", "retained message")
        destination = ContentWorkspaceService().create_workspace("Research")["workspace_id"]
        with workspace_context(destination):
            target = current_kb_manager().base_dir
        write = data_migration.atomic_write_json
        relocate = registry.relocate_kb

        def fail_commit(path, payload):
            if payload.get("status") == "committed":
                assert registry.get_kb(record.kb_id).base_dir == str(target.resolve())
                raise OSError("injected commit journal failure")
            return write(path, payload)

        def fail_restore(*args, **kwargs):
            raise registry.KbRegistryError("injected recovery failure")

        with monkeypatch.context() as patch:
            patch.setattr(data_migration, "atomic_write_json", fail_commit)
            if recovery_fails:
                patch.setattr(registry, "relocate_kb", fail_restore)
            with pytest.raises(
                WorkspaceError if recovery_fails else OSError,
                match="Recover" if recovery_fails else "commit journal",
            ):
                data_migration.migrate_data("", destination, ["knowledge_bases", "chat"])
        operation = data_migration.operations()[0]
        assert operation["kb_registry_ids"] == [record.kb_id]
        assert operation["status"] == ("recovery_required" if recovery_fails else "failed")
        assert (source / "atlas/raw/doc.md").read_text() == "hello"
        if recovery_fails:
            assert (target / "atlas/raw/doc.md").read_text() == "hello"
            assert registry.get_kb(record.kb_id).base_dir == str(target.resolve())
        assert data_migration.recover_operation(operation["id"])["status"] == "recovered"
        assert registry.get_kb(record.kb_id).base_dir == str(source.resolve())
        assert not (target / "atlas").exists()
        saved = await get_sqlite_session_store().get_session(session["id"])
        assert saved is not None
        assert (await get_sqlite_session_store().get_messages(session["id"]))[0][
            "content"
        ] == "retained message"
        with workspace_context(destination):
            assert await get_sqlite_session_store().get_session(session["id"]) is None
        assert registry.relocate_kb is relocate


def test_kb_migration_cleanup_recovery_keeps_relocated_registry(
    as_user, make_kb, monkeypatch,
):
    from deeptutor.services.task_board import TaskBoardStore
    from deeptutor.services.workspace import ContentWorkspaceService
    from deeptutor.services.workspace import data_migration
    from deeptutor.services.workspace.context import workspace_context

    with as_user("alice"):
        manager = current_kb_manager()
        source = manager.base_dir
        make_kb(manager, "atlas")
        record = registry.register_kb(
            base_dir=source, name="atlas", kind="uploaded", created_by="alice",
        )
        destination = ContentWorkspaceService().create_workspace("Research")["workspace_id"]
        with workspace_context(destination):
            target = current_kb_manager().base_dir

        def fail_cleanup(*args, **kwargs):
            raise OSError("injected cleanup failure")

        with monkeypatch.context() as patch:
            patch.setattr(TaskBoardStore, "move_session_links", fail_cleanup)
            with pytest.raises(OSError, match="cleanup"):
                data_migration.migrate_data("", destination, ["knowledge_bases"])
        operation = data_migration.operations()[0]
        assert operation["status"] == "cleanup_required"
        assert registry.get_kb(record.kb_id).base_dir == str(target.resolve())
        assert data_migration.recover_operation(operation["id"])["status"] == "completed"
        assert registry.get_kb(record.kb_id).base_dir == str(target.resolve())
        assert (target / "atlas/raw/doc.md").read_text() == "hello"
        assert not (source / "atlas").exists()


def test_stable_workspace_kb_reference_still_blocks_catalog_migration(as_user, make_kb):
    from deeptutor.services.workspace import ContentWorkspaceService, WorkspaceError
    from deeptutor.services.workspace.data_migration import migrate_data, preview
    from deeptutor.services.workspace.resources import resource_usage

    with as_user("alice"):
        manager = current_kb_manager()
        make_kb(manager, "atlas")
        record = registry.register_kb(
            base_dir=manager.base_dir, name="atlas", kind="uploaded", created_by="alice",
        )
        service = ContentWorkspaceService()
        consumer = service.create_workspace("Consumer", resources={"knowledge_bases": [record.ref]})
        destination = service.create_workspace("Research")["workspace_id"]
        assert consumer["workspace_id"] in {
            item["workspace_id"] for item in resource_usage("knowledge_bases", record.ref)
        }
        assert any("Consumer" in blocker for blocker in preview(
            "", destination, ["knowledge_bases"],
        )["blockers"])
        with pytest.raises(WorkspaceError, match="Consumer"):
            migrate_data("", destination, ["knowledge_bases"])
        assert registry.get_kb(record.kb_id).base_dir == str(manager.base_dir.resolve())
        assert (manager.base_dir / "atlas/raw/doc.md").read_text() == "hello"
