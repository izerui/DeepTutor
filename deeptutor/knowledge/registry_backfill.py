"""Register every existing knowledge base in the system registry (spec 7.7).

Runs at startup and is idempotent: files never move, already registered KBs
keep their id, and the one-time D16 promotion (admin KBs that were granted to
anyone become public) is guarded by a registry meta key so an admin who later
withdraws one is not overruled on the next start. Every root is scanned on its
own: one damaged catalog is logged and skipped, never fatal (cross-check 6.3).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any

from deeptutor.knowledge import registry

logger = logging.getLogger(__name__)

GRANTS_PROMOTED_KEY = "grants_promoted_at"

#: The app lifespan only backfills while this is True. ``tests/conftest.py``
#: switches it off so running the real lifespan in a test never walks (or,
#: through the partner migration, moves) a developer's real ``data/``.
STARTUP_BACKFILL_ENABLED = True


def _startup_steps() -> list[tuple[str, Callable[[], Any]]]:
    """Every startup migration, in order — the one place later plans extend.

    Plan D appends its book registry here::

        from deeptutor.book.registry_backfill import backfill_book_registry

        steps.append(("books", backfill_book_registry))

    so it shares ``STARTUP_BACKFILL_ENABLED`` (and the test switch) and
    ``api/main.py`` never changes again.
    """
    steps: list[tuple[str, Callable[[], Any]]] = [("knowledge_bases", backfill_registry)]
    return steps


def run_startup_backfill() -> dict[str, Any]:
    """Run each startup step; one failing step never stops the next."""
    if not STARTUP_BACKFILL_ENABLED:
        return {}
    report: dict[str, Any] = {}
    for name, step in _startup_steps():
        try:
            report[name] = step()
        except Exception:
            logger.exception("Startup backfill step %s failed", name)
            report[name] = {"error": True}
    return report


def backfill_registry() -> dict[str, int]:
    from deeptutor.multi_user.identity import list_user_info
    from deeptutor.multi_user.models import LOCAL_ADMIN_ID, CurrentUser, normalize_role
    from deeptutor.multi_user.paths import (
        get_admin_path_service,
        local_admin_user,
        scope_for_user,
        user_context,
    )

    report = {
        "admin": 0,
        "account": 0,
        "workspace": 0,
        "promoted": 0,
        "connectors_disabled": 0,
        "partner_copies": 0,
    }
    # Guard before list_user_info (or any catalog reader) can normalize away
    # damaged identities and turn an unavailable owner into an unknown owner.
    from deeptutor.services.partners.kb_migration import identity_snapshot

    try:
        identity_snapshot()
    except (OSError, ValueError):
        logger.exception("Identity catalog unavailable; registry backfill will retry")
        return report
    admin_root = get_admin_path_service().get_knowledge_bases_root()
    report["admin"] = _sync_root(admin_root, created_by=LOCAL_ADMIN_ID)
    # Every admin shares the admin scope, so its custom workspaces are scanned once.
    with user_context(local_admin_user()):
        report["workspace"] += _sync_workspaces(created_by=LOCAL_ADMIN_ID)

    users = [info for info in list_user_info() if str(info.get("id") or "")]
    for info in users:
        user_id = str(info["id"])
        role = normalize_role(str(info.get("role") or "user"))
        if role == "admin":
            continue
        scope = scope_for_user(user_id, is_admin=False)
        # Never create a workspace for an account that never used one.
        if not scope.root.is_dir():
            continue
        report["account"] += _sync_root(scope.root / "knowledge_bases", created_by=user_id)
        user = CurrentUser(
            id=user_id,
            username=str(info.get("username") or user_id),
            role=role,  # type: ignore[arg-type]
            scope=scope,
        )
        with user_context(user):
            report["workspace"] += _sync_workspaces(created_by=user_id)

    if registry.get_meta(GRANTS_PROMOTED_KEY) is None:
        # D16/D19 require the admin KBs to be registered first. If the admin-root
        # sync failed (report["admin"] == 0 while the directory exists), skip
        # promotion and leave the key unset so the next startup retries.
        admin_sync_ok = report["admin"] > 0 or not admin_root.is_dir()
        if not admin_sync_ok:
            logger.warning(
                "Admin-root sync returned 0 — skipping D16 grant promotion "
                "so the next startup retries"
            )
        else:
            try:
                promoted, disabled = _promote_granted_admin_kbs(admin_root, users)
                report["promoted"] = promoted
                report["connectors_disabled"] = disabled
                registry.set_meta(
                    GRANTS_PROMOTED_KEY,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                )
            except Exception:
                logger.exception(
                    "D16 grant promotion failed — key left unset so the next "
                    "startup retries"
                )
    try:
        from deeptutor.services.partners.kb_migration import migrate_partner_kb_copies

        partner_report = migrate_partner_kb_copies()
        report["partner_copies"] = len(partner_report["referenced"]) + len(
            partner_report["adopted"]
        )
    except Exception:
        logger.exception("Partner knowledge-base copy migration failed")
    return report


def _sync_root(root: Path, *, created_by: str) -> int:
    try:
        return _sync_root_unguarded(root, created_by=created_by)
    except Exception:
        logger.exception("Registry backfill skipped knowledge-base root %s", root)
        return 0


def _sync_root_unguarded(root: Path, *, created_by: str) -> int:
    from deeptutor.knowledge.manager import KnowledgeBaseManager

    if not root.is_dir():
        return 0
    # Manager reconciliation tolerates damaged JSON as an empty catalog and
    # may rewrite it when discovering folders. Keep that failure observable to
    # the subsequent partner migration instead of manufacturing "missing".
    try:
        catalog = json.loads((root / "kb_config.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        pass
    else:
        if not isinstance(catalog, dict) or not isinstance(catalog.get("knowledge_bases"), dict):
            raise ValueError(f"Invalid knowledge catalog: {root}")
        if any(not isinstance(entry, dict) for entry in catalog["knowledge_bases"].values()):
            raise ValueError(f"Invalid knowledge entries: {root}")
    manager = KnowledgeBaseManager(base_dir=str(root))
    names = manager.list_knowledge_bases()
    registry.sync_base_dir(
        root, {name: manager.get_kb_entry(name) for name in names}, created_by=created_by
    )
    return len(names)


def _sync_workspaces(*, created_by: str) -> int:
    from deeptutor.services.path_service import get_path_service
    from deeptutor.services.workspace import get_content_workspace_service
    from deeptutor.services.workspace.context import workspace_context

    count = 0
    try:
        rows = get_content_workspace_service()._catalog()
    except Exception:
        logger.exception("Workspace catalog unavailable during registry backfill")
        return 0
    for row in rows:
        if row.get("kind") != "workspace":
            continue
        try:
            with workspace_context(row["workspace_id"]):
                count += _sync_root(
                    get_path_service().get_knowledge_bases_root(), created_by=created_by
                )
        except Exception:
            logger.exception(
                "Skipped knowledge bases of workspace %s during registry backfill",
                row.get("workspace_id"),
            )
    return count


def _promote_granted_admin_kbs(admin_root: Path, users: list[dict[str, Any]]) -> tuple[int, int]:
    """D16: granted admin KBs become public; D19: granted connectors are reported."""
    from deeptutor.multi_user.grants import GrantStorageError, load_grant

    granted: dict[str, list[str]] = {}
    for info in users:
        if str(info.get("role") or "") == "admin":
            continue
        username = str(info.get("username") or info["id"])
        try:
            grant = load_grant(str(info["id"]))
        except GrantStorageError:
            logger.warning("Unreadable grant for %s ignored by registry backfill", info["id"])
            continue
        for item in grant.get("knowledge_bases") or []:
            name = str(item.get("name") or item.get("kb_name") or "").strip()
            resource_id = str(item.get("resource_id") or item.get("id") or "")
            if resource_id.startswith("admin:kb:"):
                name = resource_id[len("admin:kb:"):]
            if name and username not in granted.setdefault(name, []):
                granted[name].append(username)

    promoted = disabled = 0
    for name, usernames in sorted(granted.items()):
        record = registry.find_by_location(admin_root, name)
        if record is None:
            continue
        if registry.is_connector(record):
            # D19: connectors stay private; the admin sees who lost access.
            registry.add_migration_item(
                "connector_grant_disabled",
                name,
                {"name": name, "kind": record.kind, "users": usernames},
            )
            disabled += 1
            continue
        if record.visibility != "public":
            registry.set_visibility(record.kb_id, "public")
            promoted += 1
        registry.add_migration_item(
            "kb_made_public",
            record.kb_id,
            {"kb_id": record.kb_id, "name": name, "users": usernames},
        )
    return promoted, disabled
