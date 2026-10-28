"""B3 P9: teaching lifecycle tests — student deletion releases materials, three-store lock ordering.

P9-1: deleting a student who exclusively holds an old-revision KB reference
releases that reference while still-needed references survive.
P9-2: concurrent ``transfer_owner`` (``_joint_write``), ``shared_write``
and ``sync_material_references`` (``material_lock``) do not deadlock.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from deeptutor.knowledge import registry as kb_registry
from deeptutor.learning.models import TopicMetadata, TopicSource, TopicSourceKind
from deeptutor.learning.storage import LearningStore
from deeptutor.teaching import kb as kb_access
from deeptutor.teaching.instances import apply_pending_revision
from deeptutor.teaching.models import KpDecision
from deeptutor.teaching.service import handle_account_deleted, sync_material_references
from deeptutor.teaching.store import TeachingStore


# ---------------------------------------------------------------------------
# P9-1: deleting a student releases materials exclusive to their old revision
# ---------------------------------------------------------------------------


@pytest.fixture
def two_public_kbs(teaching_users, as_account, make_kb):
    """Two public KBs in Tom's account — one kept across revisions, one dropped."""
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    from deeptutor.multi_user.paths import get_account_path_service

    records = {}
    with as_account("tom"):
        root = get_account_path_service().get_knowledge_bases_root()
        root.mkdir(parents=True, exist_ok=True)
        manager = KnowledgeBaseManager(base_dir=str(root))
        for name in ("kept_kb", "dropped_kb"):
            make_kb(manager, name)
            record = kb_registry.ensure_registered(
                root, name, created_by=teaching_users["tom"]["id"]
            )
            kb_registry.set_visibility(record.kb_id, "public")
            records[name] = kb_registry.get_kb(record.kb_id)
    return SimpleNamespace(**records)


def test_deleting_a_student_releases_old_revision_material(
    two_public_kbs, teacher_draft, publish_template, as_account,
    teaching_users, edit_calculus_draft,
):
    """Sara stays on revision 1 (with both KBs); Sam moves to revision 2
    (only kept_kb). Deleting Sara releases dropped_kb.
    """
    kept = two_public_kbs.kept_kb     # .ref = "kb:...", .kb_id = raw id
    dropped = two_public_kbs.dropped_kb

    # Publish revision 1 with both KBs as sources.
    teacher_draft(
        sources=(
            TopicSource(
                id="s0",
                kind=TopicSourceKind.KNOWLEDGE_BASE,
                source_id=kept.ref,
                label="Kept notes",
                position=0,
            ),
            TopicSource(
                id="s1",
                kind=TopicSourceKind.KNOWLEDGE_BASE,
                source_id=dropped.ref,
                label="Dropped notes",
                position=1,
            ),
        )
    )
    publish_template(students=("sara", "sam"))
    template_id = TeachingStore().list_templates()[0].id

    # Both students access the path so their instances are created (applied_revision = 1).
    from deeptutor.teaching.instances import sync_assigned_instances
    for name in ("sara", "sam"):
        with as_account(name):
            sync_assigned_instances(LearningStore())

    # Both KBs referenced after revision 1.
    assert kept.kb_id in kb_access.referenced_kb_ids(template_id)
    assert dropped.kb_id in kb_access.referenced_kb_ids(template_id)

    # Edit the draft: remove the dropped source, keep only the kept one.
    with as_account("tina"):
        store = LearningStore()
        topic = store.get_topic("topic_calculus")
        store.put_topic(
            topic.metadata,
            [
                TopicSource(
                    id="s0",
                    kind=TopicSourceKind.KNOWLEDGE_BASE,
                    source_id=kept.ref,
                    label="Kept notes",
                    position=0,
                ),
            ],
        )

    # Publish revision 2 — the new revision only references kept_kb.
    ids = edit_calculus_draft()
    publish_template(kp_decisions={ids.limits: KpDecision(kind="redefined")})

    # Sam migrates to revision 2; Sara stays at revision 1.
    from deeptutor.teaching.instances import instance_path_id
    sam_path = instance_path_id(template_id)
    with as_account("sam"):
        apply_pending_revision(sam_path, LearningStore(), respect_lease=False)

    # dropped_kb is still referenced — Sara's active assignment retains rev 1.
    assert dropped.kb_id in kb_access.referenced_kb_ids(template_id)

    # Delete Sara: revoke + sync releases old-revision-exclusive materials.
    sara_id = teaching_users["sara"]["id"]
    adam_id = teaching_users["adam"]["id"]

    failed = handle_account_deleted(sara_id, successor_id=adam_id)
    assert failed == []

    # dropped_kb was exclusively needed by Sara's old revision: now released.
    assert dropped.kb_id not in kb_access.referenced_kb_ids(template_id)
    # kept_kb is still referenced (current revision and Sam's copy).
    assert kept.kb_id in kb_access.referenced_kb_ids(template_id)


# ---------------------------------------------------------------------------
# P9-2: three-store concurrent write lock ordering (no deadlock)
# ---------------------------------------------------------------------------


def test_concurrent_joint_write_shared_write_and_material_lock_do_not_deadlock(
    teacher_draft, publish_template, teaching_users, as_account
):
    """Multiple threads repeatedly take ``_joint_write`` (teaching->KB->Book),
    ``shared_write`` (KB->Book) and ``material_lock`` in parallel; all must
    finish within the timeout, with no deadlock and no ``database is locked``.
    """
    from deeptutor.book import sharing as book_sharing
    from deeptutor.teaching import store as teaching_store

    # Set up a minimal template so transfer_owner and sync have rows to work with.
    teacher_draft()
    publish_template(students=("sara",))

    # Create a second teacher so transfer_owner actually takes the lock and moves rows.
    store = TeachingStore()
    store.create_template(
        owner_user_id="locktest-teacher",
        title="Lock test",
        description="",
        draft_path_id="locktest_path",
        draft_workspace_id="",
    )

    template_id = store.list_templates()[0].id
    iterations = 8
    errors: list[str] = []
    barrier = threading.Barrier(3, timeout=10)

    def run_joint_write():
        """transfer_owner uses _joint_write (teaching->KB->Book)."""
        try:
            barrier.wait()
            for _ in range(iterations):
                # Transfer back and forth between two fake users so the lock
                # is actually acquired each time.
                store.transfer_owner("locktest-teacher", "locktest-target")
                store.transfer_owner("locktest-target", "locktest-teacher")
        except Exception as exc:
            errors.append(f"joint_write: {exc!r}")

    def run_shared_write():
        """shared_write takes KB->Book locks."""
        try:
            barrier.wait()
            for _ in range(iterations):
                with book_sharing.shared_write():
                    pass
        except Exception as exc:
            errors.append(f"shared_write: {exc!r}")

    def run_material_lock():
        """sync_material_references takes material_lock (then _write inside)."""
        try:
            barrier.wait()
            for _ in range(iterations):
                sync_material_references(template_id, store=store)
        except Exception as exc:
            errors.append(f"material_lock: {exc!r}")

    threads = [
        threading.Thread(target=run_joint_write, daemon=True),
        threading.Thread(target=run_shared_write, daemon=True),
        threading.Thread(target=run_material_lock, daemon=True),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    alive = [t for t in threads if t.is_alive()]
    assert not alive, f"deadlock: {len(alive)} thread(s) still running"
    assert errors == [], f"concurrent errors: {errors}"
