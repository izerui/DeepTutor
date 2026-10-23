"""Retire legacy partner copies, or adopt missing-source copies in place.

Per-copy intent is durable before any registry or filesystem mutation. Completed
items stay completed even after detachment; interrupted items resume on startup.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import stat
from typing import Any
import uuid

from deeptutor.knowledge import registry
from deeptutor.knowledge.kb_types import is_connected_kb

logger = logging.getLogger(__name__)
PARTNER_MIGRATION_KEY = "partner_copies_migrated_at"


def migrate_partner_kb_copies(*, force: bool = False) -> dict[str, list[dict[str, str]]]:
    from deeptutor.services.partners.manager import PartnerManager

    report: dict[str, list[dict[str, str]]] = {"referenced": [], "adopted": []}
    completed = registry.get_meta(PARTNER_MIGRATION_KEY)
    if completed and not force:
        return report
    if force and completed:
        # A failed forced scan must also be retried by the next normal startup.
        registry.set_meta(PARTNER_MIGRATION_KEY, "")
    manager = PartnerManager()
    complete = True
    for partner_id in sorted(manager._discover_partner_ids()):
        try:
            _migrate_partner(manager, partner_id, report)
        except Exception:
            complete = False
            logger.exception("Partner %s: copy migration incomplete; will retry", partner_id)
    if complete:
        registry.set_meta(
            PARTNER_MIGRATION_KEY, datetime.now(timezone.utc).isoformat(timespec="seconds")
        )
    return report


def _directory(path: Path) -> bool:
    """Only ENOENT means absent; permission/I/O errors must remain retryable."""
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(mode):
        raise ValueError(f"Expected a real directory: {path}")
    return True


def _read_config(root: Path) -> dict[str, Any]:
    # Probe the directory too: an unreadable root is not a missing catalog.
    if not _directory(root):
        return {"knowledge_bases": {}}
    path = root / "kb_config.json"
    try:
        if stat.S_ISLNK(path.lstat().st_mode):
            raise ValueError(f"Symlink catalog is not migratable: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"knowledge_bases": {}}
    if not isinstance(data, dict) or not isinstance(data.get("knowledge_bases"), dict):
        raise ValueError(f"Invalid knowledge catalog: {path}")
    for name, entry in data["knowledge_bases"].items():
        _validate_name(name)
        if not isinstance(entry, dict):
            raise ValueError(f"Invalid knowledge entry: {name}")
    return data


def _validate_name(name: str) -> None:
    if not name or name.startswith((".", "_")) or "/" in name or "\\" in name:
        raise ValueError(f"Unsafe knowledge base name: {name!r}")


def identity_snapshot() -> dict[str, dict[str, Any]]:
    """Validate the authoritative store without invoking mutating normalization."""
    from deeptutor.multi_user import identity
    from deeptutor.multi_user.models import normalize_role

    for path in (identity.USERS_FILE, identity.LEGACY_USERS_FILE):
        try:
            users = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        if not isinstance(users, dict):
            raise ValueError("Account catalog unavailable during copy migration")
        snapshot = {}
        for index, (username, value) in enumerate(users.items()):
            # Hash-only and password_hash records are supported legacy formats.
            # Missing IDs are not invented here: those rows cannot match an
            # existing partner owner, and startup can normalize them afterward.
            if isinstance(value, str) and value:
                value = {"hash": value}
            if not isinstance(value, dict) or not isinstance(
                value.get("hash") or value.get("password_hash"), str
            ) or not (value.get("hash") or value.get("password_hash")):
                raise ValueError(f"Invalid account identity: {username}")
            user_id = value.get("id") or ""
            if not isinstance(user_id, str) or (user_id and user_id in snapshot):
                raise ValueError(f"Invalid or duplicate account ID: {username}")
            role = normalize_role(str(value.get("role") or ""), "admin" if index == 0 else "user")
            if user_id:
                snapshot[user_id] = {"username": username, "role": role}
        return snapshot
    return {}


def _owner_kb_root(owner_id: str) -> tuple[Path, str]:
    from deeptutor.multi_user.models import LOCAL_ADMIN_ID
    from deeptutor.multi_user.paths import get_admin_path_service, scope_for_user

    owner_id = owner_id or LOCAL_ADMIN_ID
    if owner_id == LOCAL_ADMIN_ID:
        return get_admin_path_service().get_knowledge_bases_root(), owner_id
    found = identity_snapshot().get(owner_id)
    if found is None or found["role"] == "admin":
        return get_admin_path_service().get_knowledge_bases_root(), owner_id
    return scope_for_user(owner_id, is_admin=False).root / "knowledge_bases", owner_id


def _migrate_partner(manager: Any, partner_id: str, report: dict) -> None:
    from deeptutor.services.partners.scope import is_partner_user_id, partner_user_id

    config = manager.load_config(partner_id)
    if config is None:
        raise ValueError(f"Partner configuration unavailable: {partner_id}")
    if config.workspace_id:
        return
    copy_root = manager._partner_dir(partner_id) / "workspace" / "knowledge_bases"
    catalog = _read_config(copy_root)
    progress_key = f"partner_copy_progress:{partner_id}"
    progress = json.loads(registry.get_meta(progress_key) or "{}")
    names = set(catalog["knowledge_bases"]) | set(progress)
    if _directory(copy_root):
        for child in copy_root.iterdir():
            if child.name.startswith((".", "_")) or child.name == "kb_config.json":
                continue
            if child.is_symlink():
                raise ValueError(f"Symlink copy is not migratable: {child}")
            if child.is_dir():
                names.add(child.name)
    for name in sorted(names):
        _validate_name(name)
        state = progress.get(name)
        if state and state.get("done"):
            continue
        existing = registry.find_by_location(copy_root, name)
        synthetic_owner = partner_user_id(partner_id)
        # Human-owned registrations are independent libraries; synthetic scope
        # discovery is only preregistration, not proof of completed adoption.
        if state is None and existing is not None and existing.created_by != synthetic_owner:
            if is_partner_user_id(existing.created_by):
                raise ValueError("Copy belongs to a different synthetic owner")
            continue
        source_root, owner_id = _owner_kb_root(config.owner_id)
        source_catalog = _read_config(source_root)
        source_entry = source_catalog["knowledge_bases"].get(name)
        source_alive = _directory(source_root / name) or is_connected_kb(source_entry)
        if state is None:
            adopted = not source_alive
            retired = copy_root / ".retired" / name
            if retired.exists() or retired.is_symlink():
                retired = retired.with_name(f"{name}-{uuid.uuid4().hex}")
            state = {
                "adopted": adopted,
                "owner_id": owner_id,
                "base_dir": str(copy_root if adopted else source_root),
                "retired": retired.name,
                "done": False,
            }
            if existing is not None:
                state["legacy_id"] = existing.kb_id
                if not adopted:
                    # Keep the old ID and its consumers bound to the actual copy.
                    state["retired"] = f"{uuid.uuid4().hex}/{name}"
            progress[name] = state
            registry.set_meta(progress_key, json.dumps(progress))
        if state["owner_id"] != owner_id:
            raise ValueError("Partner owner changed during copy migration")
        adopted = state["adopted"]
        target_root = Path(state["base_dir"])
        if not adopted and (target_root != source_root or not source_alive):
            raise ValueError("Original source unavailable during copy migration")
        entry = _ensure_entry(target_root, name)
        # Use the strictly read entry, avoiding manager reconciliation of legacy
        # catalogs while their migration is in progress.
        record = registry.register_kb(
            base_dir=target_root, name=name, kind=registry.kind_of(entry), created_by=owner_id
        )
        if adopted and state.get("legacy_id"):
            record = registry.adopt_partner_copy(
                state["legacy_id"], partner_id=partner_id, owner_id=owner_id
            )
        if adopted and (record.created_by != owner_id or record.visibility != "private"):
            raise ValueError("Copy ownership changed during migration")

        def validate_target(target: registry.KbRecord) -> None:
            if registry.is_connector(target) and target.created_by != owner_id:
                raise registry.KbReferenceError("Connector belongs to a different creator")

        registry.add_reference(
            record.kb_id, referrer_kind="partner", referrer_id=partner_id,
            created_by=owner_id, validate_target=validate_target,
        )
        row = {"partner_id": partner_id, "name": name, "kb_id": record.kb_id}
        if adopted:
            registry.add_migration_item(
                "partner_copy_adopted", f"{partner_id}:{name}", {**row, "owner_id": owner_id}
            )
        else:
            if state.get("legacy_id"):
                _directory(copy_root / ".retired")
                registry.adopt_partner_copy(
                    state["legacy_id"], partner_id=partner_id, owner_id=owner_id
                )
                retired_root = copy_root / ".retired" / Path(state["retired"]).parent
                # Preserve connector metadata as well as folder-only copies.
                from deeptutor.services.file_io import atomic_write_json

                retired_catalog = _read_config(retired_root)
                if name not in retired_catalog["knowledge_bases"]:
                    retired_catalog["knowledge_bases"][name] = catalog["knowledge_bases"].get(
                        name, {"path": name, "rag_provider": "llamaindex", "status": "ready"}
                    )
                    atomic_write_json(retired_root / "kb_config.json", retired_catalog)
                _retire_copy(copy_root, name, state["retired"])
                registry.relocate_kb(state["legacy_id"], retired_root)
                registry.remove_reference(state["legacy_id"], "partner", partner_id)
            else:
                _retire_copy(copy_root, name, state["retired"])
        state["done"] = True
        registry.set_meta(progress_key, json.dumps(progress))
        report["adopted" if adopted else "referenced"].append(row)


def _ensure_entry(root: Path, name: str) -> dict[str, Any]:
    from deeptutor.services.file_io import atomic_write_json

    catalog = _read_config(root)
    entry = catalog["knowledge_bases"].get(name)
    if not _directory(root / name) and not is_connected_kb(entry):
        raise ValueError(f"Knowledge base unavailable: {root / name}")
    if entry is None:
        # Folder-only copies must be resolvable through the ordinary manager.
        entry = {"path": name, "rag_provider": "llamaindex", "status": "ready"}
        catalog["knowledge_bases"][name] = entry
        atomic_write_json(root / "kb_config.json", catalog)
    return entry


def _retire_copy(copy_root: Path, name: str, retired_name: str) -> None:
    from deeptutor.services.file_io import atomic_write_json

    catalog = _read_config(copy_root)
    folder = copy_root / name
    retired_root = copy_root / ".retired"
    if not _directory(retired_root):
        retired_root.mkdir()
    retired = retired_root / retired_name
    retired.parent.mkdir(parents=True, exist_ok=True)
    if _directory(folder):
        if retired.exists() or retired.is_symlink():
            raise ValueError(f"Retirement target already exists: {retired}")
        # Same-directory rename never falls back to copying/removing user files.
        folder.rename(retired)
    if name in catalog["knowledge_bases"]:
        del catalog["knowledge_bases"][name]
        atomic_write_json(copy_root / "kb_config.json", catalog)
