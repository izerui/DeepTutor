"""Publishing records explicit reference-only intent against pending migrations.

A legacy grant still waiting for its migration retry would make the teacher's
material public. An explicit "share by reference only" choice in the publish
request must win over that retry, for a KB the path cites directly and for a
book it cites directly; a preview (or no explicit choice) records nothing, so
the legacy promotion still happens.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from deeptutor.book import registry as books, registry_backfill as book_backfill
from deeptutor.book.models import Book
from deeptutor.book.storage import BookStorage
from deeptutor.knowledge import registry as kbs, registry_backfill as kb_backfill
from deeptutor.knowledge.manager import KnowledgeBaseManager
from deeptutor.learning.models import (
    KnowledgePoint,
    KnowledgeType,
    LearningModule,
    TopicMetadata,
    TopicSource,
    TopicSourceKind,
)
from deeptutor.learning.service import LearningService
from deeptutor.learning.storage import LearningStore
from deeptutor.multi_user import identity
from deeptutor.multi_user.grants import save_grant
from deeptutor.multi_user.models import LOCAL_ADMIN_ID
from deeptutor.multi_user.paths import get_admin_path_service, local_admin_user, user_context
from deeptutor.teaching.models import PublishRequest
from deeptutor.teaching.service import TeachingService
from deeptutor.teaching.store import TeachingStore

PATH_ID = "topic_waves"
BOOK_ID = "bk_waves"


@pytest.fixture
def legacy(seed_user, make_kb, quiet_change_model):
    """A local-admin KB "atlas" that Alice's old grant would make public."""
    seed_user("boss", role="admin")
    alice = seed_user("alice")
    save_grant(alice["id"], {"knowledge_bases": [{"resource_id": "admin:kb:atlas"}]})
    root = get_admin_path_service().get_knowledge_bases_root()
    root.mkdir(parents=True, exist_ok=True)
    kb = kbs.register_kb(base_dir=root, name="atlas", kind="uploaded", created_by=LOCAL_ADMIN_ID)
    make_kb(KnowledgeBaseManager(base_dir=str(root)), "atlas", text="Synthetic material")
    return kb


def _fail_kb_migration_once(monkeypatch, kb_id: str) -> None:
    put = kb_backfill._put_progress

    def rollback(conn, target, progress):
        put(conn, target, progress)
        raise sqlite3.OperationalError("synthetic publication rollback")

    with monkeypatch.context() as fault:
        fault.setattr(kb_backfill, "_put_progress", rollback)
        assert kb_backfill.backfill_registry()["promoted"] == 0
    assert kbs.get_kb(kb_id).visibility == "private"
    assert kbs.get_meta("visibility_intent_v1:" + kb_id) is None


def _fail_book_migration_once(monkeypatch, kb_ref: str) -> None:
    users = identity.load_users()
    users["alice"]["book_permission"] = {
        "create": True,
        "default": "none",
        "books": {BOOK_ID: "read"},
    }
    identity._write_users(users)
    shelf = BookStorage(path_service=get_admin_path_service())
    shelf.save_book(Book(id=BOOK_ID, title="Waves", knowledge_bases=[kb_ref]))
    put = book_backfill._put_progress

    def rollback(conn, key, value):
        put(conn, key, value)
        raise RuntimeError("synthetic book publication rollback")

    with monkeypatch.context() as fault:
        fault.setattr(book_backfill, "_put_progress", rollback)
        assert book_backfill.backfill_book_registry()["promoted"] == 0
    assert books.get_book(BOOK_ID).visibility == "private"
    assert books.get_meta("visibility_intent_v1:" + BOOK_ID) is None


def _draft(source: TopicSource) -> None:
    module = LearningModule(
        id="m0",
        name="Waves",
        order=0,
        objective="Describe waves",
        knowledge_points=[
            KnowledgePoint(id="m0_kp0", name="Wavelength", type=KnowledgeType.CONCEPT, module_id="m0")
        ],
    )
    LearningService(LearningStore()).create_topic(
        PATH_ID,
        name="Waves",
        modules=[module],
        metadata=TopicMetadata(path_id=PATH_ID, goal="Describe waves"),
        sources=[source],
    )


def _kb_source(kb) -> TopicSource:
    return TopicSource(
        id="s0", kind=TopicSourceKind.KNOWLEDGE_BASE, source_id=kb.ref, label="Atlas", position=0
    )


def _book_source() -> TopicSource:
    return TopicSource(
        id="s0", kind=TopicSourceKind.BOOK, source_id=BOOK_ID, label="Waves", position=0
    )


def _publish(*, publish: bool, kb_choices: dict, book_choices: dict) -> dict:
    service = TeachingService()
    template = service.register(PATH_ID, "")
    preview = asyncio.run(service.preview_publish(template.id))
    if not publish:
        return preview
    return asyncio.run(
        service.publish(
            template.id,
            PublishRequest(
                draft_revision=preview["draft_revision"],
                kb_choices=kb_choices,
                book_choices=book_choices,
            ),
        )
    )


@pytest.mark.parametrize("mode", ["explicit", "preview_only"])
def test_a_direct_kb_reference_only_choice_survives_the_migration_retry(
    legacy, monkeypatch, mode
):
    kb = legacy
    _fail_kb_migration_once(monkeypatch, kb.kb_id)
    explicit = mode == "explicit"

    with user_context(local_admin_user()):
        _draft(_kb_source(kb))
        result = _publish(
            publish=explicit, kb_choices={kb.kb_id: "reference_only"}, book_choices={}
        )

    intent = kbs.get_meta("visibility_intent_v1:" + kb.kb_id)
    refs = [(ref.referrer_kind, ref.referrer_id) for ref in kbs.list_references(kb.kb_id)]
    if explicit:
        assert result["warnings"] == []
        template_id = result["template"]["id"]
        assert intent == "private"
        assert refs == [("mastery_template", template_id)]
        (kb_ref,) = TeachingStore().get_revision(template_id, 1).sources.kb_refs
        assert (kb_ref.kb_id, kb_ref.access) == (kb.kb_id, "reference_only")
    else:
        # The preview offered the choice but recorded nothing.
        assert [item["kb_id"] for item in result["private_kbs"]] == [kb.kb_id]
        assert intent is None and refs == []
    assert kbs.get_kb(kb.kb_id).visibility == "private"

    retried = kb_backfill.backfill_registry()

    assert retried["promoted"] == int(not explicit)
    assert kbs.get_kb(kb.kb_id).visibility == ("private" if explicit else "public")


@pytest.mark.parametrize("mode", ["explicit", "preview_only"])
def test_a_direct_book_reference_only_choice_survives_the_migration_retry(
    legacy, monkeypatch, mode
):
    kb = legacy
    _fail_kb_migration_once(monkeypatch, kb.kb_id)
    _fail_book_migration_once(monkeypatch, kb.ref)
    explicit = mode == "explicit"

    with user_context(local_admin_user()):
        _draft(_book_source())
        result = _publish(
            publish=explicit,
            kb_choices={kb.kb_id: "reference_only"},
            book_choices={BOOK_ID: "reference_only"},
        )

    book_intent = books.get_meta("visibility_intent_v1:" + BOOK_ID)
    kb_intent = kbs.get_meta("visibility_intent_v1:" + kb.kb_id)
    if explicit:
        assert result["warnings"] == []
        template_id = result["template"]["id"]
        assert (book_intent, kb_intent) == ("private", "private")
        assert [
            (ref.referrer_kind, ref.referrer_id) for ref in books.list_book_references(BOOK_ID)
        ] == [("mastery_template", template_id)]
        (book_ref,) = TeachingStore().get_revision(template_id, 1).sources.book_refs
        assert (book_ref.book_id, book_ref.access) == (BOOK_ID, "reference_only")
    else:
        assert [item["book_id"] for item in result["private_books"]] == [BOOK_ID]
        assert (book_intent, kb_intent) == (None, None)
        assert books.list_book_references(BOOK_ID) == []
    assert books.get_book(BOOK_ID).visibility == "private"
    assert kbs.get_kb(kb.kb_id).visibility == "private"

    book_retry = book_backfill.backfill_book_registry()
    kb_retry = kb_backfill.backfill_registry()

    assert book_retry["promoted"] == int(not explicit)
    assert books.get_book(BOOK_ID).visibility == ("private" if explicit else "public")
    assert kb_retry["promoted"] == int(not explicit)
    assert kbs.get_kb(kb.kb_id).visibility == ("private" if explicit else "public")
