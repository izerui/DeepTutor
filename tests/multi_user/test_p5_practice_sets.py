"""P5: teacher practice sets (spec 2026-10-29 §8)."""

from __future__ import annotations

import asyncio

import pytest

from deeptutor.classes.service import ClassService
from deeptutor.practice_sets.service import (
    PracticeConflict,
    PracticeForbidden,
    PracticeNotFound,
    PracticeSetService,
    exact_result,
)
from deeptutor.publishing.scope import PRACTICE_SET
from deeptutor.publishing.service import PublishService

QUESTIONS = [
    {"question": "2 + 2 = ?", "question_type": "choice",
     "options": {"A": "3", "B": "4"}, "correct_answer": "B", "explanation": "Add."},
    {"question": "The sun is a star.", "question_type": "concept", "correct_answer": "True"},
    {"question": "Explain gravity.", "question_type": "written", "correct_answer": "Mass attracts mass.",
     "points": 2},
]


def _class_id(as_account) -> str:
    with as_account("tina"):
        [row] = ClassService().list_classes()
    return row["id"]


@pytest.fixture
def published_set(as_account):
    class_id = _class_id(as_account)
    with as_account("tina"):
        service = PracticeSetService()
        created = service.create("Week 1")
        service.update(created["id"], questions=QUESTIONS)
        service.publish(created["id"])
        PublishService().set_scope(PRACTICE_SET, created["id"], everyone=False, users=[], classes=[class_id])
    return created["id"]


@pytest.fixture
def fake_judge(monkeypatch):
    from deeptutor.learning import semantic_assessment
    from deeptutor.learning.semantic_assessment import Verdict

    async def judge(config, items):
        return {
            item["question_id"]: Verdict(
                result="correct" if "mass" in item["learner_answer"].lower() else "incorrect",
                confidence=0.95, feedback="ok",
            )
            for item in items
        }

    monkeypatch.setattr(semantic_assessment, "judge_answers", judge)


def test_exact_grading_rules():
    choice = {"question_type": "choice", "options": {"A": "3", "B": "4"}, "correct_answer": "B"}
    assert exact_result(choice, "b") == "correct" and exact_result(choice, "A") == "incorrect"
    truth = {"question_type": "concept", "options": {}, "correct_answer": "True"}
    assert exact_result(truth, "对") == "correct" and exact_result(truth, "F") == "incorrect"
    written = {"question_type": "written", "options": {}, "correct_answer": "x"}
    assert exact_result(written, "anything") is None and exact_result(written, " ") == "incorrect"


def test_a_student_answers_and_the_teacher_sees_results_not_answers(
    published_set, as_account, fake_judge
):
    with as_account("sara"):
        [listed] = PracticeSetService().assigned()
        assert listed["id"] == published_set and listed["submissions"] == 0
        taken = PracticeSetService().take(published_set)
        assert "correct_answer" not in taken["questions"][0]
        ids = [q["question_id"] for q in taken["questions"]]
        result = asyncio.run(PracticeSetService().submit(
            published_set, {ids[0]: "B", ids[1]: "False", ids[2]: "SECRET mass attracts"},
        ))
        assert [r["result"] for r in result["results"]] == ["correct", "incorrect", "correct"]
        assert result["score"] == 3 and result["max_score"] == 4
        assert result["answers_revealed"] and result["results"][1]["correct_answer"] == "True"

        from deeptutor.services.session.sqlite_store import get_sqlite_session_store

        entries = asyncio.run(get_sqlite_session_store().list_notebook_entries(limit=50))
    rows = entries["items"] if isinstance(entries, dict) else entries
    mine = [row for row in rows if row.get("material_id") == published_set]
    assert len(mine) == 3 and {row["source"] for row in mine} == {"practice_set"}

    with as_account("tina"):
        report = PracticeSetService().report(published_set)
    sara = next(s for s in report["students"] if s["username"] == "sara")
    sam = next(s for s in report["students"] if s["username"] == "sam")
    assert sara["completed"] and sara["first_score"] == 3 and not sam["completed"]
    assert [q["error_rate"] for q in report["questions"]] == [0.0, 1.0, 0.0]
    assert "SECRET" not in repr(report)


def test_outsiders_and_other_teachers_are_refused(published_set, as_account):
    with as_account("uma"):
        with pytest.raises(PracticeNotFound):
            PracticeSetService().take(published_set)
    with as_account("tom"):
        with pytest.raises(PracticeNotFound):
            PracticeSetService().report(published_set)
    with as_account("sara"):
        with pytest.raises(PracticeForbidden):
            PracticeSetService().create("Mine")


def test_a_new_revision_keeps_old_submissions(published_set, as_account, fake_judge):
    with as_account("sara"):
        ids = [q["question_id"] for q in PracticeSetService().take(published_set)["questions"]]
        asyncio.run(PracticeSetService().submit(published_set, {ids[0]: "B"}))
    with as_account("tina"):
        service = PracticeSetService()
        draft = service.get(published_set)["questions"]
        service.update(published_set, questions=draft[:1])
        assert service.publish(published_set)["current_revision"] == 2
        with pytest.raises(PracticeConflict):
            service.delete(published_set)
    with as_account("sara"):
        taken = PracticeSetService().take(published_set)
    assert len(taken["questions"]) == 1
    assert [s["revision"] for s in taken["submissions"]] == [1]


def test_answers_wait_for_the_deadline_when_the_teacher_says_so(published_set, as_account, fake_judge):
    import time

    with as_account("tina"):
        PracticeSetService().update(published_set, reveal="after_due", due_at=time.time() + 3600)
    with as_account("sara"):
        ids = [q["question_id"] for q in PracticeSetService().take(published_set)["questions"]]
        result = asyncio.run(PracticeSetService().submit(published_set, {ids[0]: "A"}))
        assert not result["answers_revealed"] and "correct_answer" not in result["results"][0]
        assert not result["late"]
