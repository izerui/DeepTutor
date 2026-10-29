"""Teacher-facing learning reports (spec 2026-10-29 D17, option B).

A teacher sees, for students in the classes they teach:

* progress, scores and mistakes on the Mastery Paths *this teacher* published
  and assigned (titles and knowledge points are the teacher's own text), and
* an overall summary of the student's learning, self-study included: study
  time, how many knowledge points are mastered, quiz and question-bank tallies.

Only numbers, timestamps and teacher-authored names leave the student's
storage. Conversation text, the student's own answers, notes, self-study path
titles and AI feedback are never read into a report. Every store is opened
read-only; a student who never studied has nothing created for them. Reports
cover the student's default space (where assigned paths live).
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import time
from typing import Any

from deeptutor.classes import store
from deeptutor.classes.service import ClassForbidden, ClassNotFound, _accounts, _role
from deeptutor.multi_user.context import get_current_user
from deeptutor.multi_user.models import CurrentUser

#: A single turn longer than this is treated as an idle tab, not study.
_TURN_CAP_SECONDS = 30 * 60
_WEEK_SECONDS = 7 * 86400


def _student_paths(student_id: str):
    from deeptutor.multi_user.paths import get_path_service_for_scope, scope_for_user

    return get_path_service_for_scope(scope_for_user(student_id, is_admin=False))


def _read_only(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _snapshots(student_id: str) -> list[tuple[Any, Any]]:
    """(progress, topic) of the student's active paths, without creating a store."""
    from deeptutor.learning.migration import mastery_v2_root
    from deeptutor.learning.storage import LearningStore

    learning_root = _student_paths(student_id).get_workspace_dir() / "learning"
    root = mastery_v2_root(learning_root)
    if not (root / LearningStore._DB_FILENAME).is_file():
        return []
    return [(progress, topic) for progress, topic, _count, _ in LearningStore(root).list_topic_snapshots()]


def _path_numbers(progress) -> dict[str, Any]:
    from deeptutor.learning.policy import display_mastery, is_mastered

    points = [kp for module in progress.modules for kp in module.knowledge_points]
    attempts = [a for a in progress.quiz_attempts if not getattr(a, "voided", False)]
    tally = {"correct": 0, "partial": 0, "incorrect": 0}
    for attempt in attempts:
        result = str(getattr(attempt, "result", "") or ("correct" if attempt.is_correct else "incorrect"))
        if result in tally:
            tally[result] += 1
    timestamps = [float(a.timestamp) for a in attempts if getattr(a, "timestamp", None)]
    return {
        "kp_total": len(points),
        "kp_mastered": sum(1 for kp in points if is_mastered(progress, kp)),
        "average_mastery": round(
            sum(display_mastery(progress, kp) for kp in points) / len(points), 3
        ) if points else 0.0,
        "attempts": tally,
        "last_activity": max(timestamps) if timestamps else None,
        "_points": points,
    }


def _question_bank(student_id: str) -> dict[str, Any]:
    db = _student_paths(student_id).get_chat_history_db()
    conn = _read_only(db)
    tally = {"total": 0, "correct": 0, "partial": 0, "incorrect": 0}
    study = {"study_minutes_total": 0, "study_minutes_7d": 0}
    if conn is None:
        return {"question_bank": tally, **study}
    with closing(conn):
        try:
            for row in conn.execute("SELECT result, COUNT(*) AS n FROM notebook_entries GROUP BY result"):
                tally["total"] += row["n"]
                if row["result"] in tally:
                    tally[row["result"]] += row["n"]
        except sqlite3.Error:
            pass
        try:
            week_ago = time.time() - _WEEK_SECONDS
            row = conn.execute(
                "SELECT "
                "COALESCE(SUM(MIN(finished_at - created_at, ?)), 0) AS total, "
                "COALESCE(SUM(CASE WHEN created_at >= ? THEN MIN(finished_at - created_at, ?) END), 0) AS week "
                "FROM turns WHERE finished_at IS NOT NULL AND finished_at > created_at",
                (_TURN_CAP_SECONDS, week_ago, _TURN_CAP_SECONDS),
            ).fetchone()
            study = {
                "study_minutes_total": int(row["total"] // 60),
                "study_minutes_7d": int(row["week"] // 60),
            }
        except sqlite3.Error:
            pass
    return {"question_bank": tally, **study}


class ReportService:
    def __init__(self, actor: CurrentUser | None = None) -> None:
        self.actor = actor or get_current_user()

    def _require_teacher(self) -> None:
        # Spec D17: reports are for teachers; administrators do not read
        # students' learning data (D2).
        if self.actor.is_admin or _role(self.actor) != "teacher":
            raise ClassForbidden("Only a class's teachers see its learning reports.")

    def _class_students(self, class_id: str) -> tuple[str, list[str]]:
        with store.read() as conn:
            if conn is None:
                raise ClassNotFound("Class not found.")
            row = conn.execute("SELECT name FROM classes WHERE id = ?", (class_id,)).fetchone()
            if row is None:
                raise ClassNotFound("Class not found.")
            teaches = conn.execute(
                "SELECT 1 FROM class_teachers WHERE class_id = ? AND user_id = ?",
                (class_id, self.actor.id),
            ).fetchone()
            if not teaches:
                raise ClassForbidden("Only a class's teachers see its learning reports.")
            students = [
                r["student_id"] for r in conn.execute(
                    "SELECT student_id FROM class_members WHERE class_id = ? ORDER BY joined_at",
                    (class_id,),
                )
            ]
        # Only accounts that are students now: a promoted student's learning
        # data is theirs alone (D2), whatever rows linger.
        accounts = _accounts()
        students = [
            s for s in students
            if (accounts.get(s) or {}).get("role") == "student" and not accounts[s]["disabled"]
        ]
        return row["name"], students

    def _my_practice_results(self, student_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        """D17: each student's results on this teacher's practice sets (scores only)."""
        from deeptutor.practice_sets import store as practice_store

        if not student_ids:
            return {}
        marks = ",".join("?" for _ in student_ids)
        with practice_store.read() as conn:
            if conn is None:
                return {}
            rows = conn.execute(
                f"SELECT p.id, p.title, s.student_id, s.score, s.max_score, s.late, s.submitted_at "
                f"FROM practice_submissions s JOIN practice_sets p ON p.id = s.set_id "
                f"WHERE p.owner_user_id = ? AND s.student_id IN ({marks}) "
                f"ORDER BY s.submitted_at",
                (self.actor.id, *student_ids),
            ).fetchall()
        grouped: dict[tuple[str, str], list] = {}
        for row in rows:
            grouped.setdefault((row["student_id"], row["id"]), []).append(row)
        results: dict[str, list[dict[str, Any]]] = {}
        for (student_id, set_id), mine in grouped.items():
            first = mine[0]
            results.setdefault(student_id, []).append({
                "set_id": set_id,
                "title": first["title"],
                "attempts": len(mine),
                "first_score": first["score"],
                "best_score": max(r["score"] for r in mine),
                "max_score": first["max_score"],
                "late": bool(first["late"]),
                "last_submitted_at": mine[-1]["submitted_at"],
            })
        return results

    def _my_template_ids(self) -> dict[str, str]:
        from deeptutor.teaching.store import TeachingStore

        return {
            template.id: template.title
            for template in TeachingStore().list_templates(owner_user_id=self.actor.id)
        }

    def _student_report(self, student_id: str, my_templates: dict[str, str], *, detail: bool) -> dict[str, Any]:
        snapshots = _snapshots(student_id)
        teacher_paths: list[dict[str, Any]] = []
        overall = {"path_count": len(snapshots), "self_study_path_count": 0, "kp_total": 0, "kp_mastered": 0,
                   "attempts": {"correct": 0, "partial": 0, "incorrect": 0}, "last_activity": None}
        for progress, _topic in snapshots:
            numbers = _path_numbers(progress)
            points = numbers.pop("_points")
            overall["kp_total"] += numbers["kp_total"]
            overall["kp_mastered"] += numbers["kp_mastered"]
            for key, value in numbers["attempts"].items():
                overall["attempts"][key] += value
            if numbers["last_activity"] and (
                overall["last_activity"] is None or numbers["last_activity"] > overall["last_activity"]
            ):
                overall["last_activity"] = numbers["last_activity"]
            origin = getattr(progress, "origin", None)
            if origin is None:
                overall["self_study_path_count"] += 1
                continue
            if origin.template_id not in my_templates:
                continue
            entry = {
                "template_id": origin.template_id,
                "title": my_templates[origin.template_id],
                "revision": origin.revision,
                **numbers,
            }
            if detail:
                from deeptutor.learning.policy import display_mastery, is_mastered

                entry["knowledge_points"] = [
                    {"name": kp.name, "mastery": round(display_mastery(progress, kp), 3),
                     "mastered": is_mastered(progress, kp)}
                    for kp in points
                ]
            teacher_paths.append(entry)
        overall.update(_question_bank(student_id))
        return {"teacher_paths": teacher_paths, "overall": overall}

    def class_report(self, class_id: str) -> dict[str, Any]:
        self._require_teacher()
        name, students = self._class_students(class_id)
        accounts = _accounts()
        my_templates = self._my_template_ids()
        practice = self._my_practice_results(students)
        rows = []
        for student_id in students:
            report = self._student_report(student_id, my_templates, detail=False)
            rows.append({
                "id": student_id,
                "username": (accounts.get(student_id) or {}).get("username") or student_id,
                **report,
                "practice_sets": practice.get(student_id, []),
            })
        return {"class_id": class_id, "class_name": name, "students": rows}

    def student_report(self, class_id: str, student_id: str) -> dict[str, Any]:
        self._require_teacher()
        _name, students = self._class_students(class_id)
        if student_id not in students:
            raise ClassNotFound("This student is not in the class.")
        accounts = _accounts()
        report = self._student_report(student_id, self._my_template_ids(), detail=True)
        return {
            "id": student_id,
            "username": (accounts.get(student_id) or {}).get("username") or student_id,
            **report,
            "practice_sets": self._my_practice_results([student_id]).get(student_id, []),
        }


__all__ = ["ReportService"]
