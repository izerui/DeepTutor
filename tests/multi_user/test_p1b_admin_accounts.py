"""P1b: the first administrator keeps the legacy root; later administrators get their own."""

from __future__ import annotations

import json

from deeptutor.multi_user import admin_accounts, paths
from deeptutor.multi_user.models import LOCAL_ADMIN_ID, CurrentUser
from deeptutor.multi_user.ownership import owner_ids


def _admin(uid: str) -> CurrentUser:
    return CurrentUser(uid, uid, "admin", paths.scope_for_user(uid, is_admin=True))


def test_the_only_admin_becomes_primary_and_is_recorded(seed_user):
    boss = seed_user("boss", role="admin")
    assert admin_accounts.primary_admin_id() == boss["id"]
    record = json.loads((paths.SYSTEM_ROOT / "auth" / "primary_admin.json").read_text())
    assert record == {"user_id": boss["id"]}
    assert paths.scope_for_user(boss["id"], is_admin=True) == paths.admin_scope()


def test_a_later_admin_gets_a_personal_root_with_shared_settings(seed_user):
    boss = seed_user("boss", role="admin")
    assert admin_accounts.primary_admin_id() == boss["id"]
    ann = seed_user("ann", role="admin")
    scope = paths.scope_for_user(ann["id"], is_admin=True)
    assert scope.kind == "admin" and scope.user_id == ann["id"]
    assert scope.root == (paths.USERS_ROOT / ann["id"]).resolve()
    service = paths.get_path_service_for_scope(scope)
    assert isinstance(service, admin_accounts.AdminAccountPathService)
    system = paths.get_admin_path_service()
    assert service.get_settings_dir() == system.get_settings_dir()
    assert service.get_settings_file("model_catalog") == system.get_settings_file("model_catalog")
    assert service.get_book_dir().is_relative_to(paths.USERS_ROOT / ann["id"])
    assert service.get_chat_history_db() != system.get_chat_history_db()
    assert service.get_memory_dir() != system.get_memory_dir()


def test_owner_ids_follow_the_storage_scope(seed_user):
    boss = seed_user("boss", role="admin")
    admin_accounts.primary_admin_id()
    ann = seed_user("ann", role="admin")
    assert owner_ids(_admin(boss["id"])) == {boss["id"], LOCAL_ADMIN_ID}
    assert owner_ids(_admin(ann["id"])) == {ann["id"]}


def test_single_machine_without_accounts_is_primary(mu_isolated_root):
    assert admin_accounts.primary_admin_id() is None
    assert admin_accounts.is_primary_admin_id("anyone")
    assert paths.scope_for_user("anyone", is_admin=True) == paths.admin_scope()


def test_several_admins_without_a_record_keep_the_legacy_shared_root(seed_user):
    boss = seed_user("boss", role="admin")
    ann = seed_user("ann", role="admin")
    assert admin_accounts.primary_admin_id() is None
    assert not (paths.SYSTEM_ROOT / "auth" / "primary_admin.json").exists()
    for uid in (boss["id"], ann["id"]):
        assert paths.scope_for_user(uid, is_admin=True) == paths.admin_scope()


def test_local_admin_identity_is_always_primary(seed_user):
    seed_user("boss", role="admin")
    admin_accounts.primary_admin_id()
    assert admin_accounts.is_primary_admin_id(LOCAL_ADMIN_ID)
    assert paths.local_admin_user().scope == paths.admin_scope()


def _two_admins(seed_user):
    boss = seed_user("boss", role="admin")
    admin_accounts.primary_admin_id()
    ann = seed_user("ann", role="admin")
    return _admin(boss["id"]), _admin(ann["id"])


def _as(user):
    from contextlib import contextmanager

    from deeptutor.multi_user.context import reset_current_user, set_current_user

    @contextmanager
    def scope():
        token = set_current_user(user)
        try:
            yield
        finally:
            reset_current_user(token)

    return scope()


def test_a_later_admins_books_are_their_own_and_hidden_from_the_first(seed_user):
    from deeptutor.book import registry as books
    from deeptutor.book.models import Book
    from deeptutor.book.storage import get_book_storage
    from deeptutor.multi_user import book_access

    boss, ann = _two_admins(seed_user)
    with _as(ann):
        get_book_storage().save_book(Book(id="bk_ann", title="Ann's notes"))
        [(book, resolved)] = [
            item for item in book_access.accessible_books() if item[0].id == "bk_ann"
        ]
        assert resolved.record.created_by == ann.id
        assert get_book_storage().path_service.get_book_dir().is_relative_to(
            paths.USERS_ROOT / ann.id
        )
    with _as(boss):
        assert all(item[0].id != "bk_ann" for item in book_access.accessible_books())
        assert not books.can_read_book(books.get_book("bk_ann"), boss)


def test_a_later_admins_knowledge_bases_are_labelled_as_their_own(seed_user, make_kb):
    from deeptutor.multi_user.knowledge_access import (
        current_kb_manager,
        list_visible_knowledge_bases,
    )

    _boss, ann = _two_admins(seed_user)
    with _as(ann):
        make_kb(current_kb_manager(), "ann-notes")
        [row] = [row for row in list_visible_knowledge_bases() if row["name"] == "ann-notes"]
        assert row["id"] == "user:kb:ann-notes"
        assert row["source"] == "user"


def test_partner_state_and_visualizers_follow_the_admin_split(seed_user):
    from deeptutor.services.partners.interaction import personal_actor_id
    from deeptutor.visualizers.store import VisualizerStore

    boss, ann = _two_admins(seed_user)
    assert personal_actor_id(boss) is None
    assert personal_actor_id(ann) == ann.id
    system_root = paths.get_admin_path_service().get_settings_dir().parent / "visualizers"
    with _as(ann):
        assert VisualizerStore().root == system_root.resolve()


def test_an_admin_with_a_personal_root_is_never_primary(mu_isolated_root):
    from deeptutor.multi_user.models import UserScope

    personal = CurrentUser(
        "u_other", "other", "admin",
        UserScope(kind="user", user_id="u_other", root=paths.USERS_ROOT / "u_other"),
    )
    assert not admin_accounts.is_primary_admin(personal)
    assert admin_accounts.storage_owner_id(personal) == "u_other"
    assert admin_accounts.is_primary_admin(paths.local_admin_user())
