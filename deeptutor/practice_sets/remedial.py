"""Remedial practice from what students got wrong (spec 2026-11-03 batch 3 §3.1).

A teacher picks questions of their practice set that many got wrong, or
knowledge points of their Mastery Path that students have not mastered, and
gets a **draft** practice set: "Remedial: <name>". It is published like any
practice set, so its receivers, deadline, task and results follow the
existing rules (no separate task, R23). The students suggested as receivers
are the ones who got those questions wrong / have not mastered those points.
"""

from __future__ import annotations

import json
from typing import Any
import uuid

from deeptutor.practice_sets import store
from deeptutor.practice_sets.service import (
    PracticeError,
    PracticeNotFound,
    PracticeSetService,
    normalize_question,
)

SIMILAR_MAX = 3
TITLE_PREFIX = "Remedial: "


def _people(ids: set[str]) -> list[dict[str, str]]:
    from deeptutor.classes.service import _accounts

    accounts = _accounts()
    return sorted(
        ({"id": i, "username": accounts[i]["username"]} for i in ids if i in accounts),
        key=lambda p: p["username"],
    )


class RemedialPractice:
    def __init__(self, actor=None) -> None:
        self.service = PracticeSetService(actor)
        self.actor = self.service.actor

    def _draft(self, title: str, questions: list[dict[str, Any]], prefix: str = TITLE_PREFIX) -> dict[str, Any]:
        created = self.service.create(((prefix or TITLE_PREFIX)[:40] + title)[:200])
        return self.service.update(created["id"], questions=questions)

    async def from_set(
        self, set_id: str, question_ids: list[str], similar: int = 0, prefix: str = TITLE_PREFIX,
    ) -> dict[str, Any]:
        """The picked questions again, plus up to three AI look-alikes of each."""
        import asyncio

        self.service._require_author()
        if not question_ids:
            raise PracticeError("Pick at least one question.")
        similar = max(0, min(int(similar or 0), SIMILAR_MAX))

        def load():
            with store.read() as conn:
                row = self.service._owned(conn, set_id)
                revision = conn.execute(
                    "SELECT questions_json FROM practice_set_revisions WHERE set_id = ? AND revision = ?",
                    (set_id, row["current_revision"]),
                ).fetchone()
                submissions = conn.execute(
                    "SELECT student_id, revision, results_json FROM practice_submissions "
                    "WHERE set_id = ? ORDER BY submitted_at",
                    (set_id,),
                ).fetchall()
            return row, revision, submissions

        row, revision, submissions = await asyncio.to_thread(load)
        if revision is None:
            raise PracticeError("Only a published practice set has results to practise from.")
        wanted = set(question_ids)
        picked = [q for q in json.loads(revision["questions_json"]) if q["question_id"] in wanted]
        if not picked:
            raise PracticeNotFound("Question not found.")
        questions = [{**q, "question_id": f"q_{uuid.uuid4().hex[:10]}"} for q in picked]
        if similar:
            lines = "\n".join(f"- {q['question']}" for q in picked)
            extra = await self.service.generate(
                topic=f"Questions like these, testing the same points:\n{lines}"[:2000],
                count=min(20, similar * len(picked)),
            )
            questions.extend(extra)
        # Who got them wrong, on their first attempt at the current version.
        seen: set[str] = set()
        wrong: set[str] = set()
        for submission in submissions:
            if submission["revision"] != row["current_revision"] or submission["student_id"] in seen:
                continue
            seen.add(submission["student_id"])
            if any(
                r["question_id"] in wanted and r["result"] in ("incorrect", "partial")
                for r in json.loads(submission["results_json"])
            ):
                wrong.add(submission["student_id"])
        draft = await asyncio.to_thread(self._draft, row["title"], [normalize_question(q) for q in questions], prefix)
        return {"set": draft, "suggested_students": _people(self._mine(wrong))}

    def _mine(self, students: set[str]) -> set[str]:
        """Only students this teacher may send to: their classes' students."""
        from deeptutor.classes.service import teacher_student_ids

        if self.actor.is_admin:
            return students
        return students & teacher_student_ids(self.actor.id)

    async def from_path(
        self, template_id: str, kp_ids: list[str], student_ids: list[str] | None = None,
        prefix: str = TITLE_PREFIX,
    ) -> dict[str, Any]:
        """AI questions on the picked knowledge points of the teacher's own path."""
        import asyncio

        from deeptutor.teaching.store import TeachingStore

        self.service._require_author()
        if not kp_ids:
            raise PracticeError("Pick at least one knowledge point.")
        teaching = TeachingStore()
        template = await asyncio.to_thread(teaching.get_template, template_id)
        if template is None or template.owner_user_id != self.actor.id or not template.current_revision:
            raise PracticeNotFound("Mastery Path not found.")
        revision = await asyncio.to_thread(teaching.get_revision, template_id, template.current_revision)
        if revision is None:
            raise PracticeNotFound("Mastery Path not found.")
        wanted = set(kp_ids)
        points = [
            kp for module in revision.outline.modules for kp in module.knowledge_points if kp.id in wanted
        ]
        if not points:
            raise PracticeNotFound("Knowledge point not found.")
        topic = "Knowledge points: " + "; ".join(
            kp.name + (f" ({detail})" if (detail := getattr(kp, "description", "")) else "") for kp in points
        )
        sources = revision.sources
        kb_ref = f"kb:{sources.kb_refs[0].kb_id}" if sources.kb_refs else None
        book_ref = f"book:{sources.book_refs[0].book_id}" if not kb_ref and sources.book_refs else None
        questions = await self.service.generate(
            topic=topic[:2000], count=min(20, max(3, 2 * len(points))), kb_ref=kb_ref, book_ref=book_ref,
        )
        draft = await asyncio.to_thread(self._draft, revision.outline.name or template.title, questions, prefix)
        return {"set": draft, "suggested_students": _people(self._mine(set(student_ids or [])))}


__all__ = ["RemedialPractice"]
