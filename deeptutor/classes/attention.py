"""Students who need a teacher's attention (spec 2026-11-03 batch 3 §5.3).

For the students of the classes a teacher teaches, from data that teacher
may already see:

* ``inactive``: nothing learned in 7 days — no chat turn, Mastery Path
  answer, practice submission, task completion or hand-in (times only);
* ``overdue``: this teacher's tasks past their deadline and not done;
* ``mistakes_rising``: on this teacher's paths and practice sets, at least 5
  wrong answers in the last 7 days and at least twice the 7 days before;
* ``stuck``: a knowledge point of this teacher's path not mastered, first
  answered 7 or more days ago, answered wrong 3 or more times.

One place computes them, for the workbench and the class report alike;
results are kept for 10 minutes per teacher.
"""

from __future__ import annotations

from contextlib import closing
import json
import sqlite3
import threading
import time
from typing import Any

DAY = 86400
INACTIVE_DAYS = 7
RISING_MIN = 5
RISING_FACTOR = 2
STUCK_DAYS = 7
STUCK_WRONG = 3
CACHE_SECONDS = 600

_cache: dict[str, tuple[float, dict[str, list[dict[str, Any]]]]] = {}
_lock = threading.Lock()


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _now() -> float:
    return time.time()


def _chat_last_turn(student_id: str) -> float | None:
    from deeptutor.classes.report import _read_only, _student_paths

    conn = _read_only(_student_paths(student_id).get_chat_history_db())
    if conn is None:
        return None
    with closing(conn):
        try:
            row = conn.execute("SELECT MAX(created_at) FROM turns").fetchone()
        except sqlite3.Error:
            return None
    return float(row[0]) if row and row[0] else None


def _system_last_activity(student_ids: list[str]) -> dict[str, float]:
    """Practice submissions, task completions and hand-ins, latest per student."""
    from deeptutor.practice_sets import store as practice_store
    from deeptutor.tasks.store import db as task_db

    latest: dict[str, float] = {}

    def keep(student_id: str, stamp: float | None) -> None:
        if stamp and stamp > latest.get(student_id, 0):
            latest[student_id] = float(stamp)

    marks = ",".join("?" for _ in student_ids)
    with practice_store.read() as conn:
        if conn is not None:
            for row in conn.execute(
                f"SELECT student_id, MAX(submitted_at) FROM practice_submissions WHERE student_id IN ({marks}) "
                "GROUP BY student_id",
                student_ids,
            ):
                keep(row[0], row[1])
    with task_db.read() as conn:
        if conn is not None:
            for table, column in (("task_completions", "completed_at"), ("task_submissions", "submitted_at")):
                for row in conn.execute(
                    f"SELECT student_id, MAX({column}) FROM {table} WHERE student_id IN ({marks}) "  # nosec B608
                    "GROUP BY student_id",
                    student_ids,
                ):
                    keep(row[0], row[1])
    return latest


def _practice_wrong_times(teacher_id: str, student_ids: list[str]) -> dict[str, list[float]]:
    """When each student answered wrong on this teacher's practice sets."""
    from deeptutor.practice_sets import store as practice_store

    out: dict[str, list[float]] = {}
    marks = ",".join("?" for _ in student_ids)
    with practice_store.read() as conn:
        if conn is None:
            return out
        for row in conn.execute(
            f"SELECT s.student_id, s.submitted_at, s.results_json FROM practice_submissions s "
            f"JOIN practice_sets p ON p.id = s.set_id WHERE p.owner_user_id = ? AND s.student_id IN ({marks})",
            (teacher_id, *student_ids),
        ):
            wrong = sum(1 for r in json.loads(row["results_json"]) if r.get("result") == "incorrect")
            out.setdefault(row["student_id"], []).extend([float(row["submitted_at"])] * wrong)
    return out


def _overdue(student_id: str, username: str, teacher_id: str) -> int:
    from deeptutor.multi_user.models import CurrentUser
    from deeptutor.multi_user.paths import scope_for_user
    from deeptutor.tasks.service import StudentTasks

    actor = CurrentUser(student_id, username, "student", scope_for_user(student_id, is_admin=False))
    try:
        entries = StudentTasks(actor).assigned()
    except Exception:
        return 0
    return sum(1 for e in entries if e.get("state") == "overdue" and e.get("teacher_id") == teacher_id)


def student_flags(
    student_id: str, username: str, teacher_id: str, my_templates: set[str], *,
    system_last: float | None, practice_wrong: list[float],
) -> list[dict[str, Any]]:
    from deeptutor.classes.report import _snapshots
    from deeptutor.learning.policy import is_mastered

    now = _now()
    flags: list[dict[str, Any]] = []
    stamps = [system_last, _chat_last_turn(student_id)]
    wrong_times = list(practice_wrong)
    stuck: list[dict[str, Any]] = []
    for progress, _topic in _snapshots(student_id):
        attempts = [a for a in progress.quiz_attempts if not getattr(a, "voided", False)]
        stamps.extend(float(a.timestamp) for a in attempts if getattr(a, "timestamp", None))
        origin = getattr(progress, "origin", None)
        if origin is None or origin.template_id not in my_templates:
            continue
        by_point: dict[str, list] = {}
        for attempt in attempts:
            result = str(getattr(attempt, "result", "") or ("correct" if attempt.is_correct else "incorrect"))
            if result == "incorrect" and getattr(attempt, "timestamp", None):
                wrong_times.append(float(attempt.timestamp))
            by_point.setdefault(attempt.knowledge_point_id, []).append((attempt, result))
        for module in progress.modules:
            for kp in module.knowledge_points:
                tried = by_point.get(kp.id, [])
                if not tried or is_mastered(progress, kp):
                    continue
                first = min(float(a.timestamp) for a, _ in tried if getattr(a, "timestamp", None)) if any(
                    getattr(a, "timestamp", None) for a, _ in tried) else None
                wrong = sum(1 for _, r in tried if r == "incorrect")
                if first and now - first >= STUCK_DAYS * DAY and wrong >= STUCK_WRONG:
                    stuck.append({"kp_id": kp.id, "name": kp.name, "days": int((now - first) // DAY),
                                  "wrong": wrong, "template_id": origin.template_id})
    last = max((s for s in stamps if s), default=None)
    if last is None or now - last >= INACTIVE_DAYS * DAY:
        flags.append({"kind": "inactive", "days": int((now - last) // DAY) if last else None})
    overdue = _overdue(student_id, username, teacher_id)
    if overdue:
        flags.append({"kind": "overdue", "count": overdue})
    recent = sum(1 for t in wrong_times if now - t < 7 * DAY)
    before = sum(1 for t in wrong_times if 7 * DAY <= now - t < 14 * DAY)
    if recent >= RISING_MIN and recent >= RISING_FACTOR * before:
        flags.append({"kind": "mistakes_rising", "recent": recent, "before": before})
    for item in sorted(stuck, key=lambda s: -s["days"]):
        flags.append({"kind": "stuck", **item})
    return flags


def for_teacher(teacher_id: str, student_ids: list[str] | None = None) -> dict[str, list[dict[str, Any]]]:
    """``{student_id: [flag, …]}`` for the teacher's students (or the given ones among them)."""
    from deeptutor.classes.service import _accounts, teacher_student_ids
    from deeptutor.teaching.store import TeachingStore

    now = _now()
    with _lock:
        cached = _cache.get(teacher_id)
    if cached and now - cached[0] < CACHE_SECONDS:
        result = cached[1]
    else:
        accounts = _accounts()
        students = sorted(
            s for s in teacher_student_ids(teacher_id)
            if (accounts.get(s) or {}).get("role") == "student" and not accounts[s]["disabled"]
        )
        my_templates = {t.id for t in TeachingStore().list_templates(owner_user_id=teacher_id)}
        system_last = _system_last_activity(students) if students else {}
        wrong = _practice_wrong_times(teacher_id, students) if students else {}
        result = {
            s: student_flags(
                s, accounts[s]["username"], teacher_id, my_templates,
                system_last=system_last.get(s), practice_wrong=wrong.get(s, []),
            )
            for s in students
        }
        with _lock:
            _cache[teacher_id] = (now, result)
    if student_ids is None:
        return result
    wanted = set(student_ids)
    return {s: f for s, f in result.items() if s in wanted}


__all__ = ["clear_cache", "for_teacher", "student_flags"]
