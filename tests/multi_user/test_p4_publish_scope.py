"""P4: publish scopes — content from before the market that reached everyone
or named people (spec 2026-10-29 §7; nobody is named any more, spec
2026-11-05-remove-teacher-management)."""

from __future__ import annotations

import pytest

from deeptutor.multi_user.context import get_current_user
from deeptutor.publishing import scope as scopes
from deeptutor.publishing.scope import BOOK, MASTERY_PATH, ScopeNeedsConfirmation, ScopeNotFound
from deeptutor.publishing.service import PublishService


def _reads_book(as_account, name, record) -> bool:
    from deeptutor.book import registry

    with as_account(name):
        return registry.can_read_book(record, get_current_user())


def _reads_kb(as_account, name, record) -> bool:
    from deeptutor.knowledge import registry

    with as_account(name):
        return registry.can_read(registry.get_kb(record.kb_id), get_current_user())


def test_narrowing_a_public_book_needs_confirmation_then_applies(teaching_books, as_account):
    book = teaching_books.public
    assert _reads_book(as_account, "sara", book)
    with as_account("tom"):
        with pytest.raises(ScopeNeedsConfirmation) as caught:
            PublishService().set_scope(BOOK, book.book_id, everyone=False)
        assert caught.value.affected == 5  # adam, tina, sara, sam, uma
        described = PublishService().set_scope(BOOK, book.book_id, everyone=False, confirm=True)
    assert described["scope"]["users"] == [] and described["scope"]["everyone"] is False
    assert not _reads_book(as_account, "sara", book)  # only who joins it now
    assert _reads_book(as_account, "tom", book)  # the owner always reads


def test_only_the_publisher_sees_or_changes_who_receives_an_item(teaching_books, as_account):
    with as_account("adam"):
        with pytest.raises(ScopeNotFound):
            PublishService().set_scope(BOOK, teaching_books.public.book_id, everyone=True)
    with as_account("sara"):
        with pytest.raises(ScopeNotFound):
            PublishService().describe(BOOK, teaching_books.public.book_id)


def _assigned(template_id: str) -> set[str]:
    from deeptutor.teaching.store import TeachingStore

    return {a.student_user_id for a in TeachingStore().list_assignments(template_id) if a.status == "active"}


def test_narrowing_an_older_everyone_path_counts_the_learners_who_lose_it(
    teacher_draft, publish_template, teaching_users, as_account
):
    teacher_draft()
    template_id = publish_template()["template"]["id"]
    # Older data: the path reached everyone; that is every learner but its author (spec 2026-11-05).
    scopes._store_scope(MASTERY_PATH, template_id, scopes.Scope(everyone=True), actor_id=teaching_users["tina"]["id"])
    scopes.reconcile_template(template_id)
    learners = {teaching_users[name]["id"] for name in ("tom", "sara", "sam", "uma")}
    assert _assigned(template_id) == learners
    with as_account("tina"):
        described = PublishService().describe(MASTERY_PATH, template_id)
        assert described["scope"]["everyone"] is True and described["audience_size"] == 4
        with pytest.raises(ScopeNeedsConfirmation) as caught:
            PublishService().set_scope(MASTERY_PATH, template_id, everyone=False)
        assert caught.value.affected == 4
        PublishService().set_scope(MASTERY_PATH, template_id, everyone=False, confirm=True)
    assert _assigned(template_id) == set()


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
