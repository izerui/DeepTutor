"""A teacher's learners once classes are retired (platform batch 2 §2.3).

Everyone, active, who joined any of the teacher's content — by themselves,
through a course or by the migration. Used where "the teacher's students"
used to mean "the students of the classes they teach": the practice-set
results, who a remedial set may go to, who a path may be handed to.
"""

from __future__ import annotations

from deeptutor.catalog import items
from deeptutor.catalog.store import db
from deeptutor.publishing import scope as scopes


def _owner(kind: str, item_id: str) -> str | None:
    # Registries only, never a book's files (review M5).
    try:
        return items.owner(kind, item_id)
    except Exception:
        return None


def learners_of(teacher_id: str) -> set[str]:
    return set(where_learners_are(teacher_id))


def where_learners_are(teacher_id: str) -> dict[str, tuple[str, str]]:
    """Each active learner of the teacher's content -> one (kind, id) they joined; courses and paths first."""
    from deeptutor.classes.service import _accounts

    with db.read() as conn:
        if conn is None:
            return {}
        rows = conn.execute(
            "SELECT kind, item_id, user_id FROM enrollments WHERE left_at IS NULL"
        ).fetchall()
    owners: dict[tuple[str, str], str | None] = {}
    found: dict[str, tuple[str, str]] = {}
    rank = {scopes.COURSE: 0, scopes.MASTERY_PATH: 1}
    for row in rows:
        key = (row["kind"], row["item_id"])
        if key not in owners:
            owners[key] = _owner(*key)
        if owners[key] != teacher_id:
            continue
        current = found.get(row["user_id"])
        if current is None or rank.get(key[0], 9) < rank.get(current[0], 9):
            found[row["user_id"]] = key
    accounts = _accounts()
    found.pop(teacher_id, None)
    return {u: key for u, key in found.items() if u in accounts and not accounts[u]["disabled"]}


__all__ = ["learners_of", "where_learners_are"]
