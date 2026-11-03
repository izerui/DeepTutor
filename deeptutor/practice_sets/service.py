"""Teacher practice sets (spec 2026-10-29 §8).

* A teacher (or administrator) writes a draft from AI-generated questions,
  questions picked from their own bank, or questions typed by hand (§8.2),
  then publishes it as a frozen revision (§8.5) to a publish scope (§7).
* A student in the scope answers it, as often as they like. Objective
  questions are checked exactly; the rest are judged by AI (§8.3). Every
  answer goes to the student's own question bank, tagged with the set, so a
  wrong answer becomes one of their mistakes.
* The teacher sees, for the students in the scope only, completion, first
  and best score, time taken and lateness, plus each question's error rate
  (§8.4). Of the answers, only what the author reviews is kept for them —
  subjective answers' text and the option picked (batch 3 §2.1); the
  review itself is in ``review.py``.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any
import uuid

from deeptutor.multi_user.context import get_current_user
from deeptutor.multi_user.models import CurrentUser
from deeptutor.practice_sets import store

logger = logging.getLogger(__name__)

QUESTION_TYPES = ("choice", "multi_choice", "concept", "fill_in_blank", "short_answer", "written", "coding")
REVEALS = ("after_submit", "after_due")
SCORE_MODES = ("best", "first")
#: Answers whose text the author reviews (batch 3 §2.1); choice answers keep the option only.
SUBJECTIVE_TYPES = ("short_answer", "written", "coding")
OPTION_TYPES = ("choice", "multi_choice", "concept")
ATTEMPTS_MAX = 100
#: Batch 4 §1: fewer scores than this and an average would reveal someone's.
CLASS_AVERAGE_MIN = 3
MAX_QUESTIONS = 200
TITLE_MAX = 200
#: A single answer longer than this is cut before grading and storage.
ANSWER_MAX = 20_000


class PracticeError(Exception):
    status = 400


class PracticeNotFound(PracticeError):
    status = 404


class PracticeForbidden(PracticeError):
    status = 403


class PracticeConflict(PracticeError):
    status = 409


def _now() -> float:
    return time.time()


def _role(user: CurrentUser) -> str:
    from deeptutor.classes.service import _role as live_role

    return live_role(user)


# ── questions ──────────────────────────────────────────────


def normalize_question(raw: Any, *, index: int = 0) -> dict[str, Any]:
    """One question in the stored shape (aligned with the quiz generator's ``QuizPair``)."""
    if not isinstance(raw, dict):
        raise PracticeError(f"Question {index + 1} is not valid.")
    text = str(raw.get("question") or "").strip()
    if not text:
        raise PracticeError(f"Question {index + 1} has no text.")
    kind = str(raw.get("question_type") or "short_answer").strip()
    aliases = {
        "single_choice": "choice", "multiple_choice": "choice", "multiple_select": "multi_choice",
        "true_false": "concept", "short": "short_answer", "open": "written", "essay": "written",
    }
    kind = aliases.get(kind, kind)
    if kind not in QUESTION_TYPES:
        kind = "short_answer"
    options_raw = raw.get("options") or {}
    if isinstance(options_raw, list):
        options_raw = {chr(ord("A") + i): value for i, value in enumerate(options_raw)}
    options = {
        str(key).strip().upper(): str(value).strip()
        for key, value in dict(options_raw).items()
        if str(key).strip() and str(value).strip()
    } if isinstance(options_raw, dict) else {}
    if kind in ("choice", "multi_choice") and len(options) < 2:
        raise PracticeError(f"Question {index + 1} needs at least two options.")
    if kind not in ("choice", "multi_choice"):
        options = {}
    try:
        points = float(raw.get("points", 1) or 1)
    except (TypeError, ValueError):
        points = 1.0
    return {
        "question_id": str(raw.get("question_id") or "").strip() or f"q_{uuid.uuid4().hex[:10]}",
        "question": text,
        "question_type": kind,
        "options": options,
        "correct_answer": str(raw.get("correct_answer") or "").strip(),
        "explanation": str(raw.get("explanation") or "").strip(),
        "points": max(0.0, min(points, 100.0)),
    }


def normalize_questions(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise PracticeError("Questions must be a list.")
    if len(raw) > MAX_QUESTIONS:
        raise PracticeError(f"A practice set holds at most {MAX_QUESTIONS} questions.")
    questions = [normalize_question(item, index=i) for i, item in enumerate(raw)]
    seen: set[str] = set()
    for question in questions:
        if question["question_id"] in seen:
            question["question_id"] = f"q_{uuid.uuid4().hex[:10]}"
        seen.add(question["question_id"])
    return questions


def _for_student(question: dict[str, Any], *, reveal: bool) -> dict[str, Any]:
    shown = {k: question[k] for k in ("question_id", "question", "question_type", "options", "points")}
    if reveal:
        shown["correct_answer"] = question["correct_answer"]
        shown["explanation"] = question["explanation"]
    return shown


# ── grading (§8.3) ─────────────────────────────────────────

_TRUE = {"TRUE", "T", "YES", "Y", "对", "正确", "是", "√", "1"}
_FALSE = {"FALSE", "F", "NO", "N", "错", "错误", "否", "×", "0"}


def _truth(value: str) -> bool | None:
    folded = value.strip().upper().rstrip("。.")
    return True if folded in _TRUE else False if folded in _FALSE else None


def exact_result(question: dict[str, Any], answer: str) -> str | None:
    """``correct``/``incorrect`` when an exact check decides; ``None`` leaves it to AI."""
    if not answer.strip():
        return "incorrect"
    kind = question["question_type"]
    expected = question["correct_answer"]
    if kind in ("choice", "multi_choice"):
        from deeptutor.services.practice.answers import check_answer

        verdict = check_answer(
            {"question_type": "multi_choice" if kind == "multi_choice" else "choice",
             "options": question["options"], "correct_answer": expected},
            answer,
        )
        return None if verdict is None else "correct" if verdict else "incorrect"
    if kind == "concept":
        want, got = _truth(expected), _truth(answer)
        if want is None or got is None:
            return None
        return "correct" if want == got else "incorrect"
    if kind == "fill_in_blank" and expected:
        from deeptutor.learning.grading import grade_answer

        # A match is final; a mismatch may still be an equivalent answer.
        return "correct" if grade_answer(answer, expected, "short") else None
    return None


async def _judge(pending: list[tuple[dict[str, Any], str]]) -> dict[str, dict[str, Any]]:
    """AI verdicts in groups of four; an unavailable judge leaves answers ungraded."""
    from deeptutor.learning.semantic_assessment import AssessmentUnavailable, judge_answers

    verdicts: dict[str, dict[str, Any]] = {}
    for start in range(0, len(pending), 4):
        group = pending[start:start + 4]
        items = [
            {
                "question_id": question["question_id"],
                "question": question["question"],
                "question_type": question["question_type"],
                "options": question["options"],
                "expected_answer": question["correct_answer"],
                "explanation": question["explanation"],
                "learner_answer": answer,
            }
            for question, answer in group
        ]
        from deeptutor.services.llm.usage_limit import exempt

        try:
            # A teacher's set is graded even when the student's own AI is used up (batch 3 §8).
            with exempt():
                decided = await judge_answers(None, items)
        except AssessmentUnavailable:
            logger.warning("Practice set grading is unavailable for %d answers", len(group))
            continue
        for question_id, verdict in decided.items():
            receipt = verdict.receipt()
            verdicts[question_id] = {"result": receipt["result"], "feedback": receipt["feedback"]}
    return verdicts


def _points(question: dict[str, Any], result: str) -> float:
    return question["points"] if result == "correct" else question["points"] / 2 if result == "partial" else 0.0


def _col(row, name: str, default: Any = None) -> Any:
    """A column added later (batch 3), read safely from a database not yet migrated."""
    return row[name] if name in row.keys() else default


def reviewable_answer(question: dict[str, Any], answer: str) -> str | None:
    """What of one answer is kept for the author (batch 3 §2.1), or ``None``."""
    kind = question["question_type"]
    if kind in SUBJECTIVE_TYPES:
        return answer
    if kind in ("choice", "multi_choice"):
        letters = sorted({ch for ch in answer.upper() if ch in question["options"]})
        return "".join(letters) or None
    if kind == "concept":
        truth = _truth(answer)
        return None if truth is None else "TRUE" if truth else "FALSE"
    return None


def counted_submission(submissions, mode: str):
    """The attempt that is the student's score: the first, or the best (§6.1)."""
    if not submissions:
        return None
    return submissions[0] if mode == "first" else best_submission(submissions)


def _ratio(submission) -> float:
    return submission["score"] / submission["max_score"] if submission["max_score"] else 0.0


def best_submission(submissions):
    """The best attempt by share of its own version's maximum (§8.5: versions
    may change questions and points, so raw scores do not compare)."""
    return max(submissions, key=_ratio) if submissions else None


#: Shown instead of a result until the answers may be revealed.
PENDING = "pending"


def _withheld(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-question rows with nothing that tells right from wrong.

    With answers shown only after the deadline, right/wrong per question (or
    a score) would let a student find the key by resubmitting variations.
    """
    return [
        {"question_id": r["question_id"], "result": PENDING, "points": None, "max_points": r["max_points"]}
        for r in rows
    ]


def _results_only(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stored per-question rows without an answer text (a few early rows kept one)."""
    return [{k: v for k, v in r.items() if k != "answer"} for r in rows]


# ── service ────────────────────────────────────────────────


class PracticeSetService:
    def __init__(self, actor: CurrentUser | None = None) -> None:
        self.actor = actor or get_current_user()

    # authorization

    def _require_author(self) -> None:
        if _role(self.actor) not in ("teacher", "admin"):
            raise PracticeForbidden("Only teachers and administrators create practice sets.")

    def _owned(self, conn, set_id: str):
        row = conn.execute("SELECT * FROM practice_sets WHERE id = ?", (set_id,)).fetchone() if conn else None
        if row is None or row["owner_user_id"] != self.actor.id:
            raise PracticeNotFound("Practice set not found.")
        return row

    def _withdrawable(self, conn, set_id: str):
        """The author's own set, or (D10) any published set for an administrator."""
        row = conn.execute("SELECT * FROM practice_sets WHERE id = ?", (set_id,)).fetchone() if conn else None
        if row is None:
            raise PracticeNotFound("Practice set not found.")
        if row["owner_user_id"] == self.actor.id:
            return row
        if _role(self.actor) == "admin" and row["current_revision"] > 0:
            return row
        raise PracticeNotFound("Practice set not found.")

    def _reaches_me(self, set_id: str) -> bool:
        from deeptutor.publishing.scope import PRACTICE_SET, _reaches, get_scope

        scope = get_scope(PRACTICE_SET, set_id)
        return scope is not None and _role(self.actor) == "student" and _reaches(scope, self.actor.id)

    @staticmethod
    def _summary(row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "title": row["title"],
            "description": row["description"],
            "status": row["status"],
            "current_revision": row["current_revision"],
            "due_at": row["due_at"],
            "reveal": row["reveal"],
            "attempts_limit": _col(row, "attempts_limit"),
            "score_mode": _col(row, "score_mode", "best") or "best",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # authoring

    def create(self, title: str) -> dict[str, Any]:
        self._require_author()
        title = str(title or "").strip()
        if not title or len(title) > TITLE_MAX:
            raise PracticeError(f"A title must contain 1 to {TITLE_MAX} characters.")
        set_id = f"ps_{uuid.uuid4().hex[:12]}"
        now = _now()
        with store.write() as conn:
            conn.execute(
                "INSERT INTO practice_sets (id, owner_user_id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (set_id, self.actor.id, title, now, now),
            )
        return self.get(set_id)

    def list_mine(self) -> list[dict[str, Any]]:
        self._require_author()
        with store.read() as conn:
            if conn is None:
                return []
            rows = conn.execute(
                "SELECT * FROM practice_sets WHERE owner_user_id = ? ORDER BY updated_at DESC",
                (self.actor.id,),
            ).fetchall()
            counts = dict(conn.execute(
                "SELECT set_id, COUNT(DISTINCT student_id) FROM practice_submissions s "
                "JOIN practice_sets p ON p.id = s.set_id WHERE p.owner_user_id = ? GROUP BY set_id",
                (self.actor.id,),
            ).fetchall())
        return [{**self._summary(r), "submitted_students": counts.get(r["id"], 0)} for r in rows]

    def get(self, set_id: str) -> dict[str, Any]:
        """The author's view: draft questions with answers."""
        self._require_author()
        with store.read() as conn:
            row = self._owned(conn, set_id)
        return {**self._summary(row), "questions": json.loads(row["draft_questions_json"])}

    def update(
        self, set_id: str, *, title: str | None = None, description: str | None = None,
        questions: Any = None, due_at: float | None | str = "", reveal: str | None = None,
        attempts_limit: int | None | str = "", score_mode: str | None = None,
    ) -> dict[str, Any]:
        self._require_author()
        sets: list[str] = []
        values: list[Any] = []
        if attempts_limit != "":
            # Batch 3 §6.1: ``None`` lifts the limit; it counts every version's attempts.
            if attempts_limit is not None and (
                isinstance(attempts_limit, bool) or not isinstance(attempts_limit, int)
                or not 1 <= attempts_limit <= ATTEMPTS_MAX
            ):
                raise PracticeError(f"Allow 1 to {ATTEMPTS_MAX} attempts, or no limit.")
            sets.append("attempts_limit = ?")
            values.append(attempts_limit)
        if score_mode is not None:
            if score_mode not in SCORE_MODES:
                raise PracticeError("Choose which attempt counts.")
            sets.append("score_mode = ?")
            values.append(score_mode)
        if title is not None:
            title = title.strip()
            if not title or len(title) > TITLE_MAX:
                raise PracticeError(f"A title must contain 1 to {TITLE_MAX} characters.")
            sets.append("title = ?")
            values.append(title)
        if description is not None:
            sets.append("description = ?")
            values.append(description.strip()[:4000])
        if questions is not None:
            sets.append("draft_questions_json = ?")
            values.append(json.dumps(normalize_questions(questions), ensure_ascii=False))
        if due_at != "":
            sets.append("due_at = ?")
            values.append(float(due_at) if due_at is not None else None)
        if reveal is not None:
            if reveal not in REVEALS:
                raise PracticeError("Choose when answers are shown.")
            sets.append("reveal = ?")
            values.append(reveal)
        with store.write() as conn:
            row = self._owned(conn, set_id)
            new_due = (float(due_at) if due_at is not None else None) if due_at != "" else row["due_at"]
            if (
                row["reveal"] == "after_due" and row["due_at"] is not None and _now() >= row["due_at"]
                and (new_due is None or new_due > row["due_at"])
            ):
                # R10: the answers were shown at the deadline; a later one would hide nothing.
                raise PracticeConflict("The answers were already shown; the deadline can no longer move later.")
            if sets:
                conn.execute(
                    f"UPDATE practice_sets SET {', '.join(sets)}, updated_at = ? WHERE id = ?",
                    (*values, _now(), set_id),
                )
        if due_at != "" and row["status"] == "published" and new_due != row["due_at"]:
            self._notify_unsubmitted(set_id, row["title"], "task_due_changed", {"due_at": new_due})
        return self.get(set_id)

    def _notify_unsubmitted(self, set_id: str, title: str, kind: str, params: dict[str, Any]) -> None:
        """Tell the students the set reaches who have not submitted it yet."""
        from deeptutor.notifications.service import notify
        from deeptutor.publishing.scope import PRACTICE_SET, audience, get_scope

        scope = get_scope(PRACTICE_SET, set_id)
        reached = audience(scope, students_only=True, owner_id=self.actor.id) if scope else set()
        with store.read() as conn:
            done = {
                r[0] for r in conn.execute(
                    "SELECT DISTINCT student_id FROM practice_submissions WHERE set_id = ?", (set_id,)
                )
            } if conn else set()
        notify(
            reached - done, kind, {"title": title, **params},
            link=f"/start?task=practice_set:{set_id}", dedupe_key=f"{kind}:practice_set:{set_id}:{params}",
        )

    def publish(self, set_id: str) -> dict[str, Any]:
        """Freeze the draft as the next revision (§8.5); earlier submissions keep theirs."""
        self._require_author()
        with store.read() as conn:
            self._owned(conn, set_id)
        from deeptutor.publishing.scope import PRACTICE_SET, default_scope_on_publish

        # Spec 2026-11-02 §4.1: the receivers are settled before anything is
        # published (chosen, or the author's classes); without them nothing is.
        default_scope_on_publish(PRACTICE_SET, set_id)
        with store.write() as conn:
            row = self._owned(conn, set_id)
            if row["status"] == "archived":
                raise PracticeConflict("This practice set is archived.")
            questions = normalize_questions(json.loads(row["draft_questions_json"]))
            if not questions:
                raise PracticeError("Add at least one question before publishing.")
            if row["reveal"] == "after_due" and row["due_at"] is None:
                raise PracticeError("Set a deadline, or show answers right after submitting.")
            revision = row["current_revision"] + 1
            now = _now()
            conn.execute(
                "INSERT INTO practice_set_revisions VALUES (?, ?, ?, ?, ?, ?)",
                (set_id, revision, row["title"], row["description"],
                 json.dumps(questions, ensure_ascii=False), now),
            )
            conn.execute(
                "UPDATE practice_sets SET status = 'published', current_revision = ?, updated_at = ? "
                "WHERE id = ?",
                (revision, now, set_id),
            )
        if revision == 1:
            self._notify_unsubmitted(
                set_id, row["title"], "content_published",
                {"content": "practice_set", "teacher": self.actor.username},
            )
        return self.get(set_id)

    def archive(self, set_id: str) -> dict[str, Any]:
        """Withdraw a set: its author, or an administrator for anyone's published set (D10)."""
        self._require_author()
        with store.write() as conn:
            row = self._withdrawable(conn, set_id)
            conn.execute(
                "UPDATE practice_sets SET status = 'archived', updated_at = ? WHERE id = ?", (_now(), set_id)
            )
        if row["owner_user_id"] != self.actor.id:
            # An administrator sees what they withdrew, never its questions or results.
            return {**self._summary(row), "status": "archived"}
        return self.get(set_id)

    def withdrawable(self) -> list[dict[str, Any]]:
        """For administrators: other people's published sets, which they may only withdraw."""
        if _role(self.actor) != "admin":
            return []
        with store.read() as conn:
            if conn is None:
                return []
            rows = conn.execute(
                "SELECT * FROM practice_sets WHERE owner_user_id != ? AND status = 'published' "
                "ORDER BY updated_at DESC",
                (self.actor.id,),
            ).fetchall()
        names = _usernames()
        return [{**self._summary(r), "owner": names.get(r["owner_user_id"], "")} for r in rows]

    def delete(self, set_id: str) -> None:
        self._require_author()
        with store.read() as conn:
            row = self._withdrawable(conn, set_id)
        if row["owner_user_id"] != self.actor.id:
            # D10: an administrator's "delete" withdraws; only the author deletes.
            self.archive(set_id)
            return
        with store.write() as conn:
            row = self._owned(conn, set_id)
            if row["current_revision"] > 0:
                raise PracticeConflict("Published practice sets cannot be deleted; archive them instead.")
            conn.execute("DELETE FROM practice_sets WHERE id = ?", (set_id,))
        from deeptutor.publishing.scope import PRACTICE_SET, forget

        forget(PRACTICE_SET, set_id)

    async def from_bank(self, entry_ids: list[int]) -> list[dict[str, Any]]:
        """Questions from the author's own bank: question, answer and explanation only (§8.2)."""
        from deeptutor.services.session.sqlite_store import get_sqlite_session_store

        self._require_author()
        bank = get_sqlite_session_store()
        picked = []
        for entry_id in list(dict.fromkeys(entry_ids))[:MAX_QUESTIONS]:
            entry = await bank.get_notebook_entry(int(entry_id))
            if entry is None:
                continue
            try:
                picked.append(normalize_question({
                    "question": entry.get("question"),
                    "question_type": entry.get("question_type"),
                    "options": entry.get("options") or {},
                    "correct_answer": entry.get("correct_answer"),
                    "explanation": entry.get("explanation"),
                }))
            except PracticeError:
                continue
        return picked

    async def generate(
        self, *, topic: str, count: int, kb_ref: str | None = None,
        question_types: list[str] | None = None, difficulty: str = "", language: str = "zh",
        book_ref: str | None = None, chapter_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """AI questions from a knowledge base the author can read (§8.2), or
        from a book they can read, whole or some chapters (batch 3 §4)."""
        self._require_author()
        topic = str(topic or "").strip()
        material = ""
        if book_ref:
            topic_from_book, material = book_material(book_ref, chapter_ids or [])
            topic = topic or topic_from_book
        return await generate_questions(
            session_id=f"practice-set-{self.actor.id}", topic=topic, count=count, kb_ref=kb_ref,
            question_types=question_types, difficulty=difficulty, language=language, material=material,
        )

    # the student side

    def _published(self, conn, set_id: str, revision: int | None = None):
        """The set and its current revision, or the earlier *revision* a student opened."""
        row = conn.execute("SELECT * FROM practice_sets WHERE id = ?", (set_id,)).fetchone() if conn else None
        if row is None or row["status"] != "published" or not self._reaches_me(set_id):
            raise PracticeNotFound("Practice set not found.")
        wanted = row["current_revision"] if revision is None else revision
        frozen = conn.execute(
            "SELECT * FROM practice_set_revisions WHERE set_id = ? AND revision = ?",
            (set_id, wanted),
        ).fetchone() if 0 < wanted <= row["current_revision"] else None
        if frozen is None:
            raise PracticeError("This version of the practice set does not exist.")
        return row, frozen

    def assigned(self) -> list[dict[str, Any]]:
        """Published sets whose scope reaches the student, with their own progress."""
        from deeptutor.publishing.scope import PRACTICE_SET, managed_items

        if _role(self.actor) != "student":
            return []
        ids = [i for i in managed_items(PRACTICE_SET) if self._reaches_me(i)]
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        with store.read() as conn:
            if conn is None:
                return []
            rows = conn.execute(
                f"SELECT * FROM practice_sets WHERE id IN ({marks}) AND status = 'published' "
                "ORDER BY updated_at DESC",
                ids,
            ).fetchall()
            mine = conn.execute(
                f"SELECT set_id, score, max_score FROM practice_submissions "
                f"WHERE student_id = ? AND set_id IN ({marks}) ORDER BY submitted_at",
                (self.actor.id, *ids),
            ).fetchall()
        from deeptutor.classes.service import _accounts
        from deeptutor.publishing.provenance import provenance

        accounts = _accounts()
        progress: dict[str, list] = {}
        for submission in mine:
            progress.setdefault(submission["set_id"], []).append(submission)
        names = _usernames()
        out = []
        for row in rows:
            done = progress.get(row["id"], [])
            best = (
                counted_submission(done, _col(row, "score_mode", "best")) if self._reveal(row, True) else None
            )
            out.append({
                **self._summary(row),
                "teacher": names.get(row["owner_user_id"], ""),
                "provenance": provenance(row["owner_user_id"], self.actor, accounts=accounts),
                "submissions": len(done),
                "best_score": best["score"] if best else None,
                "max_score": best["max_score"] if best else None,
            })
        return out

    def take(self, set_id: str) -> dict[str, Any]:
        with store.read() as conn:
            row, revision = self._published(conn, set_id)
            submissions = conn.execute(
                "SELECT id, revision, submitted_at, late, score, max_score, results_json "
                "FROM practice_submissions WHERE set_id = ? AND student_id = ? ORDER BY submitted_at",
                (set_id, self.actor.id),
            ).fetchall()
        # A submission to an earlier version does not reveal this version's answers.
        reveal = self._reveal(row, any(s["revision"] == row["current_revision"] for s in submissions))
        # Scores of what was submitted show on the same terms as the answers.
        graded = self._reveal(row, True)
        questions = json.loads(revision["questions_json"])
        latest = submissions[-1] if submissions else None
        latest_rows = _results_only(json.loads(latest["results_json"])) if latest else []
        average = self._class_average(row, sum(q["points"] for q in questions)) if graded and submissions else None
        return {
            **self._summary(row),
            "title": revision["title"],
            "description": revision["description"],
            "teacher": _usernames().get(row["owner_user_id"], ""),
            "attempts_left": (
                max(0, _col(row, "attempts_limit") - len(submissions))
                if _col(row, "attempts_limit") else None
            ),
            "answers_revealed": reveal,
            # R6 / batch 4 §1: on the current version's full marks; ``None`` with fewer than 3 scores.
            "class_average": average,
            "questions": [_for_student(q, reveal=reveal) for q in questions],
            "submissions": [
                {**{k: s[k] for k in ("id", "revision", "submitted_at", "late", "max_score")},
                 "score": s["score"] if graded else None}
                for s in submissions
            ],
            # The latest attempt, so a reopened page shows it (its version may be older).
            "latest_revision": latest["revision"] if latest else None,
            "latest_results": latest_rows if graded else _withheld(latest_rows),
        }

    def _class_average(self, row, max_score: float) -> float | None:
        """The average of the students the set reaches, each by the counted attempt's share."""
        from deeptutor.publishing.scope import PRACTICE_SET, audience, get_scope

        scope = get_scope(PRACTICE_SET, row["id"])
        reached = audience(scope, students_only=True, owner_id=row["owner_user_id"]) if scope else set()
        with store.read() as conn:
            rows = conn.execute(
                "SELECT student_id, score, max_score FROM practice_submissions WHERE set_id = ? ORDER BY submitted_at",
                (row["id"],),
            ).fetchall() if conn else []
        by_student: dict[str, list] = {}
        for submission in rows:
            if submission["student_id"] in reached:
                by_student.setdefault(submission["student_id"], []).append(submission)
        mode = _col(row, "score_mode", "best") or "best"
        shares = [_ratio(counted_submission(mine, mode)) for mine in by_student.values()]
        if len(shares) < CLASS_AVERAGE_MIN:
            return None
        return round(sum(shares) / len(shares) * max_score, 1)

    @staticmethod
    def _reveal(row, submitted: bool) -> bool:
        """Answers show only to a student who has submitted this version (§8.3);
        with ``after_due`` also not before the deadline, so nobody copies them."""
        if not submitted:
            return False
        if row["reveal"] == "after_due":
            return row["due_at"] is not None and _now() >= row["due_at"]
        return True

    async def submit(
        self, set_id: str, answers: dict[str, Any], *, started_at: float | None = None,
        revision: int | None = None,
    ) -> dict[str, Any]:
        """Grade against the version the student opened (*revision*), not one the
        teacher published while they were answering; without it, the current one."""
        import asyncio

        def load():
            with store.read() as conn:
                return self._published(conn, set_id, revision)

        row, revision = await asyncio.to_thread(load)

        def used() -> int:
            with store.read() as conn:
                return conn.execute(
                    "SELECT COUNT(*) FROM practice_submissions WHERE set_id = ? AND student_id = ?",
                    (set_id, self.actor.id),
                ).fetchone()[0]

        limit = _col(row, "attempts_limit")
        if limit and await asyncio.to_thread(used) >= limit:
            raise PracticeConflict("You have used all your attempts at this practice set.")
        questions = json.loads(revision["questions_json"])
        given = {
            q["question_id"]: str((answers or {}).get(q["question_id"]) or "")[:ANSWER_MAX]
            for q in questions
        }
        results: dict[str, dict[str, Any]] = {}
        pending = []
        for question in questions:
            decided = exact_result(question, given[question["question_id"]])
            if decided is None:
                pending.append((question, given[question["question_id"]]))
            else:
                results[question["question_id"]] = {"result": decided, "feedback": ""}
        if pending:
            judged = await _judge(pending)
            for question, _ in pending:
                results[question["question_id"]] = judged.get(
                    question["question_id"],
                    {"result": "ungraded", "feedback": "Automatic grading was unavailable; ask your teacher."},
                )
        now = _now()
        late = row["due_at"] is not None and now > row["due_at"]
        rows = [
            {"question_id": q["question_id"], "result": results[q["question_id"]]["result"],
             "points": _points(q, results[q["question_id"]]["result"]), "max_points": q["points"]}
            for q in questions
        ]
        score = sum(r["points"] for r in rows)
        max_score = sum(q["points"] for q in questions)
        submission_id = f"sub_{uuid.uuid4().hex[:12]}"
        started = float(started_at) if started_at and 0 < float(started_at) <= now else None

        kept = {
            q["question_id"]: value for q in questions
            if (value := reviewable_answer(q, given[q["question_id"]])) is not None
        }

        def save():
            with store.write() as conn:
                if limit and conn.execute(
                    "SELECT COUNT(*) FROM practice_submissions WHERE set_id = ? AND student_id = ?",
                    (set_id, self.actor.id),
                ).fetchone()[0] >= limit:
                    raise PracticeConflict("You have used all your attempts at this practice set.")
                conn.execute(
                    "INSERT INTO practice_submissions (id, set_id, revision, student_id, started_at, "
                    "submitted_at, late, score, max_score, results_json, answers_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (submission_id, set_id, revision["revision"], self.actor.id, started, now,
                     int(late), score, max_score, json.dumps(rows), json.dumps(kept, ensure_ascii=False)),
                )

        await asyncio.to_thread(save)
        reveal = self._reveal(row, True)
        await self._to_my_bank(row, revision, questions, given, results, reveal=reveal)
        return {
            "submission_id": submission_id,
            "revision": revision["revision"],
            "score": score if reveal else None,
            "max_score": max_score,
            "late": late,
            "answers_revealed": reveal,
            "results": [
                # AI feedback can quote the key, so it waits for the answers too.
                {**r, "feedback": results[r["question_id"]]["feedback"],
                 "correct_answer": q["correct_answer"], "explanation": q["explanation"]}
                for r, q in zip(rows, questions)
            ] if reveal else _withheld(rows),
        }

    @staticmethod
    def _bank_item(row, revision, question, *, answer: str, result: str, reveal: bool) -> dict[str, Any]:
        return {
            "origin_type": "practice_set",
            "origin_ref": f"practice_set:{row['id']}",
            "turn_id": f"rev{revision['revision']}",
            "question_id": question["question_id"],
            "question": question["question"],
            "question_type": question["question_type"],
            "options": question["options"],
            # Withheld until the teacher's reveal time (§8.3); filled in by
            # :meth:`reveal_in_bank` once the student may see them.
            "correct_answer": question["correct_answer"] if reveal else "",
            "explanation": question["explanation"] if reveal else "",
            "user_answer": answer,
            # Right or wrong waits for the reveal too; it would give the key away.
            "is_correct": reveal and result == "correct",
            "result": result if reveal else "ungraded",
            "source": "practice_set",
            "material_id": row["id"],
            "material_title": revision["title"],
            "assessment_type": "quiz",
        }

    async def _to_my_bank(self, row, revision, questions, given, results, *, reveal: bool) -> None:
        """Each answer into the student's own question bank, tagged with the set (§8.3)."""
        from deeptutor.services.session.sqlite_store import get_sqlite_session_store

        items = [
            self._bank_item(
                row, revision, question, answer=given[question["question_id"]],
                result=results[question["question_id"]]["result"], reveal=reveal,
            )
            for question in questions
        ]
        try:
            await get_sqlite_session_store().upsert_notebook_entries(None, items)
        except Exception:
            # The submission and its score stand; only the bank copy is missing.
            logger.exception("Could not save practice set %s answers to the question bank", row["id"])

    async def with_my_answers(self, view: dict[str, Any]) -> dict[str, Any]:
        """Fill the latest attempt's answers back in from the student's own bank.

        The answers are the student's personal data: the practice store keeps
        only results, and the bank (their own) keeps what they wrote.
        """
        from deeptutor.services.session.sqlite_store import get_sqlite_session_store

        revision = view.get("latest_revision")
        if not revision or not view.get("latest_results"):
            return view
        bank = get_sqlite_session_store()
        rows = []
        for row in view["latest_results"]:
            entry = await bank.find_notebook_entry_by_origin(
                "practice_set", f"practice_set:{view['id']}", row["question_id"], turn_id=f"rev{revision}",
            )
            rows.append({**row, "answer": str((entry or {}).get("user_answer") or "")})
        return {**view, "latest_results": rows}

    async def reveal_in_bank(self, set_id: str) -> None:
        """Once answers may show, complete the bank entries saved without them.

        Every version the student submitted is completed, not only the current
        one: a set republished after their attempt must not leave that attempt's
        mistakes without answers forever.
        """
        import asyncio

        from deeptutor.services.session.sqlite_store import get_sqlite_session_store

        def load():
            with store.read() as conn:
                row, _current = self._published(conn, set_id)
                latest: dict[int, Any] = {}
                for submission in conn.execute(
                    "SELECT revision, results_json FROM practice_submissions "
                    "WHERE set_id = ? AND student_id = ? ORDER BY submitted_at",
                    (set_id, self.actor.id),
                ):
                    latest[submission["revision"]] = submission
                revisions = {
                    number: conn.execute(
                        "SELECT * FROM practice_set_revisions WHERE set_id = ? AND revision = ?",
                        (set_id, number),
                    ).fetchone()
                    for number in latest
                }
            return row, latest, revisions

        row, latest, revisions = await asyncio.to_thread(load)
        if not latest or not self._reveal(row, True):
            return
        bank = get_sqlite_session_store()
        items = []
        for number, submission in latest.items():
            revision = revisions.get(number)
            if revision is None:
                continue
            results = {r["question_id"]: r["result"] for r in json.loads(submission["results_json"])}
            for question in json.loads(revision["questions_json"]):
                entry = await bank.find_notebook_entry_by_origin(
                    "practice_set", f"practice_set:{set_id}", question["question_id"],
                    turn_id=f"rev{number}",
                )
                result = results.get(question["question_id"], "ungraded")
                if entry is None or (
                    str(entry.get("result") or "") == result
                    and (entry.get("correct_answer") or not question["correct_answer"])
                ):
                    continue
                items.append(self._bank_item(
                    row, revision, question, answer=str(entry.get("user_answer") or ""),
                    result=result, reveal=True,
                ))
        if items:
            await bank.upsert_notebook_entries(None, items)

    # the teacher's report (§8.4)

    def report(self, set_id: str) -> dict[str, Any]:
        """Results of the students the set reaches, plus anyone who submitted and left it (§8.4)."""
        from deeptutor.publishing.scope import PRACTICE_SET, Scope, audience, get_scope

        self._require_author()
        with store.read() as conn:
            row = self._owned(conn, set_id)
            submissions = conn.execute(
                "SELECT * FROM practice_submissions WHERE set_id = ? ORDER BY submitted_at", (set_id,)
            ).fetchall()
            revision = conn.execute(
                "SELECT questions_json FROM practice_set_revisions WHERE set_id = ? AND revision = ?",
                (set_id, row["current_revision"]),
            ).fetchone()
        scope = get_scope(PRACTICE_SET, set_id)
        reached = audience(scope, students_only=True, owner_id=row["owner_user_id"]) if scope else set()
        from deeptutor.classes.service import _accounts, teacher_student_ids

        visible = None
        if scope is not None and scope.everyone and _role(self.actor) != "admin":
            # Spec §5: a teacher sees the students of their classes and the people
            # they named, even when the set went to everyone (decided 2026-11-02).
            named = Scope(users=scope.users, classes=scope.classes)
            visible = audience(named, students_only=True, owner_id=row["owner_user_id"]) | (
                reached & teacher_student_ids(self.actor.id)
            )
            reached = visible
        accounts = _accounts()
        names = {user_id: account["username"] for user_id, account in accounts.items()}
        by_student: dict[str, list] = {}
        for submission in submissions:
            # A deleted account or one that is no longer a student is not reported.
            if visible is not None and submission["student_id"] not in visible:
                continue
            if (accounts.get(submission["student_id"]) or {}).get("role") == "student":
                by_student.setdefault(submission["student_id"], []).append(submission)
        students = []
        for student_id in sorted(reached | set(by_student), key=lambda s: names.get(s, s)):
            mine = by_student.get(student_id, [])
            first = mine[0] if mine else None
            best = best_submission(mine)
            counted = counted_submission(mine, _col(row, "score_mode", "best") or "best")
            students.append({
                "id": student_id,
                "username": names.get(student_id, student_id),
                # False: submitted earlier, then left the scope (a class, or was removed).
                "in_scope": student_id in reached,
                "completed": bool(mine),
                "attempts": len(mine),
                "first_score": first["score"] if first else None,
                # What a comment attaches to (batch 2 §3): their latest attempt.
                "latest_submission_id": mine[-1]["id"] if mine else None,
                # Each score with its own version's maximum (versions may differ).
                "best_score": best["score"] if best else None,
                "best_max_score": best["max_score"] if best else None,
                # The attempt that counts under the set's score mode (batch 3 §6.1).
                "score": counted["score"] if counted else None,
                "score_max": counted["max_score"] if counted else None,
                # Answers the AI could not judge, waiting for the teacher (§6.1).
                "ungraded": sum(
                    1 for s in mine for r in json.loads(s["results_json"]) if r["result"] == "ungraded"
                ),
                "max_score": first["max_score"] if first else None,
                "minutes": round((first["submitted_at"] - first["started_at"]) / 60, 1)
                if first and first["started_at"] else None,
                "late": bool(first["late"]) if first else False,
                "submitted_at": first["submitted_at"] if first else None,
            })
        # Error rate per question of the current version, over each student's
        # first submission to that version.
        tally: dict[str, list[int]] = {}
        firsts = []
        for mine in by_student.values():
            current = [s for s in mine if s["revision"] == row["current_revision"]]
            if not current:
                continue
            firsts.append(current[0])
            for result in json.loads(current[0]["results_json"]):
                if result["result"] == "ungraded":
                    continue
                counts = tally.setdefault(result["question_id"], [0, 0])
                counts[0] += result["result"] == "incorrect"
                counts[1] += 1
        questions = json.loads(revision["questions_json"]) if revision else []
        from deeptutor.practice_sets.review import option_counts

        return {
            "set_id": set_id,
            "title": row["title"],
            "due_at": row["due_at"],
            "score_mode": _col(row, "score_mode", "best") or "best",
            "attempts_limit": _col(row, "attempts_limit"),
            "students": students,
            "questions": [
                {
                    "question_id": q["question_id"],
                    "question": q["question"],
                    "answered": tally.get(q["question_id"], [0, 0])[1],
                    "error_rate": round(tally[q["question_id"]][0] / tally[q["question_id"]][1], 3)
                    if tally.get(q["question_id"], [0, 0])[1] else None,
                    "question_type": q["question_type"],
                    "options": q["options"],
                    "correct_answer": q["correct_answer"],
                    # How many picked each option (batch 3 §6.1), first attempts at this version.
                    "option_counts": option_counts(q, firsts),
                }
                for q in questions
            ],
        }

async def generate_questions(
    *, session_id: str, topic: str, count: int, kb_ref: str | None = None,
    question_types: list[str] | None = None, difficulty: str = "", language: str = "zh",
    material: str = "",
) -> list[dict[str, Any]]:
    """AI questions in the stored shape; shared by teachers' sets and students' remedial practice."""
    from deeptutor.agents.question.pipeline import QuestionPipeline
    from deeptutor.book.blocks.quiz import QuizGenerator
    from deeptutor.core.context import UnifiedContext
    from deeptutor.runtime.stream_bus import StreamBus

    topic = str(topic or "").strip()
    if not topic:
        raise PracticeError("Describe what the questions should cover.")
    kb_name = None
    if kb_ref:
        from deeptutor.multi_user.knowledge_access import resolve_kb

        kb_name = resolve_kb(kb_ref).id
    count = max(1, min(int(count or 5), 20))
    types = [t for t in (question_types or []) if t in QUESTION_TYPES and t != "multi_choice"]
    bus = StreamBus()
    try:
        result = await QuestionPipeline(language=language, kb_name=kb_name).run(
            context=UnifiedContext(
                session_id=session_id,
                user_message=topic,
                active_capability="deep_question",
                knowledge_bases=[kb_name] if kb_name else [],
                language=language,
            ),
            user_message=topic,
            num_questions=count,
            difficulty=difficulty,
            question_types=types,
            conversation_context=material,
            stream=bus,
        )
    finally:
        await bus.close()
    generated = QuizGenerator._extract_questions(dict(result.get("summary") or {}))
    questions = []
    for item in generated:
        try:
            questions.append(normalize_question({**item, "question_id": ""}))
        except PracticeError:
            continue
    if not questions:
        raise PracticeError("No questions could be generated. Try another topic.")
    return questions


#: Book text handed to the question generator, spread over the chosen chapters.
BOOK_MATERIAL_CHARS = 24_000


def book_material(book_ref: str, chapter_ids: list[str]) -> tuple[str, str]:
    """``(topic, text)`` from chapters of a book the caller can read (batch 3 §4).

    No chapters means the whole book. Each chapter gets an equal share of the
    text budget, so a long first chapter does not crowd out the rest.
    """
    from deeptutor.book.context import build_book_context
    from deeptutor.multi_user.book_access import readable_book_storage

    storage = readable_book_storage(book_ref)
    book_id = str(book_ref).split(":", 1)[-1]
    spine = storage.load_spine(book_id) if storage is not None else None
    if storage is None or storage.load_book(book_id) is None or spine is None:
        raise PracticeNotFound("Book not found.")
    wanted = set(chapter_ids)
    chapters = [c for c in spine.chapters if not wanted or c.id in wanted]
    if not chapters:
        raise PracticeError("Choose chapters of this book.")
    share = max(2_000, BOOK_MATERIAL_CHARS // len(chapters))
    parts = []
    for chapter in chapters:
        if not chapter.page_ids:
            continue
        result = build_book_context(
            [{"book_id": book_ref, "page_ids": list(chapter.page_ids)}], storage=storage, max_chars=share,
        )
        if result.text.strip():
            parts.append(result.text.strip())
    text = "\n\n".join(parts)[:BOOK_MATERIAL_CHARS]
    if not text:
        raise PracticeError("The chosen chapters have no content yet.")
    topic = "; ".join(c.title for c in chapters if getattr(c, "title", ""))[:500]
    return topic, "Write the questions from this book material only:\n\n" + text


def _usernames() -> dict[str, str]:
    from deeptutor.classes.service import _accounts

    return {user_id: row["username"] for user_id, row in _accounts().items()}


__all__ = [
    "PracticeConflict",
    "PracticeError",
    "PracticeForbidden",
    "PracticeNotFound",
    "PracticeSetService",
    "exact_result",
    "normalize_question",
    "normalize_questions",
]
