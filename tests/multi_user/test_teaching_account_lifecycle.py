"""Teacher paths outlive the accounts that made or studied them."""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from deeptutor.api.routers import mastery_path
from deeptutor.api.routers.auth import _delete_admin_user
from deeptutor.book import registry as book_registry
from deeptutor.knowledge import registry as kb_registry
from deeptutor.learning.models import TopicSource, TopicSourceKind
from deeptutor.multi_user.context import get_current_user
from deeptutor.services.auth import TokenPayload
from deeptutor.teaching.access import studying_template
from deeptutor.teaching.models import PublishRequest
from deeptutor.teaching.service import TeachingService, TemplateConflict
from deeptutor.teaching.store import TeachingStore

TEMPLATE = "mastery_template"


def _as_admin(teaching_users) -> TokenPayload:
    admin = teaching_users["adam"]
    return TokenPayload(username="adam", role="admin", user_id=admin["id"])


def test_deleting_a_teacher_hands_their_paths_to_the_admin(
    sara_instance, teaching_users, as_account
):
    assert _delete_admin_user("tina", _as_admin(teaching_users)) == (200, "")

    template = TeachingStore().get_template(sara_instance.template_id)
    assert template.owner_user_id == teaching_users["adam"]["id"]
    assert template.draft_path_id == ""
    # Students keep learning.
    with as_account("sara"):
        topics = asyncio.run(mastery_path.list_topics())["topics"]
    assert [t["path_id"] for t in topics] == [sara_instance.path_id]
    assert topics[0]["origin"]["teacher_name"] == "adam"


def test_the_admin_manages_a_transferred_path_but_cannot_republish_it(
    sara_instance, teaching_users, as_account
):
    _delete_admin_user("tina", _as_admin(teaching_users))

    with as_account("adam"):
        service = TeachingService()
        rows = service.assign(sara_instance.template_id, [teaching_users["sam"]["id"]])
        assert sorted(r["username"] for r in rows) == ["sam", "sara"]
        with pytest.raises(TemplateConflict):
            asyncio.run(service.preview_publish(sara_instance.template_id))
        with pytest.raises(TemplateConflict):
            asyncio.run(
                service.publish(
                    sara_instance.template_id, PublishRequest(draft_revision=1)
                )
            )
        service.revoke(sara_instance.template_id, teaching_users["sam"]["id"])
        assert service.archive(sara_instance.template_id).status == "archived"
    store = TeachingStore()
    assert store.get_assignment(sara_instance.template_id, teaching_users["sam"]["id"]).status == (
        "revoked"
    )
    assert store.get_template(sara_instance.template_id).current_revision == 1


def test_a_failed_handover_never_breaks_the_deletion(
    sara_instance, teaching_users, monkeypatch, caplog
):
    def broken(*_args, **_kwargs):
        raise RuntimeError("teaching db locked")

    monkeypatch.setattr("deeptutor.teaching.service.handle_account_deleted", broken)

    assert _delete_admin_user("tina", _as_admin(teaching_users)) == (200, "")
    assert "Could not hand over teacher paths" in caplog.text


def test_a_failed_transfer_still_revokes_the_students_assignments(
    sara_instance, teaching_users, monkeypatch
):
    from deeptutor.teaching.service import handle_account_deleted

    def broken(self, *_args):
        raise RuntimeError("teaching db locked")

    monkeypatch.setattr(TeachingStore, "transfer_owner", broken)
    sara_id = teaching_users["sara"]["id"]

    handle_account_deleted(sara_id, successor_id=teaching_users["adam"]["id"])

    assert TeachingStore().get_assignment(sara_instance.template_id, sara_id).status == "revoked"


def test_deleting_a_student_revokes_their_assignments(sara_instance, teaching_users, as_account):
    sara_id = teaching_users["sara"]["id"]

    assert _delete_admin_user("sara", _as_admin(teaching_users)) == (200, "")

    assert TeachingStore().get_assignment(sara_instance.template_id, sara_id).status == "revoked"
    # The instance is no longer offered to that identity.
    with as_account("sara"):
        assert asyncio.run(mastery_path.list_topics())["topics"] == []
    # The teacher keeps owning the path; nothing else moved.
    template = TeachingStore().get_template(sara_instance.template_id)
    assert template.owner_user_id == teaching_users["tina"]["id"]
    assert template.draft_path_id


def test_transfer_refuses_inside_material_lock_instead_of_deadlocking(sara_instance, teaching_users):
    store = TeachingStore()
    with store.material_lock():
        with pytest.raises(RuntimeError):
            store.transfer_owner(teaching_users["tina"]["id"], teaching_users["adam"]["id"])
    assert store.get_template(sara_instance.template_id).owner_user_id == teaching_users["tina"]["id"]


def test_transfer_refuses_a_non_delete_journal(sara_instance, teaching_users):
    store = TeachingStore()
    conn = sqlite3.connect(store.db_path)
    try:
        assert conn.execute("PRAGMA journal_mode = WAL").fetchone()[0].lower() == "wal"
    finally:
        conn.close()
    with pytest.raises(RuntimeError, match="DELETE"):
        store.transfer_owner(teaching_users["tina"]["id"], teaching_users["adam"]["id"])
    assert store.get_template(sara_instance.template_id).owner_user_id == teaching_users["tina"]["id"]


# ---- reference ownership follows the template ------------------------------


def _kb_source(record) -> TopicSource:
    return TopicSource(
        id="s_kb", kind=TopicSourceKind.KNOWLEDGE_BASE, source_id=record.ref, label="KB", position=0
    )


def _file_source(record, rel_path: str) -> TopicSource:
    return TopicSource(
        id="s_file",
        kind=TopicSourceKind.FILE,
        source_id=rel_path,
        label="Doc",
        position=1,
        metadata={"kb_name": record.ref, "path": rel_path},
    )


def _book_source(book_id: str) -> TopicSource:
    return TopicSource(
        id=f"s_{book_id}", kind=TopicSourceKind.BOOK, source_id=book_id, label=book_id, position=2
    )


def _ready_kb(tmp_path, monkeypatch, owner_id: str, name: str):
    from deeptutor.knowledge.manager import KnowledgeBaseManager

    record = kb_registry.register_kb(
        base_dir=tmp_path / "kbs", name=name, kind="uploaded", created_by=owner_id,
        visibility="public",
    )
    raw = tmp_path / "kbs" / name / "raw"
    raw.mkdir(parents=True)
    (raw / "lesson.md").write_text("Lesson", encoding="utf-8")
    (raw / "other.md").write_text("Other", encoding="utf-8")
    monkeypatch.setattr(
        KnowledgeBaseManager, "get_kb_status", lambda self, _name: {"status": "ready"}
    )
    return record


def _template_refs(kb_id: str) -> dict[str, kb_registry.KbReference]:
    return {
        ref.referrer_id: ref
        for ref in kb_registry.list_references(kb_id)
        if ref.referrer_kind == TEMPLATE
    }


def _book_template_refs(book_id: str) -> dict[str, str]:
    return {
        ref.referrer_id: ref.created_by
        for ref in book_registry.list_book_references(book_id)
        if ref.referrer_kind == TEMPLATE
    }


@pytest.fixture
def cited_materials(
    tmp_path, monkeypatch, teaching_users, teacher_draft, publish_template, as_account
):
    """Tina's public KB and book, cited by her path (to Sara) and, optionally, Tom's."""

    def _make(*, foreign: bool):
        tina = teaching_users["tina"]["id"]
        kb = _ready_kb(tmp_path, monkeypatch, tina, "tina-notes")
        book = book_registry.register_book(
            base_dir=tmp_path / "books", dir_name="book_bk_tina", title="Tina's book",
            created_by=tina, visibility="public",
        )
        teacher_draft(
            sources=(_kb_source(kb), _file_source(kb, "lesson.md"), _book_source("bk_tina"))
        )
        own = publish_template(students=("sara",))
        assert own["warnings"] == []
        tom_template = ""
        if foreign:
            teacher_draft("tom", path_id="topic_tom", sources=(_kb_source(kb), _book_source("bk_tina")))
            other = publish_template("tom", path_id="topic_tom", students=("sam",))
            assert other["warnings"] == []
            tom_template = other["template"]["id"]
        return SimpleNamespace(
            kb=kb, book=book, template_id=own["template"]["id"], tom_template=tom_template
        )

    return _make


@pytest.mark.parametrize("foreign", [False, True])
def test_takeover_keeps_the_successors_own_references_and_foreign_protection(
    cited_materials, teaching_users, as_account, foreign
):
    materials = cited_materials(foreign=foreign)
    adam, tom = teaching_users["adam"]["id"], teaching_users["tom"]["id"]
    before = _template_refs(materials.kb.kb_id)

    assert _delete_admin_user("tina", _as_admin(teaching_users)) == (200, "")

    assert kb_registry.get_kb(materials.kb.kb_id).created_by == adam
    assert book_registry.get_book("bk_tina").created_by == adam
    after = _template_refs(materials.kb.kb_id)
    own = after[materials.template_id]
    # Identity and document scope are untouched; only the owner moved.
    assert (own.kb_id, own.doc_ids) == (
        before[materials.template_id].kb_id, before[materials.template_id].doc_ids
    )
    assert own.created_by == adam
    expected_books = {materials.template_id: adam}
    if foreign:
        assert after[materials.tom_template].created_by == tom
        expected_books[materials.tom_template] = tom
    assert _book_template_refs("bk_tina") == expected_books

    with as_account("adam"):
        if foreign:
            with pytest.raises(kb_registry.KbVisibilityError, match="other accounts"):
                kb_registry.set_visibility(materials.kb.kb_id, "private")
            with pytest.raises(book_registry.BookVisibilityError, match="other accounts"):
                book_registry.set_book_visibility("bk_tina", "private")
            assert _template_refs(materials.kb.kb_id) == after
        kb_registry.set_visibility(materials.kb.kb_id, "private", force=foreign)
        book_registry.set_book_visibility("bk_tina", "private", force=foreign)

    assert _template_refs(materials.kb.kb_id) == {materials.template_id: own}
    assert _book_template_refs("bk_tina") == {materials.template_id: adam}
    assert not kb_registry.list_unavailable_references(TEMPLATE, materials.template_id)
    assert not book_registry.list_unavailable_book_references(TEMPLATE, materials.template_id)
    if foreign:
        (kb_lost,) = kb_registry.list_unavailable_references(TEMPLATE, materials.tom_template)
        (book_lost,) = book_registry.list_unavailable_book_references(TEMPLATE, materials.tom_template)
        assert (kb_lost.reason, kb_lost.created_by) == ("made_private", tom)
        assert (book_lost.reason, book_lost.created_by) == ("made_private", tom)
    # Sara still reads the now-private material through the transferred path.
    with as_account("sara"), studying_template(materials.template_id):
        assert kb_registry.can_read(kb_registry.get_kb(materials.kb.kb_id), get_current_user())
        assert book_registry.can_read_book(book_registry.get_book("bk_tina"), get_current_user())
    with as_account("uma"), studying_template(materials.template_id):
        assert not kb_registry.can_read(kb_registry.get_kb(materials.kb.kb_id), get_current_user())


def test_a_failed_path_handover_leaves_template_references_with_the_path(
    cited_materials, teaching_users, monkeypatch, caplog
):
    materials = cited_materials(foreign=False)
    tina, adam = teaching_users["tina"]["id"], teaching_users["adam"]["id"]
    before = _template_refs(materials.kb.kb_id)

    def broken(self, *_args):
        raise RuntimeError("teaching db locked")

    monkeypatch.setattr(TeachingStore, "transfer_owner", broken)

    assert _delete_admin_user("tina", _as_admin(teaching_users)) == (200, "")

    assert "Could not transfer teacher paths" in caplog.text
    # B and D succeeded...
    assert kb_registry.get_kb(materials.kb.kb_id).created_by == adam
    assert book_registry.get_book("bk_tina").created_by == adam
    # ...but neither stage pre-transferred the path's references.
    assert TeachingStore().get_template(materials.template_id).owner_user_id == tina
    assert _template_refs(materials.kb.kb_id) == before
    assert before[materials.template_id].created_by == tina
    assert _book_template_refs("bk_tina") == {materials.template_id: tina}


@pytest.mark.parametrize("failure", ["statement", "commit"])
def test_a_failed_joint_transfer_rolls_back_paths_and_references_together(
    cited_materials, teaching_users, caplog, failure
):
    from deeptutor.teaching.service import handle_account_deleted

    materials = cited_materials(foreign=False)
    tina, adam = teaching_users["tina"]["id"], teaching_users["adam"]["id"]
    template_before = TeachingStore().get_template(materials.template_id)
    kb_before = _template_refs(materials.kb.kb_id)
    with book_registry._write() as conn:
        if failure == "statement":
            conn.execute(
                "CREATE TRIGGER fail_owner BEFORE UPDATE OF created_by ON book_references "
                "BEGIN SELECT RAISE(ABORT, 'injected reference failure'); END"
            )
        else:
            conn.execute("CREATE TABLE missing_parent (id INTEGER PRIMARY KEY)")
            conn.execute(
                "CREATE TABLE deferred_failure (id INTEGER REFERENCES missing_parent(id) "
                "DEFERRABLE INITIALLY DEFERRED)"
            )
            conn.execute(
                "CREATE TRIGGER fail_owner AFTER UPDATE OF created_by ON book_references "
                "BEGIN INSERT INTO deferred_failure VALUES (1); END"
            )

    handle_account_deleted(tina, successor_id=adam)

    assert "Could not transfer teacher paths" in caplog.text
    assert TeachingStore().get_template(materials.template_id) == template_before
    assert _template_refs(materials.kb.kb_id) == kb_before
    assert _book_template_refs("bk_tina") == {materials.template_id: tina}

    # Once the fault is gone, a retry hands everything over together.
    with book_registry._write() as conn:
        conn.execute("DROP TRIGGER fail_owner")
    assert TeachingStore().transfer_owner(tina, adam) == 1
    assert TeachingStore().get_template(materials.template_id).owner_user_id == adam
    assert _template_refs(materials.kb.kb_id)[materials.template_id].created_by == adam
    assert _book_template_refs("bk_tina") == {materials.template_id: adam}


def test_takeover_keeps_revocation_history_and_never_restores_access(
    tmp_path, monkeypatch, teaching_users, teaching_books, teacher_draft, publish_template,
    as_account,
):
    tom = teaching_users["tom"]["id"]
    kb = _ready_kb(tmp_path, monkeypatch, tom, "tom-notes")
    teacher_draft(sources=(_kb_source(kb), _book_source("bk_pub")))
    template_id = publish_template(students=("sara",))["template"]["id"]
    with as_account("adam"):
        kb_registry.set_visibility(kb.kb_id, "private", force=True)
        book_registry.set_book_visibility("bk_pub", "private", force=True)
    kb_lost = kb_registry.list_unavailable_references(TEMPLATE, template_id)
    book_lost = book_registry.list_unavailable_book_references(TEMPLATE, template_id)
    assert [row.reason for row in kb_lost] == ["made_private"]
    assert [row.reason for row in book_lost] == ["made_private"]

    assert _delete_admin_user("tina", _as_admin(teaching_users)) == (200, "")

    assert TeachingStore().get_template(template_id).owner_user_id == teaching_users["adam"]["id"]
    assert kb_registry.list_unavailable_references(TEMPLATE, template_id) == kb_lost
    assert book_registry.list_unavailable_book_references(TEMPLATE, template_id) == book_lost
    assert _template_refs(kb.kb_id) == {}
    assert _book_template_refs("bk_pub") == {}
    with as_account("sara"), studying_template(template_id):
        assert not kb_registry.can_read(kb_registry.get_kb(kb.kb_id), get_current_user())
        assert not book_registry.can_read_book(book_registry.get_book("bk_pub"), get_current_user())
