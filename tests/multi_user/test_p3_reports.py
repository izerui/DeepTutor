"""P3d: teacher learning reports (spec 2026-10-29 D17, option B)."""

from __future__ import annotations

import sqlite3
import time

import pytest

from deeptutor.classes.report import ReportService
from deeptutor.classes.service import ClassForbidden, ClassService


def _class_id(as_account) -> str:
    with as_account("tina"):
        [row] = ClassService().list_classes()
    return row["id"]


def _sara_studies(as_account, path_id: str) -> None:
    from deeptutor.learning.models import QuizAttempt
    from deeptutor.learning.storage import LearningStore

    with as_account("sara"):
        store = LearningStore()
        point = store.load(path_id).modules[0].knowledge_points[0].id

        def apply(tx):
            tx.progress.mastery_levels[point] = 0.9
            tx.progress.quiz_attempts.append(QuizAttempt(
                question_id="q1", knowledge_point_id=point, user_answer="SECRET ANSWER",
                is_correct=False, result="incorrect",
            ))
            tx.touch()

        store.mutate(path_id, apply)


def _sara_question_bank(as_account) -> None:
    from deeptutor.multi_user.paths import get_current_path_service
    from deeptutor.services.session.sqlite_store import SQLiteSessionStore

    with as_account("sara"):
        db = get_current_path_service().get_chat_history_db()
    SQLiteSessionStore(db)  # creates the schema
    now = time.time()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO notebook_entries (origin_ref, question_id, question, result, created_at, updated_at) "
            "VALUES ('o', 'q', 'what?', 'incorrect', ?, ?)",
            (now, now),
        )
        conn.execute("INSERT INTO sessions (id, title, created_at, updated_at) VALUES ('s1', 't', ?, ?)", (now, now))
        conn.execute(
            "INSERT INTO turns (id, session_id, created_at, updated_at, finished_at) VALUES ('t1', 's1', ?, ?, ?)",
            (now - 600, now, now),
        )


def test_a_teacher_sees_progress_on_their_path_and_an_overall_summary(sara_instance, as_account):
    _sara_studies(as_account, sara_instance.path_id)
    _sara_question_bank(as_account)
    class_id = _class_id(as_account)
    with as_account("tina"):
        report = ReportService().class_report(class_id)
        detail = ReportService().student_report(
            class_id, next(s["id"] for s in report["students"] if s["username"] == "sara")
        )
    sara = next(s for s in report["students"] if s["username"] == "sara")
    [path] = sara["teacher_paths"]
    assert path["title"] == "Calculus I" and path["attempts"]["incorrect"] == 1
    assert sara["overall"]["question_bank"]["incorrect"] == 1
    assert sara["overall"]["study_minutes_total"] == 10
    assert detail["teacher_paths"][0]["knowledge_points"][0]["mastery"] > 0
    assert "SECRET ANSWER" not in repr(report) + repr(detail)


def test_another_teacher_sees_no_path_details_from_tina(sara_instance, as_account):
    class_id = _class_id(as_account)
    with as_account("tom"):
        report = ReportService().class_report(class_id)
    sara = next(s for s in report["students"] if s["username"] == "sara")
    assert sara["teacher_paths"] == []
    assert sara["overall"]["path_count"] == 1


def test_admins_and_students_cannot_read_reports(sara_instance, as_account):
    class_id = _class_id(as_account)
    for name in ("adam", "sara"):
        with as_account(name):
            with pytest.raises(ClassForbidden):
                ReportService().class_report(class_id)
