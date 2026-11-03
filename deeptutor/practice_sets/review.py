"""The author's review of a practice set (spec 2026-11-03 batch 3 §6.1).

* one attempt question by question, with the subjective answers' text and
  the option picked — kept for the author only (§2.1);
* changing one question's points; the total follows and the student is told;
  their question bank catches up when they next open the set;
* how many picked each option;
* the results as CSV.
"""

from __future__ import annotations

import csv
import io
import json
import time
from typing import Any

from deeptutor.practice_sets import store
from deeptutor.practice_sets.service import (
    OPTION_TYPES,
    PracticeError,
    PracticeNotFound,
    PracticeSetService,
    _col,
)


def option_counts(question: dict[str, Any], submissions) -> dict[str, int] | None:
    """Picks per option over the given attempts; ``None`` for questions without options."""
    kind = question["question_type"]
    if kind not in OPTION_TYPES:
        return None
    keys = ["TRUE", "FALSE"] if kind == "concept" else sorted(question["options"])
    counts = {key: 0 for key in keys}
    for submission in submissions:
        picked = json.loads(_col(submission, "answers_json", "{}") or "{}").get(question["question_id"])
        if not picked:
            continue
        for key in ([picked] if kind == "concept" else list(picked)):
            if key in counts:
                counts[key] += 1
    return counts


class PracticeReview:
    """The set's author only (administrators see no answers, R21-like)."""

    def __init__(self, actor=None) -> None:
        self.service = PracticeSetService(actor)
        self.actor = self.service.actor

    def _submission(self, conn, set_id: str, submission_id: str):
        self.service._require_author()
        row = self.service._owned(conn, set_id)
        submission = conn.execute(
            "SELECT * FROM practice_submissions WHERE id = ? AND set_id = ?", (submission_id, set_id)
        ).fetchone()
        if submission is None:
            raise PracticeNotFound("Submission not found.")
        revision = conn.execute(
            "SELECT * FROM practice_set_revisions WHERE set_id = ? AND revision = ?",
            (set_id, submission["revision"]),
        ).fetchone()
        return row, submission, revision

    def attempt(self, set_id: str, submission_id: str) -> dict[str, Any]:
        """One attempt, question by question."""
        from deeptutor.classes.service import _accounts

        with store.read() as conn:
            _row, submission, revision = self._submission(conn, set_id, submission_id)
        questions = {q["question_id"]: q for q in json.loads(revision["questions_json"])}
        answers = json.loads(_col(submission, "answers_json", "{}") or "{}")
        account = _accounts().get(submission["student_id"]) or {}
        return {
            "id": submission["id"],
            "set_id": set_id,
            "revision": submission["revision"],
            "student_id": submission["student_id"],
            "username": account.get("username", ""),
            "submitted_at": submission["submitted_at"],
            "late": bool(submission["late"]),
            "score": submission["score"],
            "max_score": submission["max_score"],
            "regraded_at": _col(submission, "regraded_at"),
            "questions": [
                {
                    "question_id": r["question_id"],
                    "question": questions.get(r["question_id"], {}).get("question", ""),
                    "question_type": questions.get(r["question_id"], {}).get("question_type", ""),
                    "options": questions.get(r["question_id"], {}).get("options", {}),
                    "correct_answer": questions.get(r["question_id"], {}).get("correct_answer", ""),
                    "result": r["result"],
                    "points": r["points"],
                    "max_points": r.get("max_points", questions.get(r["question_id"], {}).get("points", 0)),
                    "answer": answers.get(r["question_id"]),
                    "regraded": bool(r.get("regraded")),
                }
                for r in json.loads(submission["results_json"])
            ],
        }

    def regrade(self, set_id: str, submission_id: str, question_id: str, points: float) -> dict[str, Any]:
        """Set one question's points; its result and the total follow (§6.1)."""
        with store.write() as conn:
            row, submission, _revision = self._submission(conn, set_id, submission_id)
            rows = json.loads(submission["results_json"])
            target = next((r for r in rows if r["question_id"] == question_id), None)
            if target is None:
                raise PracticeNotFound("Question not found.")
            maximum = float(target.get("max_points") or 0)
            try:
                points = float(points)
            except (TypeError, ValueError):
                raise PracticeError("Points must be a number.") from None
            if not 0 <= points <= maximum:
                raise PracticeError(f"Give 0 to {maximum:g} points.")
            target["points"] = points
            target["result"] = "correct" if points >= maximum else "incorrect" if points == 0 else "partial"
            target["regraded"] = True
            score = sum(float(r["points"] or 0) for r in rows)
            conn.execute(
                "UPDATE practice_submissions SET results_json = ?, score = ?, regraded_at = ? WHERE id = ?",
                (json.dumps(rows), score, time.time(), submission_id),
            )
        from deeptutor.notifications.service import notify

        notify(
            [submission["student_id"]], "practice_regraded", {"title": row["title"]},
            link=f"/practice-sets?set={set_id}", dedupe_key=f"practice_regraded:{submission_id}", bump=True,
        )
        return self.attempt(set_id, submission_id)

    def export_csv(self, set_id: str) -> str:
        """Every reported student's results, one row each, with each question's result."""
        report = self.service.report(set_id)
        with store.read() as conn:
            submissions = conn.execute(
                "SELECT student_id, results_json FROM practice_submissions WHERE set_id = ? ORDER BY submitted_at",
                (set_id,),
            ).fetchall()
        first: dict[str, dict[str, str]] = {}
        for submission in submissions:
            if submission["student_id"] not in first:
                first[submission["student_id"]] = {
                    r["question_id"]: r["result"] for r in json.loads(submission["results_json"])
                }
        out = io.StringIO()
        writer = csv.writer(out)
        questions = report["questions"]
        writer.writerow([
            "student", "in_scope", "attempts", "first_score", "best_score", "counted_score", "max_score",
            "minutes", "late", "submitted_at", *[f"Q{i + 1}" for i in range(len(questions))],
        ])
        for student in report["students"]:
            results = first.get(student["id"], {})
            writer.writerow([
                student["username"], "yes" if student["in_scope"] else "no", student["attempts"],
                _number(student["first_score"]), _number(student["best_score"]), _number(student["score"]),
                _number(student["max_score"]), _number(student["minutes"]), "yes" if student["late"] else "no",
                time.strftime("%Y-%m-%d %H:%M", time.localtime(student["submitted_at"])) if student["submitted_at"] else "",
                *[results.get(q["question_id"], "") for q in questions],
            ])
        return out.getvalue()


def _number(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value):g}"


def forget_student_answers(student_id: str) -> int:
    """A deleted student's answers kept for review go with the account (batch 3 §11)."""
    if not store.practice_db_path().exists():
        return 0
    with store.write() as conn:
        return int(conn.execute(
            "UPDATE practice_submissions SET answers_json = '{}' WHERE student_id = ?", (student_id,)
        ).rowcount)


__all__ = ["PracticeReview", "forget_student_answers", "option_counts"]
