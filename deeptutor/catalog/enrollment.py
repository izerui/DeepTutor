"""Joining and leaving (platform batch 1 §4).

A learner joins a listed item from the market (``self``). Joining a course
also brings in its tasks and what they cite (``course:<id>``); people who
had started on content before the market existed came in by the migration
(``migrated``). Leaving stamps ``left_at``: the learner's own records stay
where they are, and joining again picks them up.

The publish-scope layer reads :func:`access_state` whenever it reads an
item's receivers, so every kind of content sees its members without code of
its own.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from deeptutor.catalog.store import db
from deeptutor.publishing import scope as scopes

logger = logging.getLogger(__name__)

SELF = "self"
MIGRATED = "migrated"
COURSE_PREFIX = "course:"
#: Ways in the learner chose (they may leave them); via a course is the course's.
DIRECT = (SELF, MIGRATED)


class EnrollmentError(Exception):
    status_code = 400


class EnrollmentForbidden(EnrollmentError):
    status_code = 403


class EnrollmentNotFound(EnrollmentError):
    status_code = 404


_MEMBERS_SQL = (
    "SELECT DISTINCT e.user_id FROM enrollments e WHERE e.kind = ? AND e.item_id = ? AND e.left_at IS NULL "
    "AND NOT (e.via LIKE 'course:%' AND EXISTS (SELECT 1 FROM listings l WHERE l.kind = 'course' "
    "AND l.item_id = substr(e.via, 8) AND l.blocked = 1))"
)


def access_state(kind: str, item_id: str) -> tuple[bool, list[str]]:
    """(taken down by an administrator, the members who reach it now)."""
    with db.read() as conn:
        if conn is None:
            return False, []
        row = conn.execute(
            "SELECT blocked FROM listings WHERE kind = ? AND item_id = ?", (kind, item_id)
        ).fetchone()
        if row is not None and row["blocked"]:
            return True, []
        return False, sorted(r[0] for r in conn.execute(_MEMBERS_SQL, (kind, item_id)))


def access_states(kind: str) -> dict[str, tuple[bool, set[str]]]:
    """``access_state`` for every item of a kind at once: item -> (taken down, members) (review M5)."""
    with db.read() as conn:
        if conn is None:
            return {}
        blocked = {
            r[0] for r in conn.execute("SELECT item_id FROM listings WHERE kind = ? AND blocked = 1", (kind,))
        }
        rows = conn.execute(
            "SELECT DISTINCT e.item_id, e.user_id FROM enrollments e WHERE e.kind = ? AND e.left_at IS NULL "
            "AND NOT (e.via LIKE 'course:%' AND EXISTS (SELECT 1 FROM listings l WHERE l.kind = 'course' "
            "AND l.item_id = substr(e.via, 8) AND l.blocked = 1))",
            (kind,),
        ).fetchall()
    out: dict[str, tuple[bool, set[str]]] = {item: (True, set()) for item in blocked}
    for item_id, user_id in rows:
        if item_id not in blocked:
            out.setdefault(item_id, (False, set()))[1].add(user_id)
    return out


def members(kind: str, item_id: str) -> set[str]:
    return set(access_state(kind, item_id)[1])


def joined_count(kind: str, item_id: str) -> int:
    return joined_counts([(kind, item_id)]).get((kind, item_id), 0)


def joined_counts(keys: list[tuple[str, str]] | None = None) -> dict[tuple[str, str], int]:
    """People in each item now: who joined it, and who came in through a course (audit fix B §5).

    The same people as :func:`members` and the learning reports count; a
    course an administrator took down brings nobody.
    """
    where = (
        "e.left_at IS NULL AND NOT (e.via LIKE 'course:%' AND EXISTS (SELECT 1 FROM listings l "
        "WHERE l.kind = 'course' AND l.item_id = substr(e.via, 8) AND l.blocked = 1))"
    )
    with db.read() as conn:
        if conn is None:
            return {}
        rows = conn.execute(
            f"SELECT e.kind, e.item_id, COUNT(DISTINCT e.user_id) FROM enrollments e WHERE {where} "  # nosec B608
            "GROUP BY e.kind, e.item_id",
        ).fetchall() if keys is None or len(keys) > 1 else conn.execute(
            f"SELECT e.kind, e.item_id, COUNT(DISTINCT e.user_id) FROM enrollments e "  # nosec B608
            f"WHERE e.kind = ? AND e.item_id = ? AND {where} GROUP BY e.kind, e.item_id",
            keys[0],
        ).fetchall()
    counts = {(r[0], r[1]): int(r[2]) for r in rows}
    if keys is None:
        return counts
    return {key: counts.get(key, 0) for key in keys}


RECENT_DAYS = 7


def recent_joins(keys: list[tuple[str, str]] | None = None, *, days: int = RECENT_DAYS,
                 now: float | None = None) -> dict[tuple[str, str], int]:
    """People who came into each item in the last *days* days (polish batch §2).

    However they came in (themselves, through a course, the migration), each
    person once; someone who left since is not counted.
    """
    since = (now if now is not None else time.time()) - days * 86400
    with db.read() as conn:
        if conn is None:
            return {} if keys is None else {key: 0 for key in keys}
        rows = conn.execute(
            "SELECT kind, item_id, COUNT(DISTINCT user_id) FROM enrollments "
            "WHERE left_at IS NULL AND joined_at >= ? GROUP BY kind, item_id",
            (since,),
        ).fetchall()
    counts = {(r[0], r[1]): int(r[2]) for r in rows}
    if keys is None:
        return counts
    return {key: counts.get(key, 0) for key in keys}


def joined_by(user_id: str) -> dict[tuple[str, str], str]:
    """``{(kind, item_id): how}`` for everything the user is in now (``self``/``migrated``/``course``)."""
    with db.read() as conn:
        if conn is None:
            return {}
        rows = conn.execute(
            "SELECT kind, item_id, via FROM enrollments WHERE user_id = ? AND left_at IS NULL", (user_id,)
        ).fetchall()
    out: dict[tuple[str, str], str] = {}
    for row in rows:
        key = (row["kind"], row["item_id"])
        how = "course" if row["via"].startswith(COURSE_PREFIX) else row["via"]
        # A direct way in wins over a course.
        if key not in out or how in DIRECT:
            out[key] = how
    return out


# ── joining ────────────────────────────────────────────────


def join(user, kind: str, item_id: str) -> dict[str, Any]:
    from deeptutor.catalog import items, listings

    if kind not in items.KINDS:
        raise EnrollmentNotFound("Not found.")
    status = listings.status(kind, item_id)
    info = items.info(kind, item_id)
    # Listed but no longer ready (e.g. a course whose author removed every item): nobody joins it.
    if info is None or status != "listed" or info.closed or info.not_ready:
        raise EnrollmentNotFound("This is not in the market.")
    from deeptutor.multi_user.ownership import owner_ids

    if info.owner in owner_ids(user):
        raise EnrollmentError("This is your own.")
    now = time.time()
    with db.write() as conn:
        first = conn.execute(
            "SELECT 1 FROM enrollments WHERE kind = ? AND item_id = ? AND user_id = ? LIMIT 1",
            (kind, item_id, user.id),
        ).fetchone() is None
        conn.execute(
            "INSERT INTO enrollments (kind, item_id, user_id, via, joined_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(kind, item_id, user_id, via) DO UPDATE SET left_at = NULL",
            (kind, item_id, user.id, SELF, now),
        )
    _after_change(kind, item_id)
    if first:
        _notify_joined(kind, item_id, info)
    return {"kind": kind, "id": item_id, "joined": True}


def _notify_joined(kind: str, item_id: str, info) -> None:
    """Polish batch §2: its author hears of new people, one notice per item and day, counted.

    Only someone joining for the first time counts: leaving and joining again
    is not news.
    """
    from deeptutor.notifications.service import notify

    notify(
        [info.owner], "content_joined", {"content": kind, "title": info.title},
        link=f"/market/item?kind={kind}&id={item_id}",
        dedupe_key=f"content_joined:{kind}:{item_id}:{time.strftime('%Y-%m-%d')}", bump=True,
    )


def leave(user, kind: str, item_id: str) -> dict[str, Any]:
    now = time.time()
    with db.write() as conn:
        conn.execute(
            "UPDATE enrollments SET left_at = ? WHERE kind = ? AND item_id = ? AND user_id = ? "
            "AND via IN (?, ?) AND left_at IS NULL",
            (now, kind, item_id, user.id, *DIRECT),
        )
    _after_change(kind, item_id)
    return {"kind": kind, "id": item_id, "joined": False}


def _after_change(kind: str, item_id: str) -> None:
    """Paths hand out copies; a course brings its tasks and what they cite."""
    if kind == scopes.COURSE:
        refresh_course(item_id)
    elif kind == scopes.MASTERY_PATH:
        reconcile_path(item_id)


def reconcile_path(template_id: str) -> None:
    try:
        scopes.reconcile_template(template_id)
    except Exception:
        logger.exception("Could not update the copies of path %s", template_id)


# ── courses ────────────────────────────────────────────────


def _course_targets(course_id: str) -> set[tuple[str, str]]:
    """The course's tasks and the content they cite."""
    from deeptutor.catalog import items
    from deeptutor.catalog.listings import status

    kinds = {"mastery_path": scopes.MASTERY_PATH, "practice_set": scopes.PRACTICE_SET, "book": scopes.BOOK}
    author = items.owner(scopes.COURSE, course_id)
    targets: set[tuple[str, str]] = set()
    for task in items.course_tasks(course_id):
        targets.add((scopes.TASK, task["id"]))
        if task["kind"] not in kinds or not task["target_id"]:
            continue
        kind = kinds[task["kind"]]
        # A course hands out its author's own content, or what is in the market;
        # never someone else's item they took out or only showed to a few (review H1).
        if items.owner(kind, task["target_id"]) != author and status(kind, task["target_id"]) != "listed":
            continue
        targets.add((kind, task["target_id"]))
    return targets


def refresh_course(course_id: str) -> None:
    """Bring the rows a course hands out in line with its members and its items."""
    via = f"{COURSE_PREFIX}{course_id}"
    targets = _course_targets(course_id)
    now = time.time()
    with db.write() as conn:
        learners = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT user_id FROM enrollments WHERE kind = ? AND item_id = ? AND left_at IS NULL "
                "AND via IN (?, ?)",
                (scopes.COURSE, course_id, *DIRECT),
            )
        }
        current = {
            (r["kind"], r["item_id"], r["user_id"]) for r in conn.execute(
                "SELECT kind, item_id, user_id FROM enrollments WHERE via = ? AND left_at IS NULL", (via,)
            )
        }
        wanted = {(kind, item_id, user) for kind, item_id in targets for user in learners}
        for kind, item_id, user in wanted - current:
            conn.execute(
                "INSERT INTO enrollments (kind, item_id, user_id, via, joined_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(kind, item_id, user_id, via) DO UPDATE SET left_at = NULL",
                (kind, item_id, user, via, now),
            )
        for kind, item_id, user in current - wanted:
            conn.execute(
                "UPDATE enrollments SET left_at = ? WHERE kind = ? AND item_id = ? AND user_id = ? AND via = ?",
                (now, kind, item_id, user, via),
            )
    changed_paths = {
        item_id for kind, item_id, _ in (wanted ^ current) if kind == scopes.MASTERY_PATH
    }
    for template_id in sorted(changed_paths):
        reconcile_path(template_id)


def courses_citing(kind: str, item_id: str) -> list[str]:
    with db.read() as conn:
        if conn is None:
            return []
        return sorted({
            r[0][len(COURSE_PREFIX):] for r in conn.execute(
                "SELECT DISTINCT via FROM enrollments WHERE kind = ? AND item_id = ? AND via LIKE 'course:%'",
                (kind, item_id),
            )
        })


# ── accounts and items going away ──────────────────────────


def forget_user(user_id: str) -> int:
    if not db.path().exists():
        return 0
    with db.write() as conn:
        return int(conn.execute("DELETE FROM enrollments WHERE user_id = ?", (user_id,)).rowcount)


def forget_item(kind: str, item_id: str) -> None:
    if not db.path().exists():
        return
    with db.write() as conn:
        conn.execute("DELETE FROM enrollments WHERE kind = ? AND item_id = ?", (kind, item_id))
        if kind == scopes.COURSE:
            conn.execute("DELETE FROM enrollments WHERE via = ?", (f"{COURSE_PREFIX}{item_id}",))


__all__ = [
    "DIRECT", "EnrollmentError", "EnrollmentForbidden", "EnrollmentNotFound", "MIGRATED", "SELF",
    "access_state", "courses_citing", "forget_item", "forget_user", "join", "joined_by", "joined_count",
    "joined_counts", "leave", "recent_joins", "members", "reconcile_path", "refresh_course",
]
