"""Content published to everyone becomes listed (platform direction §9.1, batch 1 §7).

Runs once at startup, and is safe to run again. Authors' and
administrators' content that reached everyone — a stored "everyone"
scope, or a public book or knowledge base that never had a scope — gets a
listing in the market. "Everyone" goes; the people who had started on it
join (``migrated``): who answered in a path's copy, handed in a practice
set, completed or handed in a task of a course, or has reading progress in a
book. Knowledge bases keep no trace of their readers, so nobody joins them.
Classification stays empty until the author fills it in.

Before it changes anything, the publish-scope and market stores are copied
next to themselves.
"""

from __future__ import annotations

import logging
import shutil
import time
from typing import Any

from deeptutor.catalog import enrollment, items
from deeptutor.catalog.store import db
from deeptutor.publishing import scope as scopes

logger = logging.getLogger(__name__)

MARKER = "migration_everyone_listed"
#: Tests switch the startup run off, so the app lifespan never walks real data.
STARTUP_ENABLED = True


def done() -> bool:
    with db.read() as conn:
        if conn is None:
            return False
        return conn.execute("SELECT 1 FROM catalog_meta WHERE key = ?", (MARKER,)).fetchone() is not None


def _backup() -> None:
    """Once: a later retry after a failure does not copy everything again (review M6)."""
    marker = "backup:before-market"
    with db.read() as conn:
        if conn is not None and conn.execute("SELECT 1 FROM catalog_meta WHERE key = ?", (marker,)).fetchone():
            return
    _copy_stores()
    with db.write() as conn:
        conn.execute("INSERT OR IGNORE INTO catalog_meta (key, value) VALUES (?, ?)", (marker, str(time.time())))


def _copy_stores() -> None:
    from deeptutor.publishing import store as scope_store

    stamp = time.strftime("%Y%m%d-%H%M%S")
    for path in (scope_store.scopes_db_path(), db.path()):
        if path.exists():
            shutil.copy2(path, path.with_name(f"{path.name}.before-market-{stamp}"))


def _publisher(owner: str, accounts: dict[str, dict[str, Any]]) -> bool:
    from deeptutor.multi_user.models import LOCAL_ADMIN_ID

    return owner == LOCAL_ADMIN_ID or (accounts.get(owner) or {}).get("role") in ("teacher", "admin")


def _candidates(accounts: dict[str, dict[str, Any]]) -> list[tuple[str, str]]:
    """(kind, id) of authors' and administrators' content reaching everyone."""
    from deeptutor.book import registry as book_registry
    from deeptutor.knowledge import registry as kb_registry
    from deeptutor.practice_sets import store as practice_store
    from deeptutor.tasks.store import db as task_db
    from deeptutor.teaching.store import TeachingStore

    found: list[tuple[str, str]] = []

    def everyone(kind: str, item_id: str, *, unmanaged_counts: bool) -> bool:
        stored = scopes._stored_scope(kind, item_id)
        return (stored is None and unmanaged_counts) or (stored is not None and stored.everyone)

    for record in book_registry.list_book_records(visibility="public"):
        if _publisher(record.created_by, accounts) and everyone(scopes.BOOK, record.book_id, unmanaged_counts=True):
            found.append((scopes.BOOK, record.book_id))
    for record in kb_registry.list_records(visibility="public"):
        if kb_registry.is_connector(record):
            continue
        if _publisher(record.created_by, accounts) and everyone(
            scopes.KNOWLEDGE_BASE, record.kb_id, unmanaged_counts=True
        ):
            found.append((scopes.KNOWLEDGE_BASE, record.kb_id))
    for template in TeachingStore().list_templates(owner_user_id=None):
        if template.status == "published" and _publisher(template.owner_user_id, accounts) and everyone(
            scopes.MASTERY_PATH, template.id, unmanaged_counts=False
        ):
            found.append((scopes.MASTERY_PATH, template.id))
    with practice_store.read() as conn:
        rows = conn.execute(
            "SELECT id, owner_user_id FROM practice_sets WHERE status = 'published'"
        ).fetchall() if conn else []
    for row in rows:
        if _publisher(row["owner_user_id"], accounts) and everyone(
            scopes.PRACTICE_SET, row["id"], unmanaged_counts=False
        ):
            found.append((scopes.PRACTICE_SET, row["id"]))
    with task_db.read() as conn:
        try:
            rows = conn.execute(
                "SELECT id, owner_user_id FROM courses WHERE status = 'published'"
            ).fetchall() if conn else []
        except Exception:
            rows = []  # a task store from before courses
    for row in rows:
        if _publisher(row["owner_user_id"], accounts) and everyone(scopes.COURSE, row["id"], unmanaged_counts=False):
            found.append((scopes.COURSE, row["id"]))
    return found


# ── who had started ────────────────────────────────────────


def _path_starters(template_id: str, people: list[str]) -> set[str]:
    from deeptutor.catalog.learner_data import _snapshots

    out = set()
    for user_id in people:
        try:
            for progress, _topic in _snapshots(user_id):
                origin = getattr(progress, "origin", None)
                if origin is None or origin.template_id != template_id:
                    continue
                if any(not getattr(a, "voided", False) for a in progress.quiz_attempts):
                    out.add(user_id)
        except Exception:
            logger.warning("Could not read the paths of %s", user_id, exc_info=True)
    return out


def _set_starters(set_id: str) -> set[str]:
    from deeptutor.practice_sets import store as practice_store

    with practice_store.read() as conn:
        if conn is None:
            return set()
        return {r[0] for r in conn.execute(
            "SELECT DISTINCT student_id FROM practice_submissions WHERE set_id = ?", (set_id,)
        )}


def _course_starters(course_id: str) -> set[str]:
    from deeptutor.tasks.store import db as task_db

    task_ids = [t["id"] for t in items.course_tasks(course_id)]
    if not task_ids:
        return set()
    marks = ",".join("?" for _ in task_ids)
    with task_db.read() as conn:
        if conn is None:
            return set()
        return {r[0] for r in conn.execute(
            f"SELECT DISTINCT student_id FROM task_completions WHERE task_key IN ({marks})", task_ids  # nosec B608
        )}


_reading_cache: dict[str, set[str]] = {}


def _books_read(user_id: str) -> set[str]:
    """Books with reading progress anywhere under the account — every workspace, not only the default."""
    if user_id not in _reading_cache:
        from deeptutor.multi_user.paths import scope_for_user

        root = scope_for_user(user_id, is_admin=False).root
        _reading_cache[user_id] = {
            path.parent.name[len("book_"):]
            for path in root.rglob("book_learning/book_*/progress.json") if path.is_file()
        } if root.exists() else set()
    return _reading_cache[user_id]


def _book_starters(book_id: str, people: list[str]) -> set[str]:
    out = set()
    for user_id in people:
        try:
            if book_id in _books_read(user_id):
                out.add(user_id)
        except Exception:
            logger.warning("Could not read the reading progress of %s", user_id, exc_info=True)
    return out


def _starters(kind: str, item_id: str, owner: str, accounts: dict[str, dict[str, Any]]) -> set[str]:
    learners = sorted(u for u, a in accounts.items() if a["role"] == "student" and u != owner)
    readers = sorted(u for u, a in accounts.items() if a["role"] in ("student", "teacher") and u != owner)
    if kind == scopes.MASTERY_PATH:
        return _path_starters(item_id, learners)
    if kind == scopes.PRACTICE_SET:
        return _set_starters(item_id) - {owner}
    if kind == scopes.COURSE:
        return _course_starters(item_id) - {owner}
    if kind == scopes.BOOK:
        return _book_starters(item_id, readers)
    return set()


# ── running ────────────────────────────────────────────────


def _migrate_one(kind: str, item_id: str, accounts: dict[str, dict[str, Any]]) -> None:
    info = items.info(kind, item_id)
    if info is None:
        return
    now = time.time()
    starters = _starters(kind, item_id, info.owner, accounts)
    # A practice set not ready for the market goes in as taken out of it — those who
    # started keep it; its author puts it back once it is ready.
    status, reason = "listed", ""
    if kind == scopes.PRACTICE_SET and info.not_ready:
        status, reason = "unlisted", info.not_ready
    with db.write() as conn:
        conn.execute(
            "INSERT INTO listings (kind, item_id, owner_user_id, status, title, summary, approved_once, migrated, "
            "reason, created_at, updated_at, listed_at) VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?, ?) "
            "ON CONFLICT(kind, item_id) DO NOTHING",
            (kind, item_id, info.owner, status, info.title[:80], info.description[:1000], reason, now, now, now),
        )
        conn.executemany(
            "INSERT INTO enrollments (kind, item_id, user_id, via, joined_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(kind, item_id, user_id, via) DO NOTHING",
            [(kind, item_id, user_id, enrollment.MIGRATED, now) for user_id in sorted(starters)],
        )
        conn.execute(
            "INSERT INTO listing_events (kind, item_id, actor_id, action, reason, at) VALUES (?, ?, '', 'migrated', '', ?)",
            (kind, item_id, now),
        )
    if kind == scopes.COURSE:
        # Its tasks first: if this stops halfway, the course still counts next time (review L2).
        for task in items.course_tasks(item_id):
            _drop_everyone(scopes.TASK, task["id"], info.owner)
    _drop_everyone(kind, item_id, info.owner)
    if kind == scopes.COURSE:
        enrollment.refresh_course(item_id)
    elif kind == scopes.MASTERY_PATH:
        enrollment.reconcile_path(item_id)


def _drop_everyone(kind: str, item_id: str, owner: str) -> None:
    stored = scopes._stored_scope(kind, item_id)
    if stored is not None and not stored.everyone:
        return
    kept = stored or scopes.Scope()
    scopes._store_scope(
        kind, item_id,
        scopes.Scope(users=list(kept.users)),
        actor_id=owner,
    )


def run() -> int:
    """List what reached everyone; return how many items were migrated."""
    if done():
        return 0
    _reading_cache.clear()
    from deeptutor.multi_user.accounts import accounts as _accounts

    accounts = _accounts()
    candidates = _candidates(accounts)
    if candidates:
        _backup()
    migrated = 0
    for kind, item_id in candidates:
        try:
            _migrate_one(kind, item_id, accounts)
            migrated += 1
        except Exception:
            logger.exception("Could not move %s %s into the market", kind, item_id)
            raise
    with db.write() as conn:
        conn.execute(
            "INSERT INTO catalog_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (MARKER, str(time.time())),
        )
    if migrated:
        logger.info("Moved %d items published to everyone into the market", migrated)
    return migrated


def run_at_startup() -> None:
    if not STARTUP_ENABLED:
        return
    try:
        run()
    except Exception:
        logger.exception("The market migration did not finish; it runs again at the next start")


__all__ = ["MARKER", "STARTUP_ENABLED", "done", "run", "run_at_startup"]
