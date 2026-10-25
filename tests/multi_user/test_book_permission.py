from __future__ import annotations

from deeptutor.multi_user.book_permission import (
    DEFAULT_BOOK_PERMISSION,
    BookPermission,
    normalize_book_permission,
    public_permission_dict,
)
from deeptutor.multi_user.identity import (
    list_user_info,
    load_users,
    remove_book_permission_overrides,
    set_book_permission,
)


def test_missing_and_malformed_permissions_fail_closed() -> None:
    assert normalize_book_permission(None) == DEFAULT_BOOK_PERMISSION
    assert normalize_book_permission({"default": "edit"}) == DEFAULT_BOOK_PERMISSION
    assert normalize_book_permission({"default": "read", "create": "true"}) == BookPermission(
        create=False,
        default="read",
    )


def test_legacy_delete_collapses_to_edit() -> None:
    permission = normalize_book_permission(
        {
            "mode": "list",
            "book_ids": ["bk_1", "bk_1"],
            "actions": {"create": True, "delete": True},
        }
    )
    assert public_permission_dict(permission) == {
        "create": True,
        "default": "none",
        "books": {"bk_1": "edit"},
    }


def test_identity_defaults_to_no_shared_books(mu_isolated_root, seed_user) -> None:
    seed_user("root", role="admin")
    user = seed_user("alice", role="user")

    info = next(item for item in list_user_info() if item["username"] == "alice")
    assert info["book_permission"] == {
        "create": True,
        "default": "none",
        "books": {},
    }
    assert load_users()["alice"]["book_permission"] == info["book_permission"]
    assert user["id"] == info["id"]


def test_permission_round_trip_and_deleted_book_cleanup(mu_isolated_root, seed_user) -> None:
    seed_user("root", role="admin")
    alice = seed_user("alice", role="user")
    bob = seed_user("bob", role="user")
    permission = BookPermission(
        create=False,
        default="none",
        books=(("bk_shared", "edit"), ("bk_other", "read")),
    )
    assert set_book_permission("alice", permission) is True
    assert (
        set_book_permission(
            "bob",
            BookPermission(books=(("bk_shared", "read"),)),
        )
        is True
    )

    affected = remove_book_permission_overrides("bk_shared")
    assert set(affected) == {alice["id"], bob["id"]}
    records = load_users()
    assert records["alice"]["book_permission"]["books"] == {"bk_other": "read"}
    assert records["bob"]["book_permission"]["books"] == {}
