"""Register canonical book storage and resume the frozen D22 shelf migration.

Inventory and grant evidence precede all publication. A per-book outbox is
committed with publication/KB protection in D2's joint transaction. Notices
can then retry independently, including after an administrator withdraws a
book. No book files are moved or copied.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import sqlite3
import stat
import time

from deeptutor.book import registry, sharing
from deeptutor.book.storage import BookStorage
from deeptutor.core.visibility_intent import explicit_choice
from deeptutor.knowledge import registry as kbs

logger = logging.getLogger(__name__)
SHELF_MIGRATED_KEY = "shared_shelf_migrated_at"
_INVENTORY_KEY = "shared_shelf_inventory_v1"
_PROGRESS_PREFIX = "shared_shelf_book_v1:"


def _accounts() -> tuple[dict, dict]:
    from deeptutor.services.partners.kb_migration import identity_snapshot, _identity_store_paths

    identities = identity_snapshot()
    raw = {}
    for path in _identity_store_paths():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            break
        except FileNotFoundError:
            continue
    # B normalizes legacy accounts first. Do not let a future create-only live
    # loader erase grants before the durable eligibility snapshot is written.
    users = {info["username"]: {"id": uid, "role": info["role"]} for uid, info in identities.items()}
    if len(users) != len(raw) or any(
        not isinstance(raw.get(name), dict) or raw[name].get("id") != record["id"]
        for name, record in users.items()
    ):
        raise ValueError("Account identities need normalization or changed during book preflight")
    seen = set()
    for record in users.values():
        uid = record["id"]
        if (
            not re.fullmatch(r"[A-Za-z0-9_-]+", uid) or uid in seen
            or (record["role"] != "admin" and (uid == "local-admin" or uid.startswith("partner_")))
        ):
            raise ValueError("Unsafe or duplicate account identity during book backfill")
        seen.add(uid)
    return users, raw


def _inventory(root: Path) -> dict[str, list[int]]:
    """Enumerate without reading manifests; even broken books are frozen targets."""
    try:
        children = list(root.iterdir())
    except FileNotFoundError:
        return {}
    result = {}
    for child in sorted(children):
        if not child.name.startswith("book_"):
            continue
        info = child.lstat()
        # Symlinks and malformed entries remain explicit pending inputs.
        result[child.name] = [info.st_dev, info.st_ino]
    return result


def _freeze_inventory(root: Path) -> dict:
    stored = registry.get_meta(_INVENTORY_KEY)
    if stored is not None:
        return json.loads(stored)
    snapshot = {
        "base_dir": str(root.resolve()), "targets": {}, "grants": None,
        "started_ns": time.time_ns(), "inventory_complete": False,
        "attempts": 0, "observed": [], "pending": {},
    }
    # Commit the original fence before any enumeration/stat can fail. An empty
    # target map with inventory_complete=False is an obligation, not success.
    with registry._write() as conn:
        inserted = conn.execute(
            "INSERT OR IGNORE INTO registry_meta (key, value) VALUES (?, ?)",
            (_INVENTORY_KEY, json.dumps(snapshot)),
        ).rowcount == 1
        snapshot = json.loads(conn.execute(
            "SELECT value FROM registry_meta WHERE key = ?", (_INVENTORY_KEY,),
        ).fetchone()[0])
    # Only this live initializer may establish initial absence. A restarted
    # caller returning an existing unknown snapshot never receives this path.
    if inserted and _initial_absence_proven(Path(snapshot["base_dir"]), snapshot["started_ns"]):
        complete = {**snapshot, "initial_absence_confirmed": True, "inventory_complete": True}
        with registry._write() as conn:
            # Do not overwrite grants/progress from a concurrent startup.
            conn.execute(
                "UPDATE registry_meta SET value = ? WHERE key = ? AND value = ?",
                (json.dumps(complete), _INVENTORY_KEY, json.dumps(snapshot)),
            )
            snapshot = json.loads(conn.execute(
                "SELECT value FROM registry_meta WHERE key = ?", (_INVENTORY_KEY,),
            ).fetchone()[0])
    return snapshot


def _initial_absence_proven(root: Path, started_ns: int) -> bool:
    """Tie a fresh missing shelf to the fence using its unchanged ancestor."""
    candidate = root
    while True:
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            if candidate == candidate.parent:
                return False
            candidate = candidate.parent
        except OSError:
            logger.warning("Initial shelf absence unconfirmed: %s", root, exc_info=True)
            return False
        else:
            # If an original shelf disappeared after the fence, the ancestor
            # whose child disappeared has newer ctime, even on the fresh path.
            return (
                candidate != root and stat.S_ISDIR(info.st_mode)
                and 0 < info.st_ctime_ns < started_ns <= time.time_ns()
                and info.st_mtime_ns < started_ns
            )


def _resume_inventory() -> dict:
    """Recover only directories proven to predate the durable start fence.

    Kernel ctime (not editable manifest timestamps or mtime alone) is evidence
    of prior existence. A directory changed after the fence stays pending.
    A later listing is a complete original census only if the shelf directory
    itself has not changed since that fence; otherwise unknown obligations
    remain, even if all currently visible old books have been migrated.
    """
    with registry._write() as conn:
        snapshot = json.loads(conn.execute(
            "SELECT value FROM registry_meta WHERE key = ?", (_INVENTORY_KEY,),
        ).fetchone()[0])
        # Already-frozen v1 inventories predate the start/unknown-state schema.
        if snapshot.get("inventory_complete", True):
            return snapshot
        snapshot["attempts"] += 1
        conn.execute(
            "UPDATE registry_meta SET value = ? WHERE key = ?",
            (json.dumps(snapshot), _INVENTORY_KEY),
        )
    # The attempt is durable even if the process exits during filesystem I/O.
    with registry._write() as conn:
        snapshot = json.loads(conn.execute(
            "SELECT value FROM registry_meta WHERE key = ?", (_INVENTORY_KEY,),
        ).fetchone()[0])
        if snapshot.get("inventory_complete", True):
            return snapshot
        root = Path(snapshot["base_dir"])
        try:
            try:
                before = root.lstat()
            except FileNotFoundError:
                # Even attempts=0 may follow a preflight failure or process exit.
                # Current absence provides no evidence about the original shelf.
                logger.warning("Legacy shelf absent; retaining unknown inventory: %s", root)
            else:
                if not stat.S_ISDIR(before.st_mode):
                    raise ValueError(f"Unsafe legacy shelf: {root}")
                names = {child.name for child in root.iterdir() if child.name.startswith("book_")}
                snapshot["observed"] = sorted(set(snapshot["observed"]) | names)
                for dirname in snapshot["observed"]:
                    if dirname in snapshot["targets"]:
                        continue
                    try:
                        info = (root / dirname).lstat()
                        if not stat.S_ISDIR(info.st_mode):
                            raise ValueError("Not a canonical book directory")
                        if not 0 < info.st_ctime_ns < snapshot["started_ns"] <= time.time_ns():
                            raise ValueError("Directory cannot be proven to predate migration")
                        snapshot["targets"][dirname] = [info.st_dev, info.st_ino]
                        snapshot["pending"].pop(dirname, None)
                    except (OSError, ValueError) as exc:
                        snapshot["pending"][dirname] = str(exc)
                        logger.warning("Legacy inventory target pending %s: %s", root / dirname, exc)
                after = root.lstat()
                unchanged_census = (
                    (before.st_dev, before.st_ino, before.st_ctime_ns, before.st_mtime_ns)
                    == (after.st_dev, after.st_ino, after.st_ctime_ns, after.st_mtime_ns)
                    and 0 < after.st_ctime_ns < snapshot["started_ns"] <= time.time_ns()
                    and after.st_mtime_ns < snapshot["started_ns"]
                )
                snapshot["inventory_complete"] = unchanged_census and not snapshot["pending"]
        except (OSError, ValueError):
            logger.exception("Legacy shelf inventory remains unknown: %s", root)
        conn.execute(
            "UPDATE registry_meta SET value = ? WHERE key = ?",
            (json.dumps(snapshot), _INVENTORY_KEY),
        )
        return snapshot


def _freeze_grants(users: dict, raw: dict) -> dict:
    from deeptutor.multi_user.book_permission import legacy_book_shares

    grants = []
    for username, user in sorted(users.items()):
        if user["role"] == "admin":
            continue
        source = raw.get(username, {})
        shares = legacy_book_shares(source.get("book_permission") if isinstance(source, dict) else None)
        grants.append({
            "user_id": user["id"], "username": username,
            "default": shares.default, "books": dict(shares.books),
        })
    with registry._write() as conn:
        snapshot = json.loads(conn.execute(
            "SELECT value FROM registry_meta WHERE key = ?", (_INVENTORY_KEY,),
        ).fetchone()[0])
        if snapshot["grants"] is None:
            snapshot["grants"] = grants
            conn.execute(
                "UPDATE registry_meta SET value = ? WHERE key = ?",
                (json.dumps(snapshot), _INVENTORY_KEY),
            )
        return snapshot


def _catalog_names(root: Path) -> set[str]:
    """Strict read before a tolerant KB manager can rewrite damaged source data."""
    try:
        data = json.loads((root / "kb_config.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        try:
            children = list(root.iterdir())
        except FileNotFoundError:
            return set()
        if children:
            raise ValueError(f"Missing KB catalog in nonempty root: {root}")
        return set()
    entries = data.get("knowledge_bases") if isinstance(data, dict) else None
    if not isinstance(entries, dict) or any(not isinstance(v, dict) for v in entries.values()):
        raise ValueError(f"Invalid KB catalog: {root}")
    # An unindexed directory is evidence of incompleteness, not of absence.
    return set(entries) | {child.name for child in root.iterdir() if child.is_dir()}


def _confirmed_missing(refs: list[str]) -> list[str]:
    from deeptutor.multi_user.knowledge_access import DEFAULT_KB_ALIASES
    from deeptutor.multi_user.paths import get_admin_path_service
    from deeptutor.services.path_service import get_path_service

    own = get_path_service().get_knowledge_bases_root()
    admin = get_admin_path_service().get_knowledge_bases_root()
    names = {root: _catalog_names(root) for root in {own, admin}}
    missing = []
    for ref in refs:
        roots, name = {own, admin}, ref.strip()
        if ref.startswith("admin:kb:"):
            roots, name = {admin}, ref[len("admin:kb:"):].strip()
        elif ref.startswith("user:kb:"):
            roots, name = {own}, ref[len("user:kb:"):].strip()
        if (
            name.lower() not in DEFAULT_KB_ALIASES and ":" not in name
            and "/" not in name and "\\" not in name
            and all(name not in names[root] and kbs.find_by_location(root, name) is None for root in roots)
        ):
            missing.append(ref)
    return missing


def _sync_scope(created_by: str) -> tuple[int, bool]:
    from deeptutor.services.path_service import get_path_service

    count, complete = 0, True
    storage = BookStorage(path_service=get_path_service())
    root = storage.path_service.get_book_dir()
    try:
        inventory = _inventory(root)
    except Exception:
        logger.exception("Book inventory unavailable: %s", root)
        return 0, False
    for dirname in inventory:
        try:
            folder = root / dirname
            if not stat.S_ISDIR(folder.lstat().st_mode) or folder.is_symlink():
                raise ValueError(f"Unsafe book folder: {folder}")
            book_id = registry._book_id_of(dirname)
            manifest = storage.path_service.get_book_manifest_file(book_id)
            if manifest.is_symlink():
                raise ValueError(f"Unsafe book manifest: {manifest}")
            book = storage.load_book(book_id)
            if book is None or book.id != book_id:
                raise ValueError(f"Missing, invalid or mismatched book manifest: {manifest}")
            records = registry.sync_book_dir(root, {book_id: book.title or book_id}, created_by=created_by)
            if book_id not in records:
                complete = False
                continue
            count += 1
            record = records[book_id]
            if not registry.kb_links_initialized(book_id):
                # Ownership transfers are not inferred from a storage path.
                if record.created_by != created_by:
                    raise ValueError(f"Book owner differs from storage owner: {book_id}")
                links = registry.book_kb_links(book_id)
                refs = [ref for ref in book.knowledge_bases if ref not in links]
                missing = _confirmed_missing(refs) if any(not ref.startswith("kb:") for ref in refs) else []
                if not registry.initialize_book_kb_links(
                    book_id, book.knowledge_bases, confirmed_missing=missing,
                ):
                    complete = False
        except Exception:
            logger.exception("Book backfill will retry %s", root / dirname)
            complete = False
    return count, complete


def _sync_workspaces(created_by: str) -> tuple[int, bool]:
    from deeptutor.services.workspace import get_content_workspace_service
    from deeptutor.services.workspace.context import workspace_context

    count, complete = 0, True
    try:
        rows = get_content_workspace_service()._catalog()
        for row in rows:
            if row.get("kind") != "workspace":
                continue
            try:
                with workspace_context(row["workspace_id"]):
                    found, ready = _sync_scope(created_by)
                    count += found
                    complete = complete and ready
            except Exception:
                complete = False
                logger.exception("Workspace book backfill will retry %s", row.get("workspace_id"))
    except Exception:
        complete = False
        logger.exception("Workspace catalog unavailable during book backfill")
    return count, complete


def _put_progress(conn: sqlite3.Connection, key: str, value: dict) -> None:
    # Both databases have registry_meta; qualification is mandatory here.
    conn.execute(
        "INSERT INTO books.registry_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


def _promote_target(snapshot: dict, dirname: str) -> tuple[dict, bool]:
    """Commit the transition and its durable outbox together, once per target."""
    book_id = registry._book_id_of(dirname)
    key = _PROGRESS_PREFIX + book_id
    expected_root = snapshot["base_dir"]
    with sharing.shared_write() as conn:
        # Re-read durable authorization under the publication writer, rather
        # than trusting the caller's pre-transaction inventory.
        snapshot = json.loads(conn.execute(
            "SELECT value FROM books.registry_meta WHERE key = ?", (_INVENTORY_KEY,),
        ).fetchone()[0])
        if snapshot["base_dir"] != expected_root:
            raise ValueError("Legacy shelf authorization changed during migration")
        previous = conn.execute(
            "SELECT value FROM books.registry_meta WHERE key = ?", (key,),
        ).fetchone()
        if previous:
            return json.loads(previous[0]), False
        folder = Path(snapshot["base_dir"]) / dirname
        info = folder.lstat()
        if [info.st_dev, info.st_ino] != snapshot["targets"][dirname] or not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"Legacy book location replaced: {folder}")
        from deeptutor.multi_user.paths import get_admin_path_service

        storage = BookStorage(path_service=get_admin_path_service())
        if str(storage.path_service.get_book_dir().resolve()) != snapshot["base_dir"]:
            raise ValueError("Legacy shelf root changed during migration")
        manifest = storage.path_service.get_book_manifest_file(book_id)
        if manifest.is_symlink():
            raise ValueError(f"Unsafe legacy manifest: {manifest}")
        book = storage.load_book(book_id)
        if book is None or book.id != book_id:
            raise ValueError(f"Mismatched legacy manifest: {manifest}")
        record = registry._target(conn, book_id)
        from deeptutor.multi_user.models import LOCAL_ADMIN_ID

        if (
            record.base_dir != snapshot["base_dir"] or record.dir_name != dirname
            or record.created_by != LOCAL_ADMIN_ID
        ):
            raise ValueError(f"Legacy book location conflict: {book_id}")
        state = conn.execute(
            "SELECT complete FROM books.book_kb_binding_state WHERE book_id = ?", (book_id,),
        ).fetchone()
        if not state or not state[0]:
            raise registry.BookKbResolutionError(f"Pending legacy book: {book_id}")
        visible, edits = [], []
        for grant in snapshot["grants"]:
            level = grant["books"].get(book_id, grant["default"])
            if level == "none":
                continue
            visible.append(grant["username"])
            if level == "edit":
                edits.append({
                    "user_id": grant["user_id"], "username": grant["username"],
                    "book_id": book_id, "title": record.title,
                })
        notices = []
        promoted = False
        if visible and not explicit_choice(conn, book_id, schema="books"):
            sharing._authorize(conn, record, force=True)
            plan = sharing._plan(conn, record)
            for issue in plan.issues:
                notices.append(["book_kb_unavailable", f"{book_id}:{issue.kb_id}", {
                    "book_id": book_id, "title": record.title, "kb": issue.name, "reason": issue.code,
                }])
            promoted = record.visibility != "public"
            conn.execute(
                "UPDATE books.book_registry SET visibility = 'public', updated_at = ? WHERE book_id = ?",
                (registry._now(), book_id),
            )
            sharing.sync_protection(conn, book_id, force=True)
            notices.append(["book_auto_public", book_id, {
                "book_id": book_id, "title": record.title, "users": visible,
            }])
        notices.extend(
            ["book_edit_grant_retired", f"{grant['user_id']}:{book_id}", grant] for grant in edits
        )
        progress = {"notices": notices, "done": False}
        _put_progress(conn, key, progress)
        return progress, promoted


def _migrate(snapshot: dict, report: dict[str, int]) -> bool:
    complete = True
    for dirname in snapshot["targets"]:
        try:
            progress, promoted = _promote_target(snapshot, dirname)
            report["promoted"] += int(promoted)
            if progress["done"]:
                continue
            for kind, key, data in progress["notices"]:
                kbs.add_migration_item(kind, key, data)
                report["edit_grants"] += int(kind == "book_edit_grant_retired")
            progress["done"] = True
            registry.set_meta(_PROGRESS_PREFIX + registry._book_id_of(dirname), json.dumps(progress))
        except Exception:
            complete = False
            logger.exception("D22 migration will retry %s", dirname)
    return complete


def backfill_book_registry() -> dict[str, int]:
    from deeptutor.multi_user.models import LOCAL_ADMIN_ID, CurrentUser
    from deeptutor.multi_user.paths import (
        USERS_ROOT, get_admin_path_service, local_admin_user, scope_for_user, user_context,
    )
    from deeptutor.services.workspace.context import WorkspaceScope, workspace_context

    report = {"admin": 0, "account": 0, "workspace": 0, "promoted": 0, "edit_grants": 0}
    try:
        done = registry.get_meta(SHELF_MIGRATED_KEY) is not None
        snapshot = None if done else _freeze_inventory(get_admin_path_service().get_book_dir())
        users, raw = _accounts()
        if snapshot is not None and snapshot["grants"] is None:
            snapshot = _freeze_grants(users, raw)
        if snapshot is not None:
            snapshot = _resume_inventory()
    except Exception:
        logger.exception("Book migration preflight unavailable; retry without publication")
        return report
    complete = True
    actors = [local_admin_user()]
    for username, record in users.items():
        if record["role"] == "admin":
            continue
        scope = scope_for_user(record["id"], is_admin=False)
        if scope.root.parent != USERS_ROOT.resolve() or (USERS_ROOT / record["id"]).is_symlink():
            logger.warning("Unsafe account book root: %s", record["id"])
            complete = False
            continue
        if scope.root.is_dir():
            actors.append(CurrentUser(
                id=record["id"], username=username, role=record["role"], scope=scope,
            ))
    for actor in actors:
        # Drop even a same-account caller's active workspace explicitly.
        general = WorkspaceScope("", actor.scope.root, actor.scope.root)
        with user_context(actor), workspace_context(general):
            count, ready = _sync_scope(actor.id)
            report["admin" if actor.id == LOCAL_ADMIN_ID else "account"] += count
            complete = complete and ready
            count, ready = _sync_workspaces(actor.id)
            report["workspace"] += count
            complete = complete and ready
    if snapshot is not None:
        with user_context(local_admin_user()):
            migrated = _migrate(snapshot, report)
        if complete and migrated and snapshot.get("inventory_complete", True):
            registry.set_meta(SHELF_MIGRATED_KEY, registry._now())
    return report
