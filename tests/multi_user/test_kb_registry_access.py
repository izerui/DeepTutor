"""resolve_kb understands kb:<id>; legacy admin refs follow the registry, not grants."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
import pytest

from deeptutor.knowledge import registry
from deeptutor.multi_user.knowledge_access import admin_kb_base_dir, current_kb_manager, resolve_kb


def test_kb_ref_reads_another_accounts_public_kb(as_user, make_kb):
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        make_kb(manager, "physics")
        record = registry.register_kb(
            base_dir=manager.base_dir,
            name="physics",
            kind="uploaded",
            created_by="t1",
            visibility="public",
        )
        own = resolve_kb(record.ref, require_write=True)
        assert own.read_only is False and own.assigned is False

    with as_user("s1", role="student"):
        resource = resolve_kb(record.ref)
        assert resource.id == record.ref
        assert resource.name == "physics"
        assert resource.base_dir == Path(record.base_dir)
        assert resource.read_only is True and resource.assigned is True
        with pytest.raises(HTTPException) as denied:
            resolve_kb(record.ref, require_write=True)
        assert denied.value.status_code == 403


def test_kb_ref_hides_other_accounts_private_kbs(as_user, make_kb):
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        make_kb(manager, "drafts")
        record = registry.register_kb(
            base_dir=manager.base_dir, name="drafts", kind="uploaded", created_by="t1"
        )
    with as_user("s1", role="student"):
        with pytest.raises(HTTPException) as hidden:
            resolve_kb(record.ref)
        assert hidden.value.status_code == 404
    with as_user("root", role="admin"):
        assert resolve_kb(record.ref, require_write=True).read_only is False


def test_kb_ref_to_a_vanished_kb_is_unavailable(as_user):
    with as_user("t1", role="teacher"):
        record = registry.register_kb(
            base_dir=current_kb_manager().base_dir, name="gone", kind="uploaded", created_by="t1"
        )
        with pytest.raises(HTTPException) as missing:
            resolve_kb(record.ref)
        assert missing.value.status_code == 404


def test_workspace_selection_limits_own_private_kbs_but_not_public_ones(as_user, make_kb):
    from deeptutor.services.workspace import ContentWorkspaceService
    from deeptutor.services.workspace.context import workspace_context

    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        make_kb(manager, "open")
        make_kb(manager, "closed")
        public = registry.register_kb(
            base_dir=manager.base_dir, name="open", kind="uploaded", created_by="t1", visibility="public"
        )
        private = registry.register_kb(
            base_dir=manager.base_dir, name="closed", kind="uploaded", created_by="t1"
        )
        narrow = ContentWorkspaceService().create_workspace(
            "Narrow", resources={"knowledge_bases": []}
        )["workspace_id"]
        with workspace_context(narrow):
            assert resolve_kb(public.ref).name == "open"
            with pytest.raises(HTTPException) as blocked:
                resolve_kb(private.ref)
            assert blocked.value.status_code == 403


def test_legacy_admin_refs_read_public_admin_kbs_without_a_grant(as_user):
    registry.register_kb(
        base_dir=admin_kb_base_dir(),
        name="calculus",
        kind="uploaded",
        created_by="local-admin",
        visibility="public",
    )
    registry.register_kb(
        base_dir=admin_kb_base_dir(), name="answer-keys", kind="uploaded", created_by="local-admin"
    )
    with as_user("s1", role="student"):
        resource = resolve_kb("admin:kb:calculus")
        assert resource.id == "admin:kb:calculus"
        assert resource.assigned is True and resource.read_only is True
        # Old conversations stored the bare name.
        assert resolve_kb("calculus").id == "admin:kb:calculus"
        with pytest.raises(HTTPException) as private:
            resolve_kb("admin:kb:answer-keys")
        assert private.value.status_code == 403
        with pytest.raises(HTTPException) as write:
            resolve_kb("admin:kb:calculus", require_write=True)
        assert write.value.status_code == 403


def test_grants_no_longer_open_admin_kbs(as_user, seed_user):
    from deeptutor.multi_user.grants import save_grant

    seed_user("boss", role="admin")
    alice = seed_user("alice")
    save_grant(alice["id"], {"knowledge_bases": [{"resource_id": "admin:kb:secret"}]})
    registry.register_kb(
        base_dir=admin_kb_base_dir(), name="secret", kind="uploaded", created_by="local-admin"
    )
    with as_user(alice["id"], username="alice"):
        with pytest.raises(HTTPException) as denied:
            resolve_kb("admin:kb:secret")
        assert denied.value.status_code == 403


def test_workspace_allowlist_does_not_block_legacy_public_admin_refs(as_user, make_kb):
    """D14: public KBs bypass workspace allowlists even for legacy refs."""
    from deeptutor.services.workspace import ContentWorkspaceService
    from deeptutor.services.workspace.context import workspace_context

    registry.register_kb(
        base_dir=admin_kb_base_dir(),
        name="public-math",
        kind="uploaded",
        created_by="local-admin",
        visibility="public",
    )
    registry.register_kb(
        base_dir=admin_kb_base_dir(),
        name="private-answers",
        kind="uploaded",
        created_by="local-admin",
    )
    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        make_kb(manager, "my-notes")
        registry.register_kb(
            base_dir=manager.base_dir, name="my-notes", kind="uploaded", created_by="t1"
        )
        narrow = ContentWorkspaceService().create_workspace(
            "Empty", resources={"knowledge_bases": []}
        )["workspace_id"]
        with workspace_context(narrow):
            # Public admin KB via legacy ref is readable read-only.
            resource = resolve_kb("admin:kb:public-math")
            assert resource.read_only is True and resource.assigned is True
            # Bare name also works.
            bare = resolve_kb("public-math")
            assert bare.id == "admin:kb:public-math" and bare.read_only is True
            # Private admin KB is still denied.
            with pytest.raises(HTTPException) as private:
                resolve_kb("admin:kb:private-answers")
            assert private.value.status_code == 403
            # Caller's own private KB outside allowlist is still denied.
            with pytest.raises(HTTPException) as own:
                resolve_kb("my-notes")
            assert own.value.status_code == 403


def test_resolve_registered_learning_source_bypass_requires_read(as_user, make_kb):
    """learning_source_kbs bypass must not grant writes."""
    from deeptutor.services.workspace import ContentWorkspaceService
    from deeptutor.services.workspace.context import workspace_context
    from deeptutor.services.workspace.knowledge import learning_source_kbs

    with as_user("t1", role="teacher"):
        manager = current_kb_manager()
        make_kb(manager, "mine")
        record = registry.register_kb(
            base_dir=manager.base_dir, name="mine", kind="uploaded", created_by="t1"
        )
        narrow = ContentWorkspaceService().create_workspace(
            "Empty", resources={"knowledge_bases": []}
        )["workspace_id"]
        token = learning_source_kbs.set(frozenset({record.ref}))
        try:
            with workspace_context(narrow):
                # Read via learning source bypass works.
                assert resolve_kb(record.ref).name == "mine"
                # Write is still blocked by workspace selection.
                with pytest.raises(HTTPException) as denied:
                    resolve_kb(record.ref, require_write=True)
                assert denied.value.status_code == 403
        finally:
            learning_source_kbs.reset(token)
