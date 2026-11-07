"""Batch 3 of the 2026-11-02 plan.

Spec: docs/superpowers/specs/2026-11-03-batch3-design.md
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest


# ── sign-up and sign-in limits (§9) ────────────────────────


@pytest.fixture
def auth_client(seed_user, monkeypatch):
    import deeptutor.api.routers.auth as auth_router

    seed_user("boss", role="admin")
    seed_user("sara", role="student", password="Passw0rd!x")
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "POCKETBASE_ENABLED", False)
    monkeypatch.setattr("deeptutor.services.auth.AUTH_ENABLED", True)
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    return TestClient(app)


def _register(client, name, ip="1.2.3.4"):
    return client.post(
        "/api/auth/register", json={"username": name, "password": "password-123"},
        headers={"x-deeptutor-client-ip": ip},
    )


def test_one_address_signs_up_at_most_five_accounts_an_hour(auth_client, monkeypatch):
    import deeptutor.api.routers.auth as auth_router
    from deeptutor.multi_user import rate_limit

    # The limit is configurable (generous by default for campus networks); test it at 5.
    monkeypatch.setattr(rate_limit, "REGISTER_PER_ADDRESS", (5, 3600))

    # The test client's peer counts as the trusted frontend proxy.
    monkeypatch.setattr(auth_router, "TRUSTED_FRONTEND_PROXY_IPS", frozenset({"testclient"}))
    monkeypatch.setattr(auth_router, "_client_address", lambda request: request.headers.get("x-deeptutor-client-ip", ""))
    for index in range(5):
        assert _register(auth_client, f"walkin{index}").status_code == 201
    refused = _register(auth_client, "walkin5")
    assert refused.status_code == 429 and "Too many sign-ups" in refused.json()["detail"]
    assert _register(auth_client, "elsewhere", ip="5.6.7.8").status_code == 201


def test_the_whole_site_caps_sign_ups_when_addresses_vary(auth_client, monkeypatch):
    from deeptutor.multi_user import rate_limit

    monkeypatch.setattr(rate_limit, "REGISTER_SITE_WIDE", (3, 3600))
    import deeptutor.api.routers.auth as auth_router

    monkeypatch.setattr(auth_router, "_client_address", lambda request: request.headers.get("x-deeptutor-client-ip", ""))
    for index in range(3):
        assert _register(auth_client, f"spread{index}", ip=f"10.0.0.{index}").status_code == 201
    assert _register(auth_client, "spread3", ip="10.0.0.9").status_code == 429


def test_repeated_wrong_passwords_pause_sign_in_without_naming_the_reason(auth_client):
    def attempt(password):
        return auth_client.post("/api/auth/login", json={"username": "sara", "password": password})

    for _ in range(10):
        assert attempt("wrong-password").status_code == 401
    paused = attempt("Passw0rd!x")  # even the right password waits
    assert paused.status_code == 429
    assert paused.json()["detail"] == "Too many attempts. Try again in 15 minutes."
    unknown = auth_client.post("/api/auth/login", json={"username": "nobody", "password": "x"})
    assert unknown.status_code == 401  # no hint whether a name exists


def test_a_successful_sign_in_clears_the_count(auth_client):
    for _ in range(9):
        auth_client.post("/api/auth/login", json={"username": "sara", "password": "wrong"})
    assert auth_client.post("/api/auth/login", json={"username": "sara", "password": "Passw0rd!x"}).status_code == 200
    for _ in range(9):
        auth_client.post("/api/auth/login", json={"username": "sara", "password": "wrong"})
    assert auth_client.post("/api/auth/login", json={"username": "sara", "password": "Passw0rd!x"}).status_code == 200


def test_the_frontend_address_is_trusted_only_from_the_proxy():
    from starlette.requests import Request

    import deeptutor.api.routers.auth as auth_router

    def request(peer, header):
        return Request({"type": "http", "client": (peer, 1), "headers": [(b"x-deeptutor-client-ip", header.encode())]})

    assert auth_router._client_address(request("127.0.0.1", "8.8.8.8")) == "8.8.8.8"
    assert auth_router._client_address(request("203.0.113.5", "8.8.8.8")) == "203.0.113.5"
    # Through the proxy without the ingress's address: unknown, not everyone's shared "127.0.0.1".
    assert auth_router._client_address(request("127.0.0.1", "not-an-ip")) == ""


# ── learner permissions (§7) ───────────────────────────────


@pytest.fixture
def features_http(teaching_users, monkeypatch):
    """Stand-in routes for each feature behind the real learning-surface guard."""
    from fastapi import APIRouter, Depends

    import deeptutor.api.routers.auth as auth_router
    from deeptutor.services.auth import TokenPayload

    tokens = {
        name: TokenPayload(username=name, role=record["role"], user_id=record["id"])
        for name, record in teaching_users.items()
    }
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "decode_token", lambda token: tokens.get(token))
    stub = APIRouter()
    for path in ("/api/partners", "/api/subagents/connections", "/api/documents/history", "/api/courses"):
        stub.add_api_route(path, lambda: {"ok": True}, methods=["GET"])
    stub.add_api_route("/api/courses", lambda: {"ok": True}, methods=["POST"])
    stub.add_api_route("/api/task-board/cards", lambda: {"ok": True}, methods=["POST"])
    app = FastAPI()
    app.include_router(stub, dependencies=[Depends(auth_router.require_auth)])
    client = TestClient(app)

    def call(name, method, path):
        return client.request(method, path, headers={"Authorization": f"Bearer {name}"}).status_code

    return call


#: The ``deliver`` / ``in_course`` fixtures of the running test (module helpers use them).
_fixtures: dict[str, object] = {}


@pytest.fixture(autouse=True)
def _remember(deliver, in_course):
    _fixtures.update(deliver=deliver, in_course=in_course)


def test_every_feature_is_open_to_every_learner_without_a_grant(features_http, teaching_users):
    """Spec 2026-11-05 §2: no feature switches; a stored old switch is not read."""
    from deeptutor.multi_user.grants import grant_path

    import json

    grant_path(teaching_users["sam"]["id"]).write_text(json.dumps({"student_policy": {
        "allow_partners": False, "allow_agents": False, "allow_co_writer": False,
        "allow_personal_course": False, "allow_personal_task_card": False,
    }}), encoding="utf-8")
    for name in ("sara", "sam", "tina"):
        for path in ("/api/partners", "/api/subagents/connections", "/api/documents/history", "/api/courses"):
            assert features_http(name, "GET", path) == 200
        assert features_http(name, "POST", "/api/courses") == 200
        assert features_http(name, "POST", "/api/task-board/cards") == 200


# ── the daily AI limit (§8) ────────────────────────────────


def _spend(tokens: int, *, source: str = "chat", when: float | None = None) -> None:
    import time
    import uuid

    from deeptutor.services.llm.usage_ledger import ledger_path, record_call

    record_call(ledger_path(), {"call_id": uuid.uuid4().hex, "total_tokens": tokens},
                started_at=when or time.time(), source=source)


def _limit_sara(tokens: int) -> None:
    from deeptutor.multi_user.grants import load_grant, save_grant
    from deeptutor.multi_user.identity import get_user

    sara = get_user("sara")["id"]
    save_grant(sara, {**load_grant(sara), "ai_daily_tokens": tokens})


def test_a_learner_over_todays_tokens_is_refused_and_administrators_never_are(as_account):
    from deeptutor.services.llm.usage_limit import AiLimitReached, admit_call, status_for_current_user

    _limit_sara(1000)
    with as_account("sara"):
        admit_call(None)
        _spend(600)
        _spend(500, when=1.0)  # an earlier day does not count
        admit_call(None)
        assert status_for_current_user() == {"limit": 1000, "used": 600, "remaining_percent": 40}
        _spend(400)
        with pytest.raises(AiLimitReached):
            admit_call(None)
    with as_account("adam"):
        _spend(10_000)
        admit_call(None)
        assert status_for_current_user()["limit"] == 0


def test_a_authors_grading_is_neither_refused_nor_counted(as_account):
    from deeptutor.services.llm.usage_limit import AiLimitReached, admit_call, exempt, status_for_current_user

    _limit_sara(1000)
    with as_account("sara"):
        _spend(5000, source="exempt:practice")  # recorded under ``exempt``
        assert status_for_current_user()["used"] == 0
        _spend(1000)
        with exempt():
            admit_call(None)
        with pytest.raises(AiLimitReached):
            admit_call(None)


def test_an_admitted_turn_runs_to_its_end(as_account):
    from deeptutor.services.llm.metrics import TurnUsage
    from deeptutor.services.llm.usage_limit import AiLimitReached, admit_call

    _limit_sara(1000)
    turn = TurnUsage()
    with as_account("sara"):
        admit_call(turn)
        _spend(2000)  # the turn's own calls push past the limit
        admit_call(turn)  # still the same answer: not cut off
        with pytest.raises(AiLimitReached):
            admit_call(TurnUsage())


def test_the_limit_is_personal_then_the_school_default(as_account, teaching_users):
    from deeptutor.multi_user.grants import load_grant, save_grant
    from deeptutor.multi_user.roles import effective_ai_daily_tokens
    from deeptutor.services.llm.usage_limit import set_school_default_daily_tokens

    sara = teaching_users["sara"]["id"]
    assert effective_ai_daily_tokens(sara) == 0
    set_school_default_daily_tokens(3000)
    assert effective_ai_daily_tokens(sara) == 3000
    _limit_sara(0)
    assert effective_ai_daily_tokens(sara) == 0  # her own "no limit" wins
    grant = load_grant(sara)
    grant["ai_daily_tokens"] = 200
    save_grant(sara, grant)
    assert effective_ai_daily_tokens(sara) == 200


def test_a_personal_limit_stored_by_the_old_student_policy_still_counts(teaching_users):
    """Spec 2026-11-05 §3: the old switches are not read, the personal AI limit is."""
    import json

    from deeptutor.multi_user.grants import grant_path, load_grant
    from deeptutor.multi_user.roles import effective_ai_daily_tokens

    sara = teaching_users["sara"]["id"]
    grant_path(sara).write_text(
        json.dumps({"student_policy": {"allow_partners": False, "ai_daily_tokens": 700}}), encoding="utf-8",
    )
    assert effective_ai_daily_tokens(sara) == 700
    assert load_grant(sara)["ai_daily_tokens"] == 700
    assert "student_policy" not in load_grant(sara)


# ── practice sets: review, options, export, attempts (§6.1) ──


SET_QUESTIONS = [
    {"question": "2 + 2 = ?", "question_type": "choice", "options": {"A": "3", "B": "4"},
     "correct_answer": "B", "explanation": "Add.", "points": 1},
    {"question": "The sky is blue.", "question_type": "concept", "correct_answer": "True", "points": 1},
    {"question": "Explain gravity.", "question_type": "short_answer", "correct_answer": "Mass attracts mass.",
     "points": 4},
    {"question": "Capital of France?", "question_type": "fill_in_blank", "correct_answer": "Paris", "points": 1},
]


@pytest.fixture(autouse=True)
def _offline_judge(monkeypatch):
    async def ungraded(pending):
        return {}

    monkeypatch.setattr("deeptutor.practice_sets.service._judge", ungraded)


def _row(report, name):
    return next(s for s in report["students"] if s["username"] == name)


def _published_set(as_account, **settings) -> str:
    from deeptutor.practice_sets.service import PracticeSetService

    with as_account("tina"):
        service = PracticeSetService()
        created = service.create("Week 3")
        service.update(created["id"], questions=SET_QUESTIONS, **settings)
        service.publish(created["id"])
    _fixtures["deliver"]("practice_set", created["id"], "sara", "sam")  # they joined it
    return created["id"]


def _answer(as_account, name, set_id, choice="A", truth="yes", text="Things fall.", blank="Lyon"):
    import asyncio

    from deeptutor.practice_sets.service import PracticeSetService

    with as_account(name):
        questions = PracticeSetService().take(set_id)["questions"]
        answers = dict(zip([q["question_id"] for q in questions], [choice, truth, text, blank]))
        return asyncio.run(PracticeSetService().submit(set_id, answers)), [q["question_id"] for q in questions]


def test_the_author_sees_subjective_text_and_picked_options_but_not_blanks(as_account):
    from deeptutor.practice_sets.review import PracticeReview

    set_id = _published_set(as_account)
    result, ids = _answer(as_account, "sara", set_id, choice="b", truth="对")
    with as_account("tina"):
        attempt = PracticeReview().attempt(set_id, result["submission_id"])
    answers = {q["question_id"]: q["answer"] for q in attempt["questions"]}
    assert answers == {ids[0]: "B", ids[1]: "TRUE", ids[2]: "Things fall.", ids[3]: None}
    with as_account("tom"), pytest.raises(Exception):  # another learner sees nothing
        PracticeReview().attempt(set_id, result["submission_id"])
    with as_account("adam"):  # an administrator reads it (spec 2026-11-05 §2)
        assert PracticeReview().attempt(set_id, result["submission_id"])["questions"] == attempt["questions"]


def test_option_counts_use_each_learners_first_attempt(as_account):
    from deeptutor.practice_sets.service import PracticeSetService

    set_id = _published_set(as_account)
    _answer(as_account, "sara", set_id, choice="A", truth="no")
    _answer(as_account, "sara", set_id, choice="B")  # a retry does not count twice
    _answer(as_account, "sam", set_id, choice="B", truth="yes")
    with as_account("tina"):
        questions = PracticeSetService().report(set_id)["questions"]
    assert questions[0]["option_counts"] == {"A": 1, "B": 1}
    assert questions[1]["option_counts"] == {"TRUE": 1, "FALSE": 1}
    assert questions[2]["option_counts"] is None


def test_attempts_run_out_and_the_first_one_can_be_what_counts(as_account):
    from deeptutor.practice_sets.service import PracticeConflict, PracticeSetService

    set_id = _published_set(as_account, attempts_limit=2, score_mode="first")
    _answer(as_account, "sara", set_id, choice="A")
    _answer(as_account, "sara", set_id, choice="B")
    with as_account("sara"):
        assert PracticeSetService().take(set_id)["attempts_left"] == 0
    with pytest.raises(PracticeConflict):
        _answer(as_account, "sara", set_id, choice="B")
    with as_account("tina"):
        learner = _row(PracticeSetService().report(set_id), "sara")
        assert (learner["attempts"], learner["score"], learner["best_score"]) == (2, 1.0, 2.0)
        PracticeSetService().update(set_id, attempts_limit=None)  # lifting it lets them answer again
    _answer(as_account, "sara", set_id, choice="B")


def test_the_results_export_as_csv(as_account):
    import csv
    import io

    from deeptutor.practice_sets.review import PracticeReview

    set_id = _published_set(as_account)
    _answer(as_account, "sara", set_id, choice="B")
    with as_account("tina"):
        rows = list(csv.reader(io.StringIO(PracticeReview().export_csv(set_id))))
    assert rows[0][:3] == ["learner", "in_scope", "attempts"] and rows[0][-4:] == ["Q1", "Q2", "Q3", "Q4"]
    sara = next(r for r in rows if r[0] == "sara")
    assert sara[1:3] == ["yes", "1"] and sara[-4:] == ["correct", "correct", "ungraded", "ungraded"]
    sam = next(r for r in rows if r[0] == "sam")
    assert sam[2] == "0"


def test_a_deleted_learners_answers_go(as_account, teaching_users):
    from deeptutor.practice_sets import review, store

    set_id = _published_set(as_account)
    _answer(as_account, "sara", set_id)
    assert review.forget_learner_answers(teaching_users["sara"]["id"]) == 1
    with store.read() as conn:
        assert {r[0] for r in conn.execute("SELECT answers_json FROM practice_submissions")} == {"{}"}


# ── questions from a book (§4) ─────────────────────────────


def test_book_questions_use_only_the_chosen_chapters_and_readable_books(as_account, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from deeptutor.practice_sets import service as practice

    chapters = [SimpleNamespace(id="c1", title="Waves", page_ids=["p1"]),
                SimpleNamespace(id="c2", title="Optics", page_ids=["p2", "p3"]),
                SimpleNamespace(id="c3", title="Empty", page_ids=[])]
    storage = SimpleNamespace(load_spine=lambda book_id: SimpleNamespace(chapters=chapters),
                              load_book=lambda book_id: object())
    monkeypatch.setattr("deeptutor.multi_user.book_access.readable_book_storage",
                        lambda ref: storage if ref == "book:mine" else None)
    seen = []

    def fake_context(refs, *, storage, max_chars):
        seen.append((refs[0]["page_ids"], max_chars))
        return SimpleNamespace(text=f"text of {refs[0]['page_ids']}")

    monkeypatch.setattr("deeptutor.book.context.build_book_context", fake_context)
    topic, material = practice.book_material("book:mine", ["c2"])
    assert topic == "Optics" and "text of ['p2', 'p3']" in material and seen == [(["p2", "p3"], 24_000)]
    seen.clear()
    topic, _ = practice.book_material("book:mine", [])
    assert topic == "Waves; Optics; Empty" and [pages for pages, _ in seen] == [["p1"], ["p2", "p3"]]
    with pytest.raises(practice.PracticeNotFound):
        practice.book_material("book:someone-elses", [])
    with pytest.raises(practice.PracticeError):
        practice.book_material("book:mine", ["c3"])  # nothing written yet

    captured = {}

    class Pipeline:
        def __init__(self, **kwargs):
            pass

        async def run(self, **kwargs):
            captured.update(kwargs)
            return {"summary": {}}

    monkeypatch.setattr("deeptutor.agents.question.pipeline.QuestionPipeline", Pipeline)
    monkeypatch.setattr("deeptutor.book.blocks.quiz.QuizGenerator._extract_questions",
                        staticmethod(lambda summary: [{"question": "Q?", "question_type": "short_answer"}]))
    with as_account("tina"):
        questions = asyncio.run(practice.PracticeSetService().generate(
            topic="", count=3, book_ref="book:mine", chapter_ids=["c1"]))
    assert captured["user_message"] == "Waves" and "text of ['p1']" in captured["conversation_context"]
    assert questions[0]["question"] == "Q?"


# ── remedial practice (§3.2; an author's remedial sets went, spec 2026-11-05) ──


def test_a_learner_gets_similar_questions_in_their_own_bank_not_as_mistakes(as_account, monkeypatch):
    import asyncio

    from deeptutor.services.practice import remedial
    from deeptutor.services.session.sqlite_store import get_sqlite_session_store

    set_id = _published_set(as_account)
    _answer(as_account, "sara", set_id, choice="A")  # Q1 wrong: a mistake once answers show

    async def generate(**kwargs):
        assert "2 + 2 = ?" in kwargs["topic"] and kwargs["count"] == 3  # at least three
        return [{"question_id": f"g{i}", "question": f"New {i}", "question_type": "short_answer", "options": {},
                 "correct_answer": "x", "explanation": "", "points": 1} for i in range(3)]

    monkeypatch.setattr("deeptutor.practice_sets.service.generate_questions", generate)

    def mistakes(bank):
        return asyncio.run(bank.list_notebook_entries(mistakes_only=True))["items"]

    with as_account("sara"):
        bank = get_sqlite_session_store()
        [mistake] = [e for e in mistakes(bank) if e["question"] == "2 + 2 = ?"]
        made = remedial_run(remedial, [mistake["id"]], 2)
        assert len(made["entry_ids"]) == 3
        entries = [asyncio.run(bank.get_notebook_entry(i)) for i in made["entry_ids"]]
        assert {e["source"] for e in entries} == {"remedial"} and {e["result"] for e in entries} == {"ungraded"}
        assert {e["id"] for e in mistakes(bank)}.isdisjoint(made["entry_ids"])
    with as_account("sam"), pytest.raises(remedial.RemedialError):  # not in sam's bank
        remedial_run(remedial, [mistake["id"]], 3)


def remedial_run(module, ids, count):
    import asyncio

    return asyncio.run(module.similar_questions(ids, count))


# ── the workbench (§5.1) ───────────────────────────────────


def test_the_workbench_lists_what_was_sent(as_account, teaching_users):
    from deeptutor.tasks.service import TaskService
    from deeptutor.teaching.workbench import workbench

    with as_account("tina"):
        task = TaskService().create("note", "Read")
    course_id = _fixtures["in_course"](task["id"])
    set_id = _published_set(as_account)
    with as_account("tina"):
        board = workbench()
    assert "to_grade" not in board  # homework was removed (spec 2026-11-05)
    assert "classes" not in board
    kinds = {(i["kind"], i["id"]) for i in board["sent"]}
    assert {("task", task["id"]), ("practice_set", set_id), ("course", course_id)} <= kinds
    # No "learners who need attention" (spec 2026-11-05 remove-teacher-management §1.3).
    assert set(board) == {"role", "sent"}
    with as_account("adam"):
        assert workbench() == {"role": "admin"}  # administrators see no learners


def test_answers_the_ai_could_not_grade_are_graded_when_the_set_is_opened_again(as_account, monkeypatch):
    """Spec 2026-11-06: no attempt is used up; the learner's or the author's next opening grades them."""
    import asyncio

    from deeptutor.practice_sets import store
    from deeptutor.practice_sets.service import PracticeSetService, judge_ungraded

    set_id = _published_set(as_account, attempts_limit=1)
    result, ids = _answer(as_account, "sara", set_id, choice="B", truth="yes", blank="Paris")
    assert result["score"] == 3.0  # the short answer (4 points) was left ungraded
    assert next(r for r in result["results"] if r["question_id"] == ids[2])["result"] == "ungraded"

    async def judge_online(pending):
        return {q["question_id"]: {"result": "correct", "feedback": ""} for q, _answer in pending}

    monkeypatch.setattr("deeptutor.practice_sets.service._judge", judge_online)
    # The author opening the results grades it; nobody else's attempt is touched.
    assert asyncio.run(judge_ungraded(set_id)) == 1
    with store.read() as conn:
        row = conn.execute("SELECT score, results_json FROM practice_submissions WHERE id = ?",
                           (result["submission_id"],)).fetchone()
    assert row["score"] == 7.0 and '"ungraded"' not in row["results_json"]
    with as_account("tina"):
        assert _row(PracticeSetService().report(set_id), "sara")["ungraded"] == 0
    with as_account("sara"):
        view = PracticeSetService().take(set_id)
    assert len(view["submissions"]) == 1  # still one attempt of one
    assert asyncio.run(judge_ungraded(set_id)) == 0  # nothing left


def test_a_learner_opening_the_set_gets_the_ungraded_answer_graded(as_account, monkeypatch):
    import asyncio

    from deeptutor.api.routers import practice_sets as router

    set_id = _published_set(as_account)
    result, ids = _answer(as_account, "sara", set_id, choice="B", truth="yes", blank="Paris")

    async def judge_online(pending):
        return {q["question_id"]: {"result": "partial", "feedback": ""} for q, _answer in pending}

    monkeypatch.setattr("deeptutor.practice_sets.service._judge", judge_online)
    with as_account("sara"):
        view = asyncio.run(router.take_set(set_id))["set"]
    graded = next(r for r in view["latest_results"] if r["question_id"] == ids[2])
    assert graded["result"] == "partial" and view["submissions"][-1]["score"] == 5.0
