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
  (§8.4) — results, never the answers.
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
        try:
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
        with store.read() as conn:
            row = self._owned(conn, set_id)
        return {**self._summary(row), "questions": json.loads(row["draft_questions_json"])}

    def update(
        self, set_id: str, *, title: str | None = None, description: str | None = None,
        questions: Any = None, due_at: float | None | str = "", reveal: str | None = None,
    ) -> dict[str, Any]:
        sets: list[str] = []
        values: list[Any] = []
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
            self._owned(conn, set_id)
            if sets:
                conn.execute(
                    f"UPDATE practice_sets SET {', '.join(sets)}, updated_at = ? WHERE id = ?",
                    (*values, _now(), set_id),
                )
        return self.get(set_id)

    def publish(self, set_id: str) -> dict[str, Any]:
        """Freeze the draft as the next revision (§8.5); earlier submissions keep theirs."""
        with store.write() as conn:
            row = self._owned(conn, set_id)
            if row["status"] == "archived":
                raise PracticeConflict("This practice set is archived.")
            questions = normalize_questions(json.loads(row["draft_questions_json"]))
            if not questions:
                raise PracticeError("Add at least one question before publishing.")
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
        return self.get(set_id)

    def archive(self, set_id: str) -> dict[str, Any]:
        with store.write() as conn:
            self._owned(conn, set_id)
            conn.execute(
                "UPDATE practice_sets SET status = 'archived', updated_at = ? WHERE id = ?", (_now(), set_id)
            )
        return self.get(set_id)

    def delete(self, set_id: str) -> None:
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
    ) -> list[dict[str, Any]]:
        """AI questions from a knowledge base the author can read (§8.2)."""
        from deeptutor.agents.question.pipeline import QuestionPipeline
        from deeptutor.book.blocks.quiz import QuizGenerator
        from deeptutor.core.context import UnifiedContext
        from deeptutor.runtime.stream_bus import StreamBus

        self._require_author()
        topic = str(topic or "").strip()
        if not topic:
            raise PracticeError("Describe what the questions should cover.")
        kb_name = None
        if kb_ref:
            from deeptutor.multi_user.knowledge_access import resolve_kb

            kb_name = resolve_kb(kb_ref).name
        count = max(1, min(int(count or 5), 20))
        types = [t for t in (question_types or []) if t in QUESTION_TYPES and t != "multi_choice"]
        bus = StreamBus()
        try:
            result = await QuestionPipeline(language=language, kb_name=kb_name).run(
                context=UnifiedContext(
                    session_id=f"practice-set-{self.actor.id}",
                    user_message=topic,
                    active_capability="deep_question",
                    knowledge_bases=[kb_name] if kb_name else [],
                    language=language,
                ),
                user_message=topic,
                num_questions=count,
                difficulty=difficulty,
                question_types=types,
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

    # the student side

    def _published(self, conn, set_id: str):
        row = conn.execute("SELECT * FROM practice_sets WHERE id = ?", (set_id,)).fetchone() if conn else None
        if row is None or row["status"] != "published" or not self._reaches_me(set_id):
            raise PracticeNotFound("Practice set not found.")
        revision = conn.execute(
            "SELECT * FROM practice_set_revisions WHERE set_id = ? AND revision = ?",
            (set_id, row["current_revision"]),
        ).fetchone()
        return row, revision

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
                f"SELECT set_id, COUNT(*) AS n, MAX(score) AS best, MAX(max_score) AS max_score "
                f"FROM practice_submissions WHERE student_id = ? AND set_id IN ({marks}) GROUP BY set_id",
                (self.actor.id, *ids),
            ).fetchall()
        progress = {r["set_id"]: r for r in mine}
        names = _usernames()
        out = []
        for row in rows:
            done = progress.get(row["id"])
            out.append({
                **self._summary(row),
                "teacher": names.get(row["owner_user_id"], ""),
                "submissions": done["n"] if done else 0,
                "best_score": done["best"] if done else None,
                "max_score": done["max_score"] if done else None,
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
        reveal = self._reveal(row, bool(submissions))
        questions = json.loads(revision["questions_json"])
        latest = submissions[-1] if submissions else None
        return {
            **self._summary(row),
            "title": revision["title"],
            "description": revision["description"],
            "teacher": _usernames().get(row["owner_user_id"], ""),
            "answers_revealed": reveal,
            "questions": [_for_student(q, reveal=reveal) for q in questions],
            "submissions": [
                {k: s[k] for k in ("id", "revision", "submitted_at", "late", "score", "max_score")}
                for s in submissions
            ],
            "latest_results": json.loads(latest["results_json"]) if latest else [],
        }

    @staticmethod
    def _reveal(row, submitted: bool) -> bool:
        if row["reveal"] == "after_due":
            return row["due_at"] is not None and _now() >= row["due_at"]
        return submitted

    async def submit(self, set_id: str, answers: dict[str, Any], *, started_at: float | None = None) -> dict[str, Any]:
        import asyncio

        def load():
            with store.read() as conn:
                return self._published(conn, set_id)

        row, revision = await asyncio.to_thread(load)
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

        def save():
            with store.write() as conn:
                conn.execute(
                    "INSERT INTO practice_submissions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (submission_id, set_id, revision["revision"], self.actor.id, started, now,
                     int(late), score, max_score, json.dumps(rows)),
                )

        await asyncio.to_thread(save)
        await self._to_my_bank(row, revision, questions, given, results)
        reveal = self._reveal(row, True)
        return {
            "submission_id": submission_id,
            "score": score,
            "max_score": max_score,
            "late": late,
            "answers_revealed": reveal,
            "results": [
                {**r, "feedback": results[r["question_id"]]["feedback"],
                 **({"correct_answer": q["correct_answer"], "explanation": q["explanation"]} if reveal else {})}
                for r, q in zip(rows, questions)
            ],
        }

    async def _to_my_bank(self, row, revision, questions, given, results) -> None:
        """Each answer into the student's own question bank, tagged with the set (§8.3)."""
        from deeptutor.services.session.sqlite_store import get_sqlite_session_store

        items = []
        for question in questions:
            result = results[question["question_id"]]["result"]
            items.append({
                "origin_type": "practice_set",
                "origin_ref": f"practice_set:{row['id']}",
                "turn_id": f"rev{revision['revision']}",
                "question_id": question["question_id"],
                "question": question["question"],
                "question_type": question["question_type"],
                "options": question["options"],
                "correct_answer": question["correct_answer"],
                "explanation": question["explanation"],
                "user_answer": given[question["question_id"]],
                "is_correct": result == "correct",
                "result": result,
                "source": "practice_set",
                "material_id": row["id"],
                "material_title": revision["title"],
                "assessment_type": "quiz",
            })
        try:
            await get_sqlite_session_store().upsert_notebook_entries(None, items)
        except Exception:
            # The submission and its score stand; only the bank copy is missing.
            logger.exception("Could not save practice set %s answers to the question bank", row["id"])

    # the teacher's report (§8.4)

    def report(self, set_id: str) -> dict[str, Any]:
        from deeptutor.publishing.scope import PRACTICE_SET, audience, get_scope

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
        names = _usernames()
        by_student: dict[str, list] = {}
        for submission in submissions:
            if submission["student_id"] in reached:
                by_student.setdefault(submission["student_id"], []).append(submission)
        students = []
        for student_id in sorted(reached, key=lambda s: names.get(s, s)):
            mine = by_student.get(student_id, [])
            first = mine[0] if mine else None
            students.append({
                "id": student_id,
                "username": names.get(student_id, student_id),
                "completed": bool(mine),
                "attempts": len(mine),
                "first_score": first["score"] if first else None,
                "best_score": max(s["score"] for s in mine) if mine else None,
                "max_score": first["max_score"] if first else None,
                "minutes": round((first["submitted_at"] - first["started_at"]) / 60, 1)
                if first and first["started_at"] else None,
                "late": bool(first["late"]) if first else False,
                "submitted_at": first["submitted_at"] if first else None,
            })
        # Error rate per question over each student's first submission.
        tally: dict[str, list[int]] = {}
        for mine in by_student.values():
            for result in json.loads(mine[0]["results_json"]):
                if result["result"] == "ungraded":
                    continue
                counts = tally.setdefault(result["question_id"], [0, 0])
                counts[0] += result["result"] == "incorrect"
                counts[1] += 1
        questions = json.loads(revision["questions_json"]) if revision else []
        return {
            "set_id": set_id,
            "title": row["title"],
            "due_at": row["due_at"],
            "students": students,
            "questions": [
                {
                    "question_id": q["question_id"],
                    "question": q["question"],
                    "answered": tally.get(q["question_id"], [0, 0])[1],
                    "error_rate": round(tally[q["question_id"]][0] / tally[q["question_id"]][1], 3)
                    if tally.get(q["question_id"], [0, 0])[1] else None,
                }
                for q in questions
            ],
        }


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
