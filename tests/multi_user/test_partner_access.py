"""Partner assignment / visibility for non-admin users."""

from __future__ import annotations

from fastapi import HTTPException
import pytest

from deeptutor.multi_user import partner_access
from deeptutor.multi_user.grants import empty_grant, normalize_grant


class _FakeManager:
    def __init__(self, partners: list[dict]) -> None:
        self._partners = partners

    def list_partners(self) -> list[dict]:
        return self._partners

    def owner_id(self, partner_id: str) -> str:
        for partner in self._partners:
            if partner.get("partner_id") == partner_id:
                return str(partner.get("owner_id") or "")
        return ""


def _patch_manager(monkeypatch, partners: list[dict]) -> None:
    import deeptutor.services.partners as pkg

    monkeypatch.setattr(pkg, "get_partner_manager", lambda: _FakeManager(partners))


# ── Grant shape ───────────────────────────────────────────────


def test_empty_grant_has_partners_list():
    assert empty_grant("u")["partners"] == []


def test_normalize_grant_round_trips_partners():
    grant = normalize_grant(
        "u_alice",
        {"partners": [{"partner_id": "p1"}, {"id": "p2"}, "not-a-dict", {}]},
    )
    # Non-dict entries are dropped; dict entries (even empty) survive.
    assert grant["partners"] == [{"partner_id": "p1"}, {"id": "p2"}, {}]


def test_normalize_grant_missing_partners_defaults_empty():
    assert normalize_grant("u_alice", {"skills": []})["partners"] == []


# ── assigned_partner_ids ──────────────────────────────────────
# Spec 2026-10-29 D5: lending is retired — a partner belongs to its creator.


def _lend(uid: str, partner_ids: list[str]) -> None:
    from deeptutor.multi_user.grants import load_grant, save_grant
    from deeptutor.multi_user.identity import save_user

    record = save_user(uid, "hash", role="student")
    grant = load_grant(record["id"])
    grant["partners"] = [{"partner_id": pid} for pid in partner_ids]
    save_grant(record["id"], grant)


def test_assigned_partner_ids_is_always_empty(as_user, seed_user):
    seed_user("boss", role="admin")
    _lend("u_alice", ["p1", "p2"])
    with as_user("u_alice", role="student"):
        assert partner_access.assigned_partner_ids() == set()


# ── assert_partner_allowed ────────────────────────────────────


def test_admin_may_use_admin_managed_partners(as_user):
    with as_user("u_admin", role="admin"):
        partner_access.assert_partner_allowed("anything")  # ownerless → admin-managed


def test_a_lent_partner_is_not_usable(as_user, monkeypatch):
    _patch_manager(monkeypatch, [{"partner_id": "p1", "name": "P1", "owner_id": "u_bob"}])
    with as_user("u_alice", role="student"):
        with pytest.raises(HTTPException) as exc:
            partner_access.assert_partner_allowed("p1")
        assert exc.value.status_code == 403


# ── visible_partner_cards ─────────────────────────────────────


def test_admin_sees_all_partners_identity_only(as_user, monkeypatch):
    _patch_manager(
        monkeypatch,
        [
            {"partner_id": "p1", "name": "P1", "emoji": "🤖", "channels": ["telegram"]},
            {"partner_id": "p2", "name": "P2", "channels": [], "llm_selection": {"x": "y"}},
        ],
    )
    with as_user("u_admin", role="admin"):
        cards = partner_access.visible_partner_cards()
    assert {c["partner_id"] for c in cards} == {"p1", "p2"}
    # Identity only — channel wiring / model selection must not leak to a card.
    assert all("channels" not in c and "llm_selection" not in c for c in cards)


def test_non_admin_sees_no_partners_they_do_not_own(as_user, monkeypatch):
    _patch_manager(monkeypatch, [{"partner_id": "p1", "name": "P1"}, {"partner_id": "p2", "name": "P2"}])
    with as_user("u_alice", role="student"):
        assert partner_access.visible_partner_cards() == []


# ── ownership ─────────────────────────────────────────────────


def test_owner_may_manage_and_use_their_own_partner(as_user, monkeypatch):
    _patch_manager(monkeypatch, [{"partner_id": "p1", "name": "P1", "owner_id": "u_alice"}])
    with as_user("u_alice", role="student"):
        assert partner_access.can_manage_partner("p1")
        assert partner_access.can_use_partner("p1")
        partner_access.assert_partner_manageable("p1")  # no raise
        partner_access.assert_partner_allowed("p1")  # no raise


def test_a_partner_someone_else_owns_is_not_manageable(as_user, monkeypatch):
    _patch_manager(monkeypatch, [{"partner_id": "p1", "name": "P1", "owner_id": "u_bob"}])
    with as_user("u_alice", role="student"):
        assert not partner_access.can_manage_partner("p1")
        with pytest.raises(HTTPException) as exc:
            partner_access.assert_partner_manageable("p1")
        assert exc.value.status_code == 403


def test_an_admin_cannot_manage_another_accounts_partner(as_user, monkeypatch):
    # Spec 2026-10-29 D2: someone's own partner is their personal data.
    _patch_manager(monkeypatch, [{"partner_id": "p1", "name": "P1", "owner_id": "u_bob"}])
    with as_user("u_admin", role="admin"):
        assert not partner_access.can_manage_partner("p1")
        assert not partner_access.can_use_partner("p1")


def test_ownerless_partners_stay_admin_managed(as_user, monkeypatch):
    # Partners that predate ownership carry no owner_id; nobody inherits them
    # just by having an empty id themselves.
    _patch_manager(monkeypatch, [{"partner_id": "legacy", "name": "Legacy", "owner_id": ""}])
    with as_user("", role="student"):
        assert not partner_access.can_manage_partner("legacy")
    with as_user("u_admin", role="admin"):
        assert partner_access.can_manage_partner("legacy")


def test_visible_partners_shows_only_the_callers_own(as_user, monkeypatch):
    _patch_manager(
        monkeypatch,
        [
            {"partner_id": "mine", "name": "Mine", "owner_id": "u_alice", "channels": ["qq"]},
            {"partner_id": "lent", "name": "Lent", "owner_id": "u_bob", "channels": ["telegram"]},
        ],
    )
    with as_user("u_alice", role="student"):
        visible = {p["partner_id"]: p for p in partner_access.visible_partners()}

    assert set(visible) == {"mine"}
    assert visible["mine"]["can_manage"] is True
    assert visible["mine"]["channels"] == ["qq"]


# ── assignable pool (admin side) ──────────────────────────────


def test_admin_partner_summary_is_identity_only(monkeypatch):
    from deeptutor.api.routers import multi_user as router

    _patch_manager(
        monkeypatch,
        [
            {
                "partner_id": "p1",
                "name": "Tutor",
                "description": "math",
                "emoji": "🤖",
                "channels": ["telegram"],
                "llm_selection": {"x": "y"},
            }
        ],
    )
    summary = router._admin_partner_summary()
    assert summary == [{"partner_id": "p1", "name": "Tutor", "description": "math", "emoji": "🤖"}]
