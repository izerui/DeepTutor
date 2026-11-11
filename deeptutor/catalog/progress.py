"""How far learners are in the market's content (spec 2026-11-11 §1, §2, §5).

Everything is worked out live from what each kind of content already keeps;
nothing new is stored:

* course: its lessons the learner completed, of all its lessons (plain
  instruction lessons count only when the course has nothing else);
* practice set: how often the learner submitted it and the best score; done
  once submitted;
* Mastery Path: knowledge points mastered, of all; done when all are;
* book: pages read, of all pages; done when every page was read;
* knowledge base: no progress, never done.

The viewer's own progress reads their own stores. The author's "finished"
count reads the joiners' records read-only (like the learning reports) and
never creates a store for someone who has not started.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from deeptutor.multi_user.models import CurrentUser
from deeptutor.publishing import scope as scopes

logger = logging.getLogger(__name__)

UNITS = {
    scopes.COURSE: "lessons",
    scopes.PRACTICE_SET: "submissions",
    scopes.MASTERY_PATH: "points",
    scopes.BOOK: "pages",
}
#: What has a progress and a "finished"; knowledge bases are for looking things up.
KINDS = tuple(UNITS)


def _result(kind: str, done: int, total: int, finished: bool, **extra: Any) -> dict[str, Any]:
    percent = round(min(100.0, done * 100.0 / total)) if total else 0
    return {"done": done, "total": total, "unit": UNITS[kind], "percent": percent, "finished": finished, **extra}


# ── courses ────────────────────────────────────────────────


def _lessons(course_id: str) -> list[dict[str, Any]]:
    """The course's published lessons that count: everything but plain instructions, if it has any."""
    from deeptutor.tasks.store import db as task_db

    with task_db.read() as conn:
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT t.id, t.kind, t.target_id, t.settings_json FROM course_items i JOIN tasks t ON t.id = i.task_id "
            "WHERE i.course_id = ? AND t.status = 'published' ORDER BY i.position",
            (course_id,),
        ).fetchall()
    lessons = [
        {"id": r["id"], "kind": r["kind"], "target_id": r["target_id"] or "",
         "settings": json.loads(r["settings_json"] or "{}")}
        for r in rows
    ]
    return [lesson for lesson in lessons if lesson["kind"] != "note"] or lessons


def _completed(task_ids: list[str], learner_ids: list[str]) -> dict[str, set[str]]:
    """learner -> the lessons among *task_ids* recorded as completed."""
    from deeptutor.tasks.store import db as task_db

    out: dict[str, set[str]] = {u: set() for u in learner_ids}
    if not task_ids or not learner_ids:
        return out
    task_marks = ",".join("?" for _ in task_ids)
    for start in range(0, len(learner_ids), 400):
        chunk = learner_ids[start:start + 400]
        user_marks = ",".join("?" for _ in chunk)
        with task_db.read() as conn:
            if conn is None:
                return out
            for row in conn.execute(
                f"SELECT task_key, student_id FROM task_completions WHERE task_key IN ({task_marks}) "  # nosec B608
                f"AND student_id IN ({user_marks})",
                (*task_ids, *chunk),
            ):
                out[row[1]].add(row[0])
    return out


def _course_mine(course_id: str, viewer: CurrentUser) -> dict[str, Any]:
    from deeptutor.tasks.service import _completed_now

    lessons = _lessons(course_id)
    recorded = _completed([lesson["id"] for lesson in lessons], [viewer.id])[viewer.id]
    done = sum(
        1 for lesson in lessons
        if lesson["id"] in recorded or (
            lesson["kind"] != "note" and _completed_now(lesson["kind"], lesson["target_id"], lesson["settings"], viewer)
        )
    )
    return _result(scopes.COURSE, done, len(lessons), bool(lessons) and done == len(lessons))


def _course_finished(course_id: str, learners: list[str]) -> int:
    """Learners who completed every lesson: recorded completions, and practice sets they submitted."""
    lessons = _lessons(course_id)
    if not lessons or not learners:
        return 0
    done = _completed([lesson["id"] for lesson in lessons], learners)
    sets = {lesson["target_id"]: lesson["id"] for lesson in lessons if lesson["kind"] == "practice_set" and lesson["target_id"]}
    if sets:
        for learner, set_id in _submitters(list(sets), learners):
            done[learner].add(sets[set_id])
    wanted = {lesson["id"] for lesson in lessons}
    return sum(1 for learner in learners if wanted <= done[learner])


# ── practice sets ──────────────────────────────────────────


def _submitters(set_ids: list[str], learners: list[str]) -> set[tuple[str, str]]:
    """(learner, set) pairs where the learner submitted the set at least once."""
    from deeptutor.practice_sets import store as practice_store

    found: set[tuple[str, str]] = set()
    if not set_ids or not learners:
        return found
    set_marks = ",".join("?" for _ in set_ids)
    for start in range(0, len(learners), 400):
        chunk = learners[start:start + 400]
        user_marks = ",".join("?" for _ in chunk)
        with practice_store.read() as conn:
            if conn is None:
                return found
            found |= {
                (r[0], r[1]) for r in conn.execute(
                    f"SELECT DISTINCT student_id, set_id FROM practice_submissions WHERE set_id IN ({set_marks}) "  # nosec B608
                    f"AND student_id IN ({user_marks})",
                    (*set_ids, *chunk),
                )
            }
    return found


def _practice_mine(set_id: str, viewer: CurrentUser) -> dict[str, Any]:
    from deeptutor.practice_sets import store as practice_store
    from deeptutor.practice_sets.service import counted_submission

    with practice_store.read() as conn:
        row = conn.execute("SELECT * FROM practice_sets WHERE id = ?", (set_id,)).fetchone() if conn else None
        mine = conn.execute(
            "SELECT score, max_score FROM practice_submissions WHERE set_id = ? AND student_id = ? ORDER BY submitted_at",
            (set_id, viewer.id),
        ).fetchall() if conn else []
    mode = (row["score_mode"] if row is not None and "score_mode" in row.keys() else "best") or "best"
    counted = counted_submission(mine, mode)
    submitted = bool(mine)
    return _result(
        scopes.PRACTICE_SET, int(submitted), 1, submitted, submissions=len(mine),
        best_score=counted["score"] if counted else None, max_score=counted["max_score"] if counted else None,
    )


# ── Mastery Paths ──────────────────────────────────────────


def _path_mine(template_id: str, viewer: CurrentUser) -> dict[str, Any]:
    from deeptutor.tasks.service import path_counts, ratio_met

    counts = path_counts(template_id, viewer)
    if counts is None:
        # Not opened yet: its copy is made on first opening, nothing mastered.
        from deeptutor.catalog.reports import _revision_points

        return _result(scopes.MASTERY_PATH, 0, len(_revision_points(template_id)), False)
    return _result(scopes.MASTERY_PATH, counts["mastered"], counts["total"], ratio_met(counts))


def _path_finished(template_id: str, learners: list[str]) -> int:
    from deeptutor.catalog.learner_data import _snapshots
    from deeptutor.learning import policy
    from deeptutor.tasks.service import ratio_met

    finished = 0
    for learner in learners:
        try:
            for progress, _topic in _snapshots(learner):
                origin = getattr(progress, "origin", None)
                if origin is not None and origin.template_id == template_id:
                    if ratio_met(policy.map_summary(progress)["counts"]):
                        finished += 1
                    break
        except Exception:
            logger.debug("Could not read the path progress of %s", learner, exc_info=True)
    return finished


# ── books ──────────────────────────────────────────────────


def _book_page_ids(book_id: str) -> set[str]:
    """Every page of the book, from its spine."""
    from deeptutor.book import registry as book_registry
    from deeptutor.multi_user.book_access import storage_for

    record = book_registry.get_book(book_id)
    if record is None:
        return set()
    try:
        spine = storage_for(record).load_spine(book_id)
    except Exception:
        return set()
    return {page for chapter in (spine.chapters if spine else []) for page in chapter.page_ids}


def _visited(path_service, book_id: str) -> set[str]:
    """Pages read in a shared book, from the reader's own progress file (read only)."""
    from deeptutor.book.learning_overlay import BookLearningOverlay

    progress = BookLearningOverlay(path_service).load_progress(book_id)
    return set(progress.visited_page_ids) if progress else set()


def _book_mine(book_id: str, viewer: CurrentUser) -> dict[str, Any]:
    from deeptutor.multi_user.paths import get_current_path_service

    pages = _book_page_ids(book_id)
    done = len(_visited(get_current_path_service(), book_id) & pages)
    return _result(scopes.BOOK, done, len(pages), bool(pages) and done == len(pages))


def _book_finished(book_id: str, learners: list[str]) -> int:
    from deeptutor.catalog.learner_data import _learner_paths

    pages = _book_page_ids(book_id)
    if not pages:
        return 0
    finished = 0
    for learner in learners:
        try:
            if pages <= _visited(_learner_paths(learner), book_id):
                finished += 1
        except Exception:
            logger.debug("Could not read the book progress of %s", learner, exc_info=True)
    return finished


# ── entry points ───────────────────────────────────────────

_MINE = {
    scopes.COURSE: _course_mine,
    scopes.PRACTICE_SET: _practice_mine,
    scopes.MASTERY_PATH: _path_mine,
    scopes.BOOK: _book_mine,
}


def mine(viewer: CurrentUser, kind: str, item_id: str) -> dict[str, Any] | None:
    """The viewer's own progress in an item; ``None`` for knowledge bases (or when it cannot be read)."""
    reader = _MINE.get(kind)
    if reader is None:
        return None
    try:
        return reader(item_id, viewer)
    except Exception:
        logger.exception("Could not work out the progress in %s %s", kind, item_id)
        return None


def learners(kind: str, item_id: str, owner: str) -> list[str]:
    """Who is in the item now, its author aside, active accounts only (as the reports count)."""
    from deeptutor.catalog.enrollment import members
    from deeptutor.multi_user.accounts import accounts as _accounts

    accounts = _accounts()
    return sorted(u for u in members(kind, item_id) - {owner} if u in accounts and not accounts[u]["disabled"])


def finished_count(kind: str, item_id: str, owner: str) -> int | None:
    """How many of the item's learners finished it; ``None`` for knowledge bases."""
    if kind not in KINDS:
        return None
    people = learners(kind, item_id, owner)
    if not people:
        return 0
    try:
        if kind == scopes.PRACTICE_SET:
            return len({learner for learner, _set in _submitters([item_id], people)})
        if kind == scopes.COURSE:
            return _course_finished(item_id, people)
        if kind == scopes.MASTERY_PATH:
            return _path_finished(item_id, people)
        return _book_finished(item_id, people)
    except Exception:
        logger.exception("Could not count who finished %s %s", kind, item_id)
        return 0


def reviewed_by(user_id: str) -> set[tuple[str, str]]:
    """The items the user wrote a review of."""
    from deeptutor.catalog.store import db

    with db.read() as conn:
        if conn is None:
            return set()
        try:
            return {(r[0], r[1]) for r in conn.execute("SELECT kind, item_id FROM reviews WHERE user_id = ?", (user_id,))}
        except Exception:
            return set()


def finished_unreviewed(viewer: CurrentUser) -> list[dict[str, str]]:
    """Spec §2: what the viewer joined, finished and has not reviewed yet, of what is in the market."""
    from deeptutor.catalog.enrollment import joined_by
    from deeptutor.catalog.store import db
    from deeptutor.multi_user.ownership import owns

    reviewed = reviewed_by(viewer.id)
    keys = [key for key in joined_by(viewer.id) if key[0] in KINDS and key not in reviewed]
    if not keys:
        return []
    with db.read() as conn:
        rows = conn.execute(
            "SELECT kind, item_id, owner_user_id FROM listings WHERE status = 'listed' AND blocked = 0"
        ).fetchall() if conn else []
    listed = {(r["kind"], r["item_id"]): r["owner_user_id"] for r in rows}
    out = []
    for kind, item_id in sorted(keys):
        owner = listed.get((kind, item_id))
        if owner is None or owns(owner, viewer):
            continue
        progress = mine(viewer, kind, item_id)
        if progress and progress["finished"]:
            out.append({"kind": kind, "id": item_id})
    return out


__all__ = ["KINDS", "UNITS", "finished_count", "finished_unreviewed", "learners", "mine", "reviewed_by"]
