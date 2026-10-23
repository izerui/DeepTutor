"""Physical source scope and authoritative owners for registry-backed jobs."""

from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING, cast

from deeptutor.knowledge import registry

if TYPE_CHECKING:
    from deeptutor.multi_user.models import CurrentUser


def owner_user(record: registry.KbRecord) -> CurrentUser:
    from deeptutor.multi_user.identity import get_user_by_id
    from deeptutor.multi_user.models import LOCAL_ADMIN_ID, CurrentUser, Role, normalize_role
    from deeptutor.multi_user.paths import local_admin_user, scope_for_user
    from deeptutor.services.partners.scope import (
        PARTNER_USER_PREFIX,
        is_partner_user_id,
        partner_user,
    )

    if record.created_by == LOCAL_ADMIN_ID:
        return local_admin_user()
    if is_partner_user_id(record.created_by):
        return partner_user(record.created_by[len(PARTNER_USER_PREFIX):])
    found = get_user_by_id(record.created_by)
    role = cast(Role, normalize_role(str(found[1].get("role") or "user")) if found else "user")
    # Authenticated admins share storage, but route job keys use their real ID.
    # Neither a storage location nor a missing account grants administrator rights.
    return CurrentUser(
        id=record.created_by,
        username=found[0] if found else record.created_by,
        role=role,
        scope=scope_for_user(record.created_by, is_admin=role == "admin"),
    )


def is_retired_source(base_dir: str | Path) -> bool:
    from deeptutor.multi_user.paths import get_admin_path_service

    partner_root = get_admin_path_service().workspace_root / "partners"
    location = Path(base_dir).resolve()
    if not location.is_relative_to(partner_root):
        return False
    relative = location.relative_to(partner_root)
    return relative.parts[1:4] == ("workspace", "knowledge_bases", ".retired")


def archived_source_roots() -> set[str]:
    """Read physical archive flags, including catalogs of former owners.

    Transfers do not move files or catalogs. Read the retained account catalogs
    without installing their identities, creating directories, or probing outputs.
    """
    from deeptutor.multi_user.paths import USERS_ROOT, get_admin_path_service
    from deeptutor.services.path_service import PathService
    from deeptutor.services.workspace.models import WorkspaceError

    admin = get_admin_path_service()
    account_roots = {admin.workspace_root}
    account_roots.update(path for path in USERS_ROOT.glob("*") if path.is_dir())
    account_roots.update(
        path / "workspace"
        for path in (admin.workspace_root / "partners").glob("*")
        if (path / "workspace").is_dir()
    )
    archived = set()
    for account_root in account_roots:
        catalog = PathService(workspace_root=account_root).get_runtime_state_dir() / "workspaces.sqlite3"
        if not catalog.is_file():
            continue
        try:
            with closing(sqlite3.connect(f"{catalog.as_uri()}?mode=ro", uri=True)) as conn:
                for (payload,) in conn.execute("SELECT payload FROM workspaces"):
                    row = json.loads(payload)
                    if row.get("kind") == "workspace" and row.get("archived"):
                        storage = Path(row["path"]).expanduser() / ".deeptutor/data/knowledge_bases"
                        archived.add(str(storage.resolve()))
        except (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise WorkspaceError("The source workspace registry could not be read.") from exc
    return archived


def source_is_archived(base_dir: str | Path) -> bool:
    root = Path(base_dir).resolve()
    return str(root) in archived_source_roots()


def indexed_records() -> list[registry.KbRecord]:
    """Eligible KBs, keeping each creator even when several share one root."""
    records = registry.list_records()
    archived = archived_source_roots()
    return [
        record for record in records
        if is_indexed_source(record, archived=archived)
    ]


def is_indexed_source(record: registry.KbRecord, *, archived: set[str] | None = None) -> bool:
    if (
            record.kind != registry.UPLOADED_KIND
            or is_retired_source(record.base_dir)
            or not (Path(record.base_dir) / record.dir_name).is_dir()
    ):
        return False
    return (
        record.base_dir not in archived
        if archived is not None else not source_is_archived(record.base_dir)
    )


def indexed_roots() -> list[tuple[str, CurrentUser]]:
    """Distinct (root, owner) pairs; a mixed-owner root intentionally repeats."""
    pairs = {(record.base_dir, record.created_by): record for record in indexed_records()}
    return [(root, owner_user(pairs[root, owner])) for root, owner in sorted(pairs)]
