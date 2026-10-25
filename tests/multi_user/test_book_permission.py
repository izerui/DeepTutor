from __future__ import annotations

import json

import pytest

from deeptutor.multi_user.book_permission import (
    DEFAULT_BOOK_PERMISSION,
    BookPermission,
    legacy_book_shares,
    normalize_book_permission,
    public_permission_dict,
)
from deeptutor.multi_user import identity


def test_create_only_normalization():
    assert normalize_book_permission(None) == DEFAULT_BOOK_PERMISSION
    assert normalize_book_permission({"default": "edit"}) == BookPermission()
    assert normalize_book_permission({"create": "true"}) == BookPermission(create=False)
    assert normalize_book_permission({"mode": "list", "actions": {"create": False}}) == BookPermission(create=False)
    assert public_permission_dict(BookPermission(create=False)) == {"create": False}


def test_retired_levels_are_only_migration_evidence():
    assert legacy_book_shares({
        "mode": "list", "book_ids": ["bk_1", "bk_1"], "actions": {"delete": True},
    }).books == (("bk_1", "edit"),)
    shares = legacy_book_shares({"default": "read", "books": {"bk_2": "none"}})
    assert shares.level_for("bk_9") == "read"
    assert shares.level_for("bk_2") == "none"
    assert legacy_book_shares({"default": "edit"}).default == "none"
    assert legacy_book_shares({"mode": "all"}).default == "read"
    assert legacy_book_shares("garbage").level_for("bk_1") == "none"


def test_identity_create_round_trip(mu_isolated_root, seed_user):
    seed_user("root", role="admin")
    seed_user("alice")
    info = next(row for row in identity.list_user_info() if row["username"] == "alice")
    assert info["book_permission"] == {"create": True}
    assert identity.set_book_permission("alice", BookPermission(create=False))
    assert identity.load_users()["alice"]["book_permission"] == {"create": False}


@pytest.mark.parametrize("legacy", [
    {"default": "read", "books": {"bk_old": "edit"}},
    {"mode": "all", "actions": {"create": False, "delete": True}},
    {"mode": "list", "book_ids": ["bk_old"], "actions": {"edit": True}},
])
def test_identity_reads_and_policy_saves_retain_legacy_evidence(mu_isolated_root, seed_user, legacy):
    seed_user("root", role="admin")
    seed_user("alice")
    users = identity.load_users()
    users["alice"]["book_permission"] = legacy
    identity._write_users(users)
    expected = legacy_book_shares(legacy)
    identity.get_user("alice")
    identity.list_user_info()
    identity.save_user("alice", "new-hash")
    assert identity.set_book_permission("alice", BookPermission(create=False))
    stored = json.loads(identity.USERS_FILE.read_text())["alice"]["book_permission"]
    assert legacy_book_shares(stored) == expected
    for key, value in legacy.items():
        assert stored[key] == value
    info = next(row for row in identity.list_user_info() if row["username"] == "alice")
    assert info["book_permission"] == {"create": False}
