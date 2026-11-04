"""Classes stop counting (platform batch 2 §2).

Once, at startup: everyone who receives content or a task through a class,
a department or grade, a named account or "everyone" joins it (``migrated``)
and those receivers are cleared; what a student's classes allowed or gave in
daily AI tokens is written into their own settings where it made a
difference. Only then is the "classes retired" mark set — from it on a class
gives nobody anything. A failed run leaves the mark unset, so classes keep
working until the next start tries again.
"""

from __future__ import annotations

import logging
import shutil
import time
from typing import Any

from deeptutor.catalog import enrollment
from deeptutor.catalog.store import db
from deeptutor.publishing import scope as scopes

logger = logging.getLogger(__name__)

MARKER = "classes_retired"
#: Tests switch the startup run off, so the app lifespan never walks real data.
STARTUP_ENABLED = True

_retired_at: dict[str, bool] = {}


def retired() -> bool:
    """Whether classes no longer give anything; read once per store file, then remembered."""
    key = str(db.path())
    if _retired_at.get(key):
        return True
    try:
        with db.read() as conn:
            found = conn is not None and conn.execute(
                "SELECT 1 FROM catalog_meta WHERE key = ?", (MARKER,)
            ).fetchone() is not None
    except Exception:
        logger.exception("Could not read whether classes are retired")
        return False
    if found:
        _retired_at[key] = True
    return found


def _accounts() -> dict[str, dict[str, Any]]:
    from deeptutor.classes.service import _accounts as accounts

    return accounts()


# ── content and tasks (§2.1) ───────────────────────────────


def _scoped_items() -> list[tuple[str, str]]:
    from deeptutor.publishing import store as scope_store

    with scope_store.read() as conn:
        if conn is None:
            return []
        rows = conn.execute("SELECT kind, item_id, everyone FROM publish_items").fetchall()
        targets = {
            (r["kind"], r["item_id"]) for r in conn.execute("SELECT DISTINCT kind, item_id FROM publish_targets")
        }
    return [(r["kind"], r["item_id"]) for r in rows if r["everyone"] or (r["kind"], r["item_id"]) in targets]


def _owner(kind: str, item_id: str) -> str | None:
    """``None`` only when the item is gone; an unreadable store raises and stops the run (review H3)."""
    from deeptutor.catalog import items

    return items.owner(kind, item_id)


def _student_owned_share(kind: str, owner: str, accounts: dict[str, dict[str, Any]]) -> bool:
    """A book or knowledge base a student once made public stays as it is (direction §9.3)."""
    return kind in (scopes.BOOK, scopes.KNOWLEDGE_BASE) and (accounts.get(owner) or {}).get("role") == "student"


def _unmanaged_assigned_paths() -> list[str]:
    from deeptutor.teaching.store import TeachingStore

    teaching = TeachingStore()
    out = []
    for template in teaching.list_templates(owner_user_id=None):
        if template.status != "published" or scopes._stored_scope(scopes.MASTERY_PATH, template.id) is not None:
            continue
        if any(a.status == "active" for a in teaching.list_assignments(template.id)):
            out.append(template.id)
    return out


def _receivers(kind: str, item_id: str, owner: str) -> set[str]:
    if kind == scopes.MASTERY_PATH and scopes._stored_scope(kind, item_id) is None:
        from deeptutor.teaching.store import TeachingStore

        return {a.student_user_id for a in TeachingStore().list_assignments(item_id) if a.status == "active"} - {owner}
    stored = scopes._stored_scope(kind, item_id)
    if stored is None:
        return set()
    return scopes.audience(stored, students_only=kind in scopes.STUDENT_KINDS, owner_id=owner)


def _move_receivers(accounts: dict[str, dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Receivers become members; returns the courses and paths to bring in line afterwards."""
    keys = list(dict.fromkeys([*_scoped_items(), *[(scopes.MASTERY_PATH, t) for t in _unmanaged_assigned_paths()]]))
    courses: list[str] = []
    paths: list[str] = []
    now = time.time()
    for kind, item_id in keys:
        owner = _owner(kind, item_id)
        if owner is None or _student_owned_share(kind, owner, accounts):
            continue
        receivers = _receivers(kind, item_id, owner)
        with db.write() as conn:
            conn.executemany(
                "INSERT INTO enrollments (kind, item_id, user_id, via, joined_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(kind, item_id, user_id, via) DO UPDATE SET left_at = NULL",
                [(kind, item_id, user_id, enrollment.MIGRATED, now) for user_id in sorted(receivers)],
            )
        scopes._store_scope(kind, item_id, scopes.Scope(), actor_id=owner)
        if kind == scopes.COURSE:
            courses.append(item_id)
        elif kind == scopes.MASTERY_PATH:
            paths.append(item_id)
    return courses, paths


# ── policies (§2.2) ────────────────────────────────────────


def _keep_policies(accounts: dict[str, dict[str, Any]]) -> int:
    """Write into each student's own settings what their classes changed for them."""
    from deeptutor.classes.service import class_ai_daily_tokens, class_student_policy
    from deeptutor.multi_user.grants import load_grant, save_grant
    from deeptutor.multi_user.roles import AI_DAILY_TOKENS, STUDENT_POLICY_DEFAULTS, _ai_tokens

    changed = 0
    for user_id, account in sorted(accounts.items()):
        if account["role"] != "student":
            continue
        from_classes = class_student_policy(user_id)
        class_tokens = class_ai_daily_tokens(user_id)
        if not from_classes and class_tokens is None:
            continue
        grant = load_grant(user_id)
        stored = dict(grant.get("student_policy") or {})
        before = dict(stored)
        for key, value in from_classes.items():
            if key in STUDENT_POLICY_DEFAULTS and not isinstance(stored.get(key), bool) \
                    and value != STUDENT_POLICY_DEFAULTS[key]:
                stored[key] = value
        if class_tokens is not None and _ai_tokens(stored.get(AI_DAILY_TOKENS)) is None:
            from deeptutor.services.llm.usage_limit import school_default_daily_tokens

            if class_tokens != school_default_daily_tokens():
                stored[AI_DAILY_TOKENS] = class_tokens
        if stored != before:
            save_grant(user_id, {**grant, "student_policy": stored})
            changed += 1
    return changed


# ── running ────────────────────────────────────────────────


def _backup() -> None:
    """Once: a later retry after a failure does not copy everything again (review M6)."""
    marker = "backup:before-classes-retired"
    with db.read() as conn:
        if conn is not None and conn.execute("SELECT 1 FROM catalog_meta WHERE key = ?", (marker,)).fetchone():
            return
    _copy_stores()
    with db.write() as conn:
        conn.execute("INSERT OR IGNORE INTO catalog_meta (key, value) VALUES (?, ?)", (marker, str(time.time())))


def _copy_stores() -> None:
    from deeptutor.multi_user import grants as grant_store
    from deeptutor.publishing import store as scope_store

    stamp = time.strftime("%Y%m%d-%H%M%S")
    for path in (scope_store.scopes_db_path(), db.path()):
        if path.exists():
            shutil.copy2(path, path.with_name(f"{path.name}.before-classes-retired-{stamp}"))
    grants = grant_store.GRANTS_DIR
    if grants.is_dir():
        shutil.copytree(grants, grants.with_name(f"grants.before-classes-retired-{stamp}"), dirs_exist_ok=True)


def run() -> bool:
    """Retire classes; ``True`` when this call did it."""
    if retired():
        return False
    from deeptutor.catalog import migration

    if not migration.done():
        # Review H2: "everyone" goes to the market first; never turn it into everyone joined.
        logger.warning("Classes stay until content published to everyone is in the market")
        return False
    accounts = _accounts()
    _backup()
    courses, paths = _move_receivers(accounts)
    for course_id in courses:
        enrollment.refresh_course(course_id)
    for template_id in paths:
        enrollment.reconcile_path(template_id)
    kept = _keep_policies(accounts)
    with db.write() as conn:
        conn.execute(
            "INSERT INTO catalog_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (MARKER, str(time.time())),
        )
    _retired_at[str(db.path())] = True
    logger.info("Classes retired; %d courses refreshed, %d students kept their class settings", len(courses), kept)
    return True


def run_at_startup() -> None:
    if not STARTUP_ENABLED:
        return
    try:
        run()
    except Exception:
        logger.exception("Retiring classes did not finish; classes keep working until the next start")


__all__ = ["MARKER", "STARTUP_ENABLED", "retired", "run", "run_at_startup"]
