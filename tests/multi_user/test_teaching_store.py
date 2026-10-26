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
    # DB is created lazily on first write, not on construction.
    s = TeachingStore()
    assert not s.db_path.exists()
    _template(s)  # first write triggers schema creation
    assert s.db_path.exists()
    assert "users" not in teaching_db_path().parts


def test_tests_never_touch_the_system_root(store, tmp_path):
    assert store.db_path == tmp_path / "teaching.sqlite3"


def test_reads_on_missing_db_return_empty_without_creating_file(tmp_path):
    """Every read method must return empty/None when the DB does not exist,
    and must not create the file as a side effect."""
    db = tmp_path / "nonexistent" / "teaching.sqlite3"
    s = TeachingStore(db_path=db)

    assert not db.exists()
    assert s.get_template("tpl_missing") is None
    assert s.find_by_draft("u", "p", "w") is None
    assert s.list_templates() == []
    assert s.list_templates(owner_user_id="u") == []
    assert s.get_revision("tpl_missing", 1) is None
    assert s.revisions_between("tpl_missing", after=0, upto=5) == []
    assert s.get_assignment("tpl_missing", "u") is None
    assert s.list_assignments("tpl_missing") == []
    assert s.assignments_for_student("u") == []
    assert s.has_assignments("tpl_missing") is False
    assert s.count_active("tpl_missing") == 0

    # The DB file must still not exist after all those reads.
    assert not db.exists()
    assert not db.parent.exists()


def test_existing_db_reads_use_readonly_connection(store):
    """After a write creates the DB, reads open it in read-only mode.

    Verify by checking the DB file exists and that a read does not acquire
    a write lock (no journal file is left behind).
    """
    _template(store)
    assert store.db_path.exists()

    journal = store.db_path.parent / (store.db_path.name + "-journal")
    # A read should not create or leave a journal file.
    _ = store.list_templates()
    assert not journal.exists(), "Read path left a journal file; it should use mode=ro"


def test_journal_mode_is_delete(store):
    """The store must use DELETE journal mode, not WAL.

    The accepted D2 shared transitions require on-disk DELETE journals and
    FULL synchronous for multi-database atomicity.  This matches the
    existing KB and book registries.
    """
    _template(store)  # ensure DB exists
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


def test_an_archived_template_rejects_a_late_revision(store):
    template = _template(store)
    store.update_template(template.id, status="archived")

    with pytest.raises(ValueError):
        store.add_revision(_revision(template.id, 1), aliases={}, title="Calculus I")

    archived = store.get_template(template.id)
    assert (archived.status, archived.current_revision) == ("archived", 0)
    assert store.get_revision(template.id, 1) is None


def test_material_lock_excludes_writers_and_refuses_nested_writes(store):
    template = _template(store)

    with store.material_lock():
        assert store.get_template(template.id) is not None  # reads still work
        with pytest.raises(RuntimeError):
            store.update_template(template.id, status="archived")
        conn = sqlite3.connect(store.db_path, timeout=0, isolation_level=None)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                conn.execute("BEGIN IMMEDIATE")
        finally:
            conn.close()

    assert store.update_template(template.id, status="archived").status == "archived"
