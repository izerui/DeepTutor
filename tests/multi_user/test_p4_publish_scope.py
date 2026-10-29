"""P4: publish scopes — specific people, classes, everyone (spec 2026-10-29 §7)."""

from __future__ import annotations

import pytest

from deeptutor.classes.service import ClassService
from deeptutor.multi_user.context import get_current_user
from deeptutor.publishing.scope import BOOK, KNOWLEDGE_BASE, MASTERY_PATH
from deeptutor.publishing.service import PublishService
from deeptutor.publishing.scope import ScopeForbidden, ScopeNeedsConfirmation, ScopeNotFound


def _class_id(as_account) -> str:
    with as_account("tina"):
        [row] = ClassService().list_classes()
    return row["id"]


def _reads_book(as_account, name, record) -> bool:
    from deeptutor.book import registry

    with as_account(name):
        return registry.can_read_book(record, get_current_user())


def _reads_kb(as_account, name, record) -> bool:
    from deeptutor.knowledge import registry

    with as_account(name):
        return registry.can_read(registry.get_kb(record.kb_id), get_current_user())


def test_narrowing_a_public_book_needs_confirmation_then_applies(teaching_books, teaching_users, as_account):
    book = teaching_books.public
    assert _reads_book(as_account, "sara", book)
    sam = teaching_users["sam"]["id"]
    with as_account("tom"):
        with pytest.raises(ScopeNeedsConfirmation) as caught:
            PublishService().set_scope(BOOK, book.book_id, everyone=False, users=[sam], classes=[])
        assert caught.value.affected == 4  # adam, tina, sara, uma
        described = PublishService().set_scope(
            BOOK, book.book_id, everyone=False, users=[sam], classes=[], confirm=True
        )
    assert described["scope"]["users"] == [{"id": sam, "username": "sam"}]
    assert _reads_book(as_account, "sam", book)
    assert not _reads_book(as_account, "sara", book)
    assert _reads_book(as_account, "tom", book)  # the owner always reads


def test_a_class_scope_reaches_its_students_and_teachers_only(teaching_users, as_account, tmp_path):
    from deeptutor.knowledge import registry

    kb = registry.register_kb(
        base_dir=tmp_path / "kbs", name="notes", kind="uploaded",
        created_by=teaching_users["tina"]["id"], visibility="public",
    )
    class_id = _class_id(as_account)
    with as_account("tina"):
        described = PublishService().describe(KNOWLEDGE_BASE, kb.kb_id)
        assert described["suggested_classes"] == [class_id]
        PublishService().set_scope(
            KNOWLEDGE_BASE, kb.kb_id, everyone=False, users=[], classes=[class_id], confirm=True
        )
    for name, expected in (("sara", True), ("sam", True), ("tom", True), ("uma", False), ("adam", False)):
        assert _reads_kb(as_account, name, kb) is expected, name


def test_teachers_publish_only_to_classes_they_teach_and_only_their_own_items(
    teaching_books, teaching_users, as_account
):
    with as_account("adam"):
        other = ClassService().create("Other class")
        with pytest.raises(ScopeNotFound):
            PublishService().set_scope(BOOK, teaching_books.public.book_id, everyone=True, users=[], classes=[])
    with as_account("tom"):
        with pytest.raises(ScopeForbidden):
            PublishService().set_scope(
                BOOK, teaching_books.public.book_id, everyone=False, users=[], classes=[other["id"]]
            )
    with as_account("sara"):
        with pytest.raises(ScopeForbidden):
            PublishService().describe(BOOK, teaching_books.public.book_id)


def _assigned(template_id: str) -> set[str]:
    from deeptutor.teaching.store import TeachingStore

    return {a.student_user_id for a in TeachingStore().list_assignments(template_id) if a.status == "active"}


def test_a_path_published_to_a_class_follows_membership(
    teacher_draft, publish_template, teaching_users, as_account
):
    from deeptutor.teaching.service import TeachingService, TemplateConflict

    teacher_draft()
    template_id = publish_template()["template"]["id"]
    class_id = _class_id(as_account)
    sara, sam = teaching_users["sara"]["id"], teaching_users["sam"]["id"]
    with as_account("tina"):
        PublishService().set_scope(MASTERY_PATH, template_id, everyone=False, users=[], classes=[class_id])
    assert _assigned(template_id) == {sara, sam}

    with as_account("sam"):
        ClassService().leave(class_id)
    assert _assigned(template_id) == {sara}

    with as_account("tina"):
        with pytest.raises(TemplateConflict):
            TeachingService().revoke(template_id, sara)
    with as_account("adam"):
        ClassService().add_students(class_id, [sam])
    assert _assigned(template_id) == {sara, sam}


def test_narrowing_a_path_counts_the_students_who_lose_it(
    teacher_draft, publish_template, teaching_users, as_account
):
    teacher_draft()
    template_id = publish_template(students=("sara", "sam"))["template"]["id"]
    sara = teaching_users["sara"]["id"]
    with as_account("tina"):
        described = PublishService().describe(MASTERY_PATH, template_id)
        assert described["scope"]["managed"] is False and described["audience_size"] == 2
        with pytest.raises(ScopeNeedsConfirmation) as caught:
            PublishService().set_scope(MASTERY_PATH, template_id, everyone=False, users=[sara], classes=[])
        assert caught.value.affected == 1
        PublishService().set_scope(
            MASTERY_PATH, template_id, everyone=False, users=[sara], classes=[], confirm=True
        )
    assert _assigned(template_id) == {sara}


def test_an_admin_withdraws_a_published_kb_but_cannot_edit_it(teaching_users, as_account, tmp_path):
    """Spec 2026-10-29 D10 (P4): withdraw or delete, nothing more."""
    from deeptutor.knowledge import registry
    from deeptutor.publishing.admin import admin_retiring

    kb = registry.register_kb(
        base_dir=tmp_path / "kbs", name="notes", kind="uploaded",
        created_by=teaching_users["tina"]["id"], visibility="public",
    )
    with as_account("adam"):
        admin = get_current_user()
        assert registry.can_read(kb, admin) and not registry.can_write(kb, admin)
        with admin_retiring():
            assert registry.can_write(kb, admin)
        # Leaving the withdraw/delete route restores the narrow rule.
        assert not registry.can_write(kb, admin)
    registry.set_visibility(kb.kb_id, "private")
    with as_account("adam"), admin_retiring():
        assert not registry.can_write(registry.get_kb(kb.kb_id), get_current_user())


def test_retiring_routes_see_positional_and_keyword_arguments():
    import asyncio
    from types import SimpleNamespace

    from deeptutor.publishing.admin import retiring, retiring_route

    @retiring_route(lambda arguments: arguments["payload"].visibility == "private")
    async def route(name, payload):
        return retiring()

    private, public = SimpleNamespace(visibility="private"), SimpleNamespace(visibility="public")
    assert asyncio.run(route("kb", private)) is True
    assert asyncio.run(route("kb", payload=public)) is False
    assert retiring() is False
