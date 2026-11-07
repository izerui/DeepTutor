"""A learner's own remedial practice from their mistakes (spec 2026-11-03 batch 3 §3.2).

New questions like the chosen mistakes go into the learner's own question
bank, not yet answered (``ungraded``, so they are not mistakes), tagged
``remedial``; the learner then practises them in the usual practice session.
They are the learner's alone: no author sees them, they are no task. The
AI they use counts against the learner's daily limit.
"""

from __future__ import annotations

import time
from typing import Any
import uuid

COUNT_MIN, COUNT_MAX = 3, 10
SOURCES_MAX = 20


class RemedialError(Exception):
    status = 400


async def similar_questions(entry_ids: list[int], count: int, *, language: str = "zh") -> dict[str, Any]:
    """``{"entry_ids": [...]}`` of the new questions in the caller's own bank."""
    from deeptutor.practice_sets.service import PracticeError, generate_questions
    from deeptutor.services.session.sqlite_store import get_sqlite_session_store

    if not entry_ids:
        raise RemedialError("Pick at least one question.")
    if len(entry_ids) > SOURCES_MAX:
        raise RemedialError(f"Pick at most {SOURCES_MAX} questions.")
    count = max(COUNT_MIN, min(int(count or COUNT_MIN), COUNT_MAX))
    bank = get_sqlite_session_store()
    sources = [entry for entry_id in entry_ids if (entry := await bank.get_notebook_entry(int(entry_id)))]
    if not sources:
        raise RemedialError("Pick questions from your question bank.")
    lines = "\n".join(f"- {str(entry.get('question') or '')[:500]}" for entry in sources)
    try:
        questions = await generate_questions(
            session_id=f"remedial-{uuid.uuid4().hex[:8]}",
            topic=f"New questions testing the same points as these, which the learner got wrong:\n{lines}"[:2000],
            count=count, language=language,
        )
    except PracticeError as exc:
        raise RemedialError(str(exc)) from exc
    batch = uuid.uuid4().hex[:12]
    title = time.strftime("Remedial %Y-%m-%d %H:%M")
    items = [
        {
            "origin_type": "remedial",
            "origin_ref": f"remedial:{batch}",
            "turn_id": batch,
            "question_id": question["question_id"],
            "question": question["question"],
            "question_type": question["question_type"],
            "options": question["options"],
            "correct_answer": question["correct_answer"],
            "explanation": question["explanation"],
            "user_answer": "",
            "is_correct": False,
            "result": "ungraded",
            "source": "remedial",
            "material_id": f"remedial:{batch}",
            "material_title": title,
            "assessment_type": "quiz",
        }
        for question in questions
    ]
    await bank.upsert_notebook_entries(None, items)
    made = []
    for item in items:
        entry = await bank.find_notebook_entry_by_origin(
            "remedial", item["origin_ref"], item["question_id"], turn_id=batch,
        )
        if entry:
            made.append(int(entry["id"]))
    return {"entry_ids": made, "title": title}


__all__ = ["RemedialError", "similar_questions"]
