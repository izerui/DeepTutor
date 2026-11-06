"""P4: administrators withdraw published content but do not edit it (spec 2026-10-29 D10)."""

from __future__ import annotations

from deeptutor.multi_user.context import get_current_user


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
