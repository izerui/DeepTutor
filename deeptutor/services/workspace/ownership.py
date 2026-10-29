"""Whether a folder may store one account's personal learning data.

Spec 2026-10-29 §4 rule 3: one storage folder belongs to one account. The
deployment content folder (``DEEPTUTOR_WORKSPACE_ROOT``) is every account's
agent-readable folder, so it never becomes anyone's learning workspace; nor
may a folder inside another account's data, the system data, or a folder
another account has already registered.
"""

from __future__ import annotations

from contextlib import closing
import json
import logging
import os
from pathlib import Path
import sqlite3

from deeptutor.knowledge.file_snapshot import FileSnapshotCache

logger = logging.getLogger(__name__)

SHARED_DEPLOYMENT_FOLDER = (
    "This folder is the deployment's shared content folder and cannot store "
    "personal learning data."
)
FOREIGN_FOLDER = "This folder belongs to another account."

# Parsed catalogs, re-validated per use like registry_scope._CATALOG_LOCATIONS.
_CATALOG_OWNERS = FileSnapshotCache()


def _deployment_root() -> Path | None:
    raw = os.environ.get("DEEPTUTOR_WORKSPACE_ROOT", "").strip()
    return Path(raw).expanduser().resolve() if raw else None


def _account_roots() -> list[Path]:
    from deeptutor.multi_user import paths

    roots = [paths.ADMIN_WORKSPACE_ROOT.resolve()]
    if paths.USERS_ROOT.is_dir():
        roots.extend(path.resolve() for path in paths.USERS_ROOT.iterdir() if path.is_dir())
    return roots


def _inside_foreign_account(resolved: Path) -> bool:
    from deeptutor.multi_user import paths
    from deeptutor.multi_user.paths import get_account_path_service

    if resolved.is_relative_to(paths.SYSTEM_ROOT.resolve()):
        return True
    own = get_account_path_service().workspace_root.resolve()
    users = paths.USERS_ROOT.resolve()
    # Checked first: the administrator's own root contains every user root.
    if resolved.is_relative_to(users):
        parts = resolved.relative_to(users).parts
        return (users / parts[0] if parts else users) != own
    # A partner's own root lives under the administrator root; it is still its own.
    if resolved.is_relative_to(own):
        return False
    return resolved.is_relative_to(paths.ADMIN_WORKSPACE_ROOT.resolve())


def _catalog_owners(catalog: Path) -> dict[str, frozenset[str]]:
    owners: dict[str, set[str]] = {}
    with closing(sqlite3.connect(f"{catalog.as_uri()}?mode=ro", uri=True)) as conn:
        for (payload,) in conn.execute("SELECT payload FROM workspaces"):
            row = json.loads(payload)
            if row.get("kind") != "workspace":
                continue
            location = str(Path(row["path"]).expanduser().resolve())
            owners.setdefault(location, set()).add(str(row.get("owner_id") or ""))
    return {location: frozenset(ids) for location, ids in owners.items()}


def _claimed_by_others(resolved: Path, owner_id: str) -> bool:
    from deeptutor.services.path_service import PathService

    location = str(resolved)
    visited = []
    claimed = False
    for account_root in _account_roots():
        catalog = PathService(workspace_root=account_root).get_runtime_state_dir() / "workspaces.sqlite3"
        visited.append(catalog)
        if not catalog.is_file():
            continue
        try:
            owners = _CATALOG_OWNERS.load(catalog, _catalog_owners)
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as exc:
            # That account cannot open its own workspaces either; skip it.
            logger.warning("Workspace catalog %s skipped: %s", catalog, exc)
            continue
        if owners.get(location, frozenset()) - {owner_id}:
            claimed = True
    _CATALOG_OWNERS.retain(visited)
    return claimed


def personal_root_error(root: Path, owner_id: str) -> str:
    """``""`` when *root* may hold *owner_id*'s learning data, else the reason."""
    resolved = Path(root).expanduser().resolve()
    if resolved == _deployment_root():
        return SHARED_DEPLOYMENT_FOLDER
    if _inside_foreign_account(resolved) or _claimed_by_others(resolved, owner_id):
        return FOREIGN_FOLDER
    return ""
