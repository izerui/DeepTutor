"""System-level persistence for teacher mastery templates."""

from __future__ import annotations

import sqlite3

import pytest

from deeptutor.learning.models import KnowledgePoint, KnowledgeType, LearningModule
from deeptutor.teaching.models import ChangeEntry, TemplateOutline, TemplateRevision
from deeptutor.teaching.store import TeachingStore, teaching_db_path


def _revision(template_id: str, number: int) -> TemplateRevision:
    module = LearningModule(
        id="m0",
        name="Calculus",
        order=0,
        knowledge_points=[
            KnowledgePoint(
                id="kp_limits", name="Limits", type=KnowledgeType.CONCEPT, module_id="m0"
            )
        ],
    )
    return TemplateRevision(
        template_id=template_id,
        revision=number,
        outline=TemplateOutline(name="Calculus I", modules=[module]),
        change_log={"kp_limits": ChangeEntry(kind="added")},
        published_by="u_tina",
    )


@pytest.fixture
def store(mu_isolated_root):
    return TeachingStore()


def _template(store: TeachingStore, owner: str = "u_tina", path_id: str = "topic_a"):
    return store.create_template(
        owner_user_id=owner,
        title="Calculus",
        description="",
        draft_path_id=path_id,
        draft_workspace_id="",
    )


def test_database_lives_in_the_system_root(mu_isolated_root, monkeypatch):
    from deeptutor.multi_user import paths
    from deeptutor.teaching import store as teaching_store

    monkeypatch.setattr(teaching_store, "DB_PATH_OVERRIDE", None)
    assert teaching_db_path() == paths.SYSTEM_ROOT / "teaching.sqlite3"
    assert TeachingStore().db_path.exists()
    assert "users" not in teaching_db_path().parts


def test_tests_never_touch_the_system_root(store, tmp_path):
    assert store.db_path == tmp_path / "teaching.sqlite3"


def test_journal_mode_is_delete(store):
    """The store must use DELETE journal mode, not WAL.

    The accepted D2 shared transitions require on-disk DELETE journals and
    FULL synchronous for multi-database atomicity.  This matches the
    existing KB and book registries.
    """
    conn = sqlite3.connect(store.db_path, timeout=5)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "delete", f"Expected DELETE journal mode, got {mode!r}"
        sync = conn.execute("PRAGMA synchronous").fetchone()[0]
        # 2 = FULL (SQLite default)
        assert sync == 2, f"Expected synchronous=FULL (2), got {sync!r}"
    finally:
        conn.close()


def test_create_find_and_list_templates(store):
    mine = _template(store)
    _template(store, owner="u_tom", path_id="topic_b")

    assert (mine.status, mine.current_revision) == ("draft", 0)
    assert store.find_by_draft("u_tina", "topic_a", "").id == mine.id
    assert [t.id for t in store.list_templates(owner_user_id="u_tina")] == [mine.id]
    assert len(store.list_templates()) == 2


def test_archiving_frees_the_draft_for_a_new_template(store):
    first = _template(store)
    store.update_template(first.id, status="archived")

    assert store.find_by_draft("u_tina", "topic_a", "") is None
    second = _template(store)
    assert second.id != first.id


def test_add_revision_publishes_and_rejects_gaps(store):
    template = _template(store)

    updated = store.add_revision(
        _revision(template.id, 1), aliases={"draft_kp": "kp_limits"}, title="Calculus I"
    )

    assert (updated.status, updated.current_revision, updated.title) == (
        "published",
        1,
        "Calculus I",
    )
    assert updated.kp_aliases == {"draft_kp": "kp_limits"}
    stored = store.get_revision(template.id, 1)
    assert stored.outline.modules[0].knowledge_points[0].id == "kp_limits"
    with pytest.raises(ValueError):
        store.add_revision(_revision(template.id, 3), aliases={}, title="Calculus I")
    store.add_revision(_revision(template.id, 2), aliases={}, title="Calculus I")
    assert [r.revision for r in store.revisions_between(template.id, after=0, upto=2)] == [1, 2]
    assert [r.revision for r in store.revisions_between(template.id, after=1, upto=2)] == [2]


def test_assign_revoke_and_reassign_keep_the_instance(store):
    template = _template(store)
    store.assign(template.id, ["u_sara", "u_sam"], assigned_by="u_tina")
    store.record_instance(
        template.id, "u_sara", instance_path_id="assigned_x", applied_revision=1
    )

    revoked = store.revoke(template.id, "u_sara")
    assert revoked.status == "revoked"
    assert revoked.revoked_at is not None
    assert store.count_active(template.id) == 1

    store.assign(template.id, ["u_sara"], assigned_by="u_tina")
    again = store.get_assignment(template.id, "u_sara")
    assert again.status == "active"
    assert again.revoked_at is None
    assert (again.instance_path_id, again.applied_revision) == ("assigned_x", 1)
    assert [a.template_id for a in store.assignments_for_student("u_sara")] == [template.id]
    assert store.has_assignments(template.id) is True


def test_revoking_an_unknown_assignment_returns_none(store):
    template = _template(store)
    assert store.revoke(template.id, "u_nobody") is None


def test_transfer_owner_and_student_cleanup(store):
    template = _template(store)
    store.assign(template.id, ["u_sara"], assigned_by="u_tina")

    assert store.transfer_owner("u_tina", "u_adam") == 1
    moved = store.get_template(template.id)
    assert moved.owner_user_id == "u_adam"
    assert moved.draft_path_id == ""

    assert store.revoke_all_for_student("u_sara") == 1
    assert store.get_assignment(template.id, "u_sara").status == "revoked"


def test_delete_cascades_to_revisions_and_assignments(store):
    template = _template(store)
    store.add_revision(_revision(template.id, 1), aliases={}, title="Calculus I")
    store.assign(template.id, ["u_sara"], assigned_by="u_tina")

    store.delete_template(template.id)

    assert store.get_template(template.id) is None
    assert store.get_revision(template.id, 1) is None
    assert store.list_assignments(template.id) == []
