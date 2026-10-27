"""Admin-root KBs must always be recorded with created_by = LOCAL_ADMIN_ID.

Tests cover:
  1. sync_base_dir (listing path) records LOCAL_ADMIN_ID for admin root.
  2. register_new_kb (creation path) records LOCAL_ADMIN_ID for admin root.
  3. Normalization backfill fixes existing admin-root rows with concrete admin ids.
  4. Non-admin (teacher/student) KBs are untouched by normalization.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deeptutor.knowledge import registry
from deeptutor.multi_user.knowledge_access import current_kb_manager
from deeptutor.multi_user.models import LOCAL_ADMIN_ID


@pytest.fixture
def knowledge():
    from deeptutor.api.routers import knowledge as module

    return module


def _seed_kb(root: Path, name: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / name / "raw").mkdir(parents=True, exist_ok=True)
    (root / name / "raw" / "doc.md").write_text("hello", encoding="utf-8")
    config = root / "kb_config.json"
    data = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
    data.setdefault("knowledge_bases", {})[name] = {
        "path": name,
        "rag_provider": "llamaindex",
        "status": "ready",
    }
    config.write_text(json.dumps(data), encoding="utf-8")


# --- Bug 1: sync_base_dir (listing) ---

def test_list_visible_records_local_admin_id_for_admin_root(as_user, make_kb):
    """Admin listing should register KBs with LOCAL_ADMIN_ID, not the concrete admin id."""
    from deeptutor.multi_user.knowledge_access import _list_visible_knowledge_bases

    with as_user("admin42", role="admin"):
        manager = current_kb_manager()
        make_kb(manager, "physics")
        # _list_visible_knowledge_bases triggers sync_base_dir internally
        _list_visible_knowledge_bases()
        record = registry.find_by_location(manager.base_dir, "physics")
        # Before fix, this would be "admin42". After fix, must be LOCAL_ADMIN_ID.
        assert record is not None
        assert record.created_by == LOCAL_ADMIN_ID


# --- Bug 2: _register_created_kb (creation) ---

@pytest.mark.asyncio
async def test_register_created_kb_uses_local_admin_id(knowledge, as_user, make_kb, monkeypatch):
    """Creating a KB in admin root should set created_by = LOCAL_ADMIN_ID."""
    with as_user("admin42", role="admin"):
        manager = current_kb_manager()
        # Patch to skip actual file upload processing
        monkeypatch.setattr(
            "deeptutor.api.routers.knowledge.get_kb_manager",
            lambda: manager,
        )
        record = knowledge._register_created_kb("new_kb", "private")
        assert record.created_by == LOCAL_ADMIN_ID


# --- Bug 3: normalization backfill ---

def test_normalization_fixes_admin_root_rows_with_concrete_admin_id(
    mu_isolated_root, as_user, seed_user,
):
    """Rows in admin root with created_by = concrete admin id should be fixed to LOCAL_ADMIN_ID."""
    from deeptutor.knowledge.registry_backfill import normalize_admin_root_created_by
    from deeptutor.multi_user.paths import get_admin_path_service

    seed_user("boss", role="admin")
    admin_root = get_admin_path_service().get_knowledge_bases_root()
    _seed_kb(admin_root, "algebra")

    # Manually register with a concrete admin id (the bug)
    record = registry.register_kb(
        base_dir=admin_root, name="algebra", kind="uploaded", created_by="boss",
    )
    assert record.created_by == "boss"

    # Run normalization
    count = normalize_admin_root_created_by()
    assert count >= 1

    # Should now be LOCAL_ADMIN_ID
    fixed = registry.get_kb(record.ref)
    assert fixed.created_by == LOCAL_ADMIN_ID


def test_normalization_is_idempotent(mu_isolated_root, as_user, seed_user):
    """Running normalization twice should not fail or change already-correct rows."""
    from deeptutor.knowledge.registry_backfill import normalize_admin_root_created_by
    from deeptutor.multi_user.paths import get_admin_path_service

    seed_user("boss", role="admin")
    admin_root = get_admin_path_service().get_knowledge_bases_root()
    _seed_kb(admin_root, "calc")

    # Register correctly with LOCAL_ADMIN_ID
    record = registry.register_kb(
        base_dir=admin_root, name="calc", kind="uploaded", created_by=LOCAL_ADMIN_ID,
    )

    normalize_admin_root_created_by()
    second = normalize_admin_root_created_by()
    assert second == 0  # Nothing to fix on second run

    fixed = registry.get_kb(record.ref)
    assert fixed.created_by == LOCAL_ADMIN_ID


def test_normalization_leaves_teacher_kbs_untouched(mu_isolated_root, as_user, seed_user):
    """Teacher KBs must not be altered by admin-root normalization."""
    from deeptutor.knowledge.registry_backfill import normalize_admin_root_created_by
    from deeptutor.multi_user.paths import scope_for_user

    alice = seed_user("alice")
    alice_root = scope_for_user(alice["id"], is_admin=False).root / "knowledge_bases"
    _seed_kb(alice_root, "notes")

    record = registry.register_kb(
        base_dir=alice_root, name="notes", kind="uploaded", created_by=alice["id"],
    )
    normalize_admin_root_created_by()

    after = registry.get_kb(record.ref)
    assert after.created_by == alice["id"]
