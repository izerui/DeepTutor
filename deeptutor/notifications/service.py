"""In-app notifications (spec 2026-11-03 batch 1 §5).

Lives in ``<SYSTEM_ROOT>/notifications.sqlite3``. A notification stores a
``kind`` and its parameters, never rendered text: the web client words it in
the reader's language. A ``dedupe_key`` makes "the same thing for the same
person" one row; with ``bump`` a repeat raises its count and marks it unread
again (summed per item and day). Rows older than 90 days go
on the next write. Opening one re-checks access where it points to.
"""

from __future__ import annotations

from collections.abc import Iterable
import json
import logging
import time
from typing import Any
import uuid

from deeptutor.multi_user.system_db import SystemDb

logger = logging.getLogger(__name__)

RETENTION_SECONDS = 90 * 24 * 3600

db = SystemDb(
    "notifications.sqlite3",
    """\
CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}',
    link TEXT NOT NULL DEFAULT '',
    dedupe_key TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    read_at REAL,
    UNIQUE (user_id, dedupe_key)
);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, created_at DESC);
""",
    "Notification",
)


def notify(
    user_ids: Iterable[str],
    kind: str,
    params: dict[str, Any] | None = None,
    *,
    link: str = "",
    dedupe_key: str = "",
    bump: bool = False,
) -> int:
    """Tell each of *user_ids*; return how many rows were added or raised.

    Never raises: a notification is a courtesy, never a reason for the action
    that caused it to fail.
    """
    recipients = sorted({str(u) for u in user_ids if u})
    if not recipients:
        return 0
    key = dedupe_key or f"{kind}:{uuid.uuid4().hex}"
    payload = json.dumps(params or {}, ensure_ascii=False)
    now = time.time()
    try:
        with db.write() as conn:
            conn.execute("DELETE FROM notifications WHERE created_at < ?", (now - RETENTION_SECONDS,))
            changed = 0
            for user_id in recipients:
                if bump:
                    cursor = conn.execute(
                        "UPDATE notifications SET count = count + 1, params_json = ?, created_at = ?, "
                        "read_at = NULL WHERE user_id = ? AND dedupe_key = ?",
                        (payload, now, user_id, key),
                    )
                    if cursor.rowcount:
                        changed += 1
                        continue
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO notifications "
                    "(id, user_id, kind, params_json, link, dedupe_key, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (f"ntf_{uuid.uuid4().hex[:16]}", user_id, kind, payload, link, key, now),
                )
                changed += cursor.rowcount
            return changed
    except Exception:
        logger.exception("Could not record %s notifications", kind)
        return 0


def _row(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "params": json.loads(row["params_json"] or "{}"),
        "link": row["link"],
        "count": row["count"],
        "created_at": row["created_at"],
        "read": row["read_at"] is not None,
    }


def list_for(user_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    with db.read() as conn:
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT * FROM notifications WHERE user_id = ? AND created_at >= ? "
            "ORDER BY created_at DESC LIMIT ?",
            (user_id, time.time() - RETENTION_SECONDS, max(1, min(limit, 200))),
        ).fetchall()
    return [_row(row) for row in rows]


def unread_count(user_id: str) -> int:
    with db.read() as conn:
        if conn is None:
            return 0
        return int(conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read_at IS NULL AND created_at >= ?",
            (user_id, time.time() - RETENTION_SECONDS),
        ).fetchone()[0])


def mark_read(user_id: str, ids: Iterable[str] | None = None) -> None:
    """Mark the given notifications (or all of them) of *user_id* read."""
    if not db.path().exists():
        return
    with db.write() as conn:
        if ids is None:
            conn.execute(
                "UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL",
                (time.time(), user_id),
            )
            return
        conn.executemany(
            "UPDATE notifications SET read_at = ? WHERE user_id = ? AND id = ? AND read_at IS NULL",
            [(time.time(), user_id, str(i)) for i in ids],
        )


def forget_user(user_id: str) -> None:
    """Hand-over stage ``notifications``: a deleted account's notifications go."""
    if not db.path().exists():
        return
    with db.write() as conn:
        conn.execute("DELETE FROM notifications WHERE user_id = ?", (user_id,))


def forget_links(links: list[str]) -> None:
    """Notifications pointing at something that no longer exists go (e.g. removed homework tasks)."""
    if not db.path().exists() or not links:
        return
    with db.write() as conn:
        for start in range(0, len(links), 500):
            chunk = links[start:start + 500]
            conn.execute(
                f"DELETE FROM notifications WHERE link IN ({','.join('?' for _ in chunk)})", chunk  # nosec B608
            )


def forget_kinds(kinds: list[str]) -> None:
    """Notifications of a kind the platform no longer has go (e.g. homework, spec 2026-11-05)."""
    if not db.path().exists() or not kinds:
        return
    with db.write() as conn:
        conn.execute(
            f"DELETE FROM notifications WHERE kind IN ({','.join('?' for _ in kinds)})", kinds  # nosec B608
        )

def notify_handover(successor_id: str, from_user_id: str, from_name: str, counts: dict[str, int]) -> None:
    """One summary to whoever took over a departing teacher's content (spec 2026-11-03 batch 4 §3)."""
    moved = {kind: int(n) for kind, n in counts.items() if n}
    if not successor_id or not moved:
        return
    notify([successor_id], "content_transferred", {"from": from_name, **moved},
           link="/start", dedupe_key=f"content_transferred:{from_user_id}:{successor_id}", bump=True)


__all__ = ["db", "forget_kinds", "forget_links", "forget_user", "list_for", "mark_read", "notify", "notify_handover", "unread_count"]
