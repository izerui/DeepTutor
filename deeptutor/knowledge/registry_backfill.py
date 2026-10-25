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
import sqlite3
import stat
import time
from typing import Any

from deeptutor.core.visibility_intent import explicit_choice
from deeptutor.knowledge import registry

logger = logging.getLogger(__name__)

GRANTS_PROMOTED_KEY = "grants_promoted_at"
_INVENTORY_KEY = "grants_inventory_v1"
_PROGRESS_PREFIX = "grants_target_v1:"

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
    from deeptutor.book.registry_backfill import backfill_book_registry

    steps.append(("books", backfill_book_registry))
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

    admin_root = get_admin_path_service().get_knowledge_bases_root()
    try:
        if registry.get_meta(GRANTS_PROMOTED_KEY) is None:
            _begin_inventory(admin_root)
        identity_snapshot()
        users = [info for info in list_user_info() if str(info.get("id") or "")]
        if registry.get_meta(GRANTS_PROMOTED_KEY) is None:
            _freeze_grants(users)
    except Exception:
        logger.exception("Identity catalog unavailable; registry backfill will retry")
        return report
    census_complete = False
    try:
        census_complete = (
            registry.get_meta(GRANTS_PROMOTED_KEY) is not None
            or _census_unchanged(admin_root)
        )
        report["admin"] = _sync_root_unguarded(admin_root, created_by=LOCAL_ADMIN_ID)
        admin_sync_ok = True
    except Exception:
        admin_sync_ok = False
        logger.exception("Registry backfill skipped knowledge-base root %s", admin_root)
    # Every admin shares the admin scope, so its custom workspaces are scanned once.
    with user_context(local_admin_user()):
        report["workspace"] += _sync_workspaces(created_by=LOCAL_ADMIN_ID)

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
        if not admin_sync_ok:
            logger.warning(
                "Admin-root sync failed; retaining pending D16 grant promotion"
            )
        else:
            try:
                snapshot = _freeze_targets(admin_root, census_complete=census_complete)
                if _deliver_targets(report) and snapshot["inventory_complete"]:
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

    try:
        info = root.stat()
    except FileNotFoundError:
        return 0
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"Not a knowledge-base root: {root}")
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


def _begin_inventory(admin_root: Path) -> None:
    """Freeze registered identities before any catalog/identity operation fails."""
    with registry._write() as conn:
        if conn.execute(
            "SELECT 1 FROM main.registry_meta WHERE key = ?", (_INVENTORY_KEY,),
        ).fetchone():
            return
        identities = dict(conn.execute(
            "SELECT dir_name, kb_id FROM kb_registry WHERE base_dir = ?",
            (str(admin_root.resolve()),),
        ))
        started_ns = time.time_ns()
        root_state = _root_state(admin_root)
        conn.execute(
            "INSERT INTO main.registry_meta (key, value) VALUES (?, ?)",
            (_INVENTORY_KEY, json.dumps({
                "base_dir": str(admin_root.resolve()), "started_ns": started_ns,
                "identities": identities, "targets": None, "grants": None,
                "root_state": root_state, "inventory_complete": False,
            })),
        )


def _root_state(root: Path) -> list[int] | None | str:
    try:
        info = root.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        return "unknown"
    if not stat.S_ISDIR(info.st_mode):
        return "unknown"
    return [info.st_dev, info.st_ino, info.st_ctime_ns, info.st_mtime_ns]


def _census_unchanged(root: Path) -> bool:
    snapshot = json.loads(registry.get_meta(_INVENTORY_KEY))
    current = _root_state(root)
    return current != "unknown" and current == snapshot["root_state"]


def _freeze_grants(users: list[dict[str, Any]]) -> None:
    from deeptutor.multi_user.grants import load_grant

    snapshot = json.loads(registry.get_meta(_INVENTORY_KEY))
    if snapshot["grants"] is not None:
        return
    granted: dict[str, list[str]] = {}
    for info in users:
        if str(info.get("role") or "") == "admin":
            continue
        username = str(info.get("username") or info["id"])
        # An unreadable grant is not a complete authorization census.
        grant = load_grant(str(info["id"]))
        for item in grant.get("knowledge_bases") or []:
            name = str(item.get("name") or item.get("kb_name") or "").strip()
            resource_id = str(item.get("resource_id") or item.get("id") or "")
            if resource_id.startswith("admin:kb:"):
                name = resource_id[len("admin:kb:"):]
            if name and username not in granted.setdefault(name, []):
                granted[name].append(username)

    with registry._write() as conn:
        snapshot = json.loads(conn.execute(
            "SELECT value FROM main.registry_meta WHERE key = ?", (_INVENTORY_KEY,),
        ).fetchone()[0])
        if snapshot["grants"] is None:
            snapshot["grants"] = granted
            conn.execute(
                "UPDATE main.registry_meta SET value = ? WHERE key = ?",
                (json.dumps(snapshot), _INVENTORY_KEY),
            )


def _freeze_targets(admin_root: Path, *, census_complete: bool | None = None) -> dict:
    """Bind old names once to canonical IDs, never re-resolve a pending target."""
    from deeptutor.multi_user.models import LOCAL_ADMIN_ID

    if census_complete is None:
        census_complete = _census_unchanged(admin_root)
    with registry._write() as conn:
        snapshot = json.loads(conn.execute(
            "SELECT value FROM main.registry_meta WHERE key = ?", (_INVENTORY_KEY,),
        ).fetchone()[0])
        if snapshot["inventory_complete"]:
            return snapshot
        if snapshot["base_dir"] != str(admin_root.resolve()):
            raise ValueError("Admin KB root changed during migration")
        targets = snapshot["targets"] or {}
        for name, usernames in sorted(snapshot["grants"].items()):
            row = conn.execute(
                "SELECT * FROM kb_registry WHERE base_dir = ? AND dir_name = ?",
                (snapshot["base_dir"], name),
            ).fetchone()
            if row is None:
                continue
            record = registry._record(row)
            if record.kb_id in targets:
                continue
            if record.created_by != LOCAL_ADMIN_ID:
                continue
            original_id = snapshot["identities"].get(name)
            if original_id is not None and original_id != record.kb_id:
                continue
            source = None
            connector = registry.is_connector(record)
            if not connector:
                info = (admin_root / name).lstat()
                if not stat.S_ISDIR(info.st_mode):
                    raise ValueError(f"Unsafe legacy KB location: {name}")
                source = [info.st_dev, info.st_ino]
                # Registered IDs are authoritative. Only an unregistered old
                # directory needs the same conservative POSIX evidence as D3.
                if original_id is None and not (
                    0 < info.st_ctime_ns < snapshot["started_ns"] <= time.time_ns()
                ):
                    if explicit_choice(conn, record.kb_id, schema="main"):
                        continue
                    census_complete = False
                    logger.warning("Unproven legacy KB identity remains pending: %s", name)
                    continue
            targets[record.kb_id] = {
                "name": name, "owner": record.created_by, "kind": record.kind,
                "source": source, "users": usernames, "connector": connector,
            }
            snapshot["identities"].setdefault(name, record.kb_id)
        snapshot["targets"] = targets
        snapshot["inventory_complete"] = census_complete
        conn.execute(
            "UPDATE main.registry_meta SET value = ? WHERE key = ?",
            (json.dumps(snapshot), _INVENTORY_KEY),
        )
        return snapshot


def _put_progress(conn: sqlite3.Connection, kb_id: str, progress: dict) -> None:
    conn.execute(
        "INSERT INTO main.registry_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (_PROGRESS_PREFIX + kb_id, json.dumps(progress)),
    )


def _promote_target(kb_id: str) -> tuple[dict, bool]:
    with registry._write() as conn:
        snapshot = json.loads(conn.execute(
            "SELECT value FROM main.registry_meta WHERE key = ?", (_INVENTORY_KEY,),
        ).fetchone()[0])
        target = snapshot["targets"][kb_id]
        previous = conn.execute(
            "SELECT value FROM main.registry_meta WHERE key = ?", (_PROGRESS_PREFIX + kb_id,),
        ).fetchone()
        if previous:
            return json.loads(previous[0]), False
        row = conn.execute("SELECT * FROM kb_registry WHERE kb_id = ?", (kb_id,)).fetchone()
        notices, promoted = [], False
        if row is not None:
            record = registry._record(row)
            if (
                record.base_dir != snapshot["base_dir"] or record.dir_name != target["name"]
                or record.created_by != target["owner"] or record.kind != target["kind"]
            ):
                raise ValueError(f"Legacy KB identity changed: {kb_id}")
            if target["source"] is not None:
                info = (Path(record.base_dir) / record.dir_name).lstat()
                if [info.st_dev, info.st_ino] != target["source"] or not stat.S_ISDIR(info.st_mode):
                    raise ValueError(f"Legacy KB source replaced: {kb_id}")
            if target["connector"] or registry.is_connector(record):
                notices.append(["connector_grant_disabled", record.name, {
                    "name": record.name, "kind": record.kind, "users": target["users"],
                }])
            elif not explicit_choice(conn, kb_id, schema="main"):
                # Compatibility with a prior version's committed notification:
                # its existence proves publication already happened.
                completed = conn.execute(
                    "SELECT 1 FROM migration_items WHERE kind = 'kb_made_public' AND item_key = ?",
                    (kb_id,),
                ).fetchone()
                if not completed:
                    promoted = record.visibility != "public"
                    conn.execute(
                        "UPDATE kb_registry SET visibility = 'public', updated_at = ? WHERE kb_id = ?",
                        (registry._now(), kb_id),
                    )
                notices.append(["kb_made_public", kb_id, {
                    "kb_id": kb_id, "name": record.name, "users": target["users"],
                }])
        progress = {"notices": notices, "done": False}
        _put_progress(conn, kb_id, progress)
        return progress, promoted


def _deliver_targets(report: dict[str, int]) -> bool:
    snapshot = json.loads(registry.get_meta(_INVENTORY_KEY))
    complete = True
    for kb_id in snapshot["targets"]:
        try:
            progress, promoted = _promote_target(kb_id)
            report["promoted"] += int(promoted)
            if progress["done"]:
                continue
            for kind, key, data in progress["notices"]:
                registry.add_migration_item(kind, key, data)
                report["connectors_disabled"] += int(kind == "connector_grant_disabled")
            progress["done"] = True
            registry.set_meta(_PROGRESS_PREFIX + kb_id, json.dumps(progress))
        except Exception:
            complete = False
            logger.exception("D16 target will retry %s", kb_id)
    return complete
