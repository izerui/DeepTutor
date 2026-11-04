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


# ── student permissions by class (§7) ──────────────────────


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
    app.include_router(stub, dependencies=[Depends(auth_router.require_learning_surface)])
    client = TestClient(app)

    def call(name, method, path):
        return client.request(method, path, headers={"Authorization": f"Bearer {name}"}).status_code

    return call


def _class_id(as_account) -> str:
    from deeptutor.classes.service import ClassService

    with as_account("tina"):
        [row] = ClassService().list_classes()
    return row["id"]


def test_partners_agents_and_co_writer_are_off_for_students_by_default(features_http):
    for path in ("/api/partners", "/api/subagents/connections", "/api/documents/history"):
        assert features_http("sara", "GET", path) == 403
        assert features_http("tina", "GET", path) == 200
    assert features_http("sara", "GET", "/api/courses") == 200  # reading one's courses is not creating
    assert features_http("sara", "POST", "/api/courses") == 200  # own courses default to allowed
    assert features_http("sara", "POST", "/api/task-board/cards") == 200


def test_a_class_turns_features_on_and_a_personal_setting_still_wins(features_http, as_account, teaching_users):
    from deeptutor.classes.service import ClassService
    from deeptutor.multi_user.grants import load_grant, save_grant

    class_id = _class_id(as_account)
    with as_account("adam"):
        ClassService().set_student_policy(class_id, {"allow_partners": True, "allow_personal_course": False})
    assert features_http("sara", "GET", "/api/partners") == 200
    assert features_http("sara", "POST", "/api/courses") == 403
    sam = teaching_users["sam"]["id"]
    grant = load_grant(sam)
    grant["student_policy"] = {"allow_partners": False}
    save_grant(sam, grant)
    assert features_http("sam", "GET", "/api/partners") == 403
    assert features_http("sam", "POST", "/api/courses") == 403  # unset personally: the class decides


def test_only_administrators_set_class_permissions_and_unknown_keys_are_dropped(as_account):
    from deeptutor.classes.service import ClassForbidden, ClassService

    class_id = _class_id(as_account)
    with as_account("tina"), pytest.raises(ClassForbidden):
        ClassService().set_student_policy(class_id, {"allow_partners": True})
    with as_account("adam"):
        detail = ClassService().set_student_policy(
            class_id, {"allow_agents": True, "made_up": True, "ai_daily_tokens": 5000, "allow_co_writer": "yes"},
        )
    assert detail["student_policy"] == {"allow_agents": True, "ai_daily_tokens": 5000}


# ── the daily AI limit (§8) ────────────────────────────────


def _spend(tokens: int, *, source: str = "chat", when: float | None = None) -> None:
    import time
    import uuid

    from deeptutor.services.llm.usage_ledger import ledger_path, record_call

    record_call(ledger_path(), {"call_id": uuid.uuid4().hex, "total_tokens": tokens},
                started_at=when or time.time(), source=source)


def _limit_class(as_account, tokens: int) -> str:
    from deeptutor.classes.service import ClassService

    class_id = _class_id(as_account)
    with as_account("adam"):
        ClassService().set_student_policy(class_id, {"ai_daily_tokens": tokens})
    return class_id


def test_a_student_over_todays_tokens_is_refused_and_teachers_never_are(as_account):
    from deeptutor.services.llm.usage_limit import AiLimitReached, admit_call, status_for_current_user

    _limit_class(as_account, 1000)
    with as_account("sara"):
        admit_call(None)
        _spend(600)
        _spend(500, when=1.0)  # an earlier day does not count
        admit_call(None)
        assert status_for_current_user() == {"limit": 1000, "used": 600, "remaining_percent": 40}
        _spend(400)
        with pytest.raises(AiLimitReached):
            admit_call(None)
    with as_account("tina"):
        _spend(10_000)
        admit_call(None)
        assert status_for_current_user()["limit"] == 0


def test_a_teachers_grading_is_neither_refused_nor_counted(as_account):
    from deeptutor.services.llm.usage_limit import AiLimitReached, admit_call, exempt, status_for_current_user

    _limit_class(as_account, 1000)
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

    _limit_class(as_account, 1000)
    turn = TurnUsage()
    with as_account("sara"):
        admit_call(turn)
        _spend(2000)  # the turn's own calls push past the limit
        admit_call(turn)  # still the same answer: not cut off
        with pytest.raises(AiLimitReached):
            admit_call(TurnUsage())


def test_the_limit_is_personal_then_the_widest_class_then_the_school_default(as_account, teaching_users):
    from deeptutor.classes.service import ClassService
    from deeptutor.multi_user.grants import load_grant, save_grant
    from deeptutor.multi_user.roles import effective_ai_daily_tokens
    from deeptutor.services.llm.usage_limit import set_school_default_daily_tokens

    sara = teaching_users["sara"]["id"]
    assert effective_ai_daily_tokens(sara) == 0
    set_school_default_daily_tokens(3000)
    assert effective_ai_daily_tokens(sara) == 3000
    class_id = _limit_class(as_account, 1000)
    with as_account("adam"):
        other = ClassService().create("Second class")
        ClassService().add_students(other["id"], [sara])
        ClassService().set_student_policy(other["id"], {"ai_daily_tokens": 8000})
    assert effective_ai_daily_tokens(sara) == 8000
    with as_account("adam"):
        ClassService().set_student_policy(class_id, {"ai_daily_tokens": 0})
    assert effective_ai_daily_tokens(sara) == 0  # one class without a limit
    grant = load_grant(sara)
    grant["student_policy"] = {"ai_daily_tokens": 200}
    save_grant(sara, grant)
    assert effective_ai_daily_tokens(sara) == 200


# ── practice sets: review, regrade, options, export, attempts (§6.1) ──


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
    with as_account("tom"), pytest.raises(Exception):  # a co-teacher of the class sees nothing
        PracticeReview().attempt(set_id, result["submission_id"])
    with as_account("adam"), pytest.raises(Exception):
        PracticeReview().attempt(set_id, result["submission_id"])


def test_a_regrade_changes_the_total_and_tells_the_student(as_account, teaching_users):
    from deeptutor.notifications import service as notifications
    from deeptutor.practice_sets.review import PracticeReview
    from deeptutor.practice_sets.service import PracticeError, PracticeSetService

    set_id = _published_set(as_account)
    result, ids = _answer(as_account, "sara", set_id, choice="B")
    assert result["score"] == 2.0  # B and "yes" right; the AI (offline) left two ungraded
    with as_account("tina"):
        assert _row(PracticeSetService().report(set_id), "sara")["ungraded"] == 2
        with pytest.raises(PracticeError):
            PracticeReview().regrade(set_id, result["submission_id"], ids[2], 5)
        attempt = PracticeReview().regrade(set_id, result["submission_id"], ids[2], 3)
    graded = next(q for q in attempt["questions"] if q["question_id"] == ids[2])
    assert (graded["result"], graded["points"], graded["regraded"]) == ("partial", 3.0, True)
    assert attempt["score"] == 5.0
    with as_account("tina"):
        student = _row(PracticeSetService().report(set_id), "sara")
    assert (student["first_score"], student["ungraded"]) == (5.0, 1)
    assert "practice_regraded" in [n["kind"] for n in notifications.list_for(teaching_users["sara"]["id"])]


def test_option_counts_use_each_students_first_attempt(as_account):
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
        student = _row(PracticeSetService().report(set_id), "sara")
        assert (student["attempts"], student["score"], student["best_score"]) == (2, 1.0, 2.0)
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
    assert rows[0][:3] == ["student", "in_scope", "attempts"] and rows[0][-4:] == ["Q1", "Q2", "Q3", "Q4"]
    sara = next(r for r in rows if r[0] == "sara")
    assert sara[1:3] == ["yes", "1"] and sara[-4:] == ["correct", "correct", "ungraded", "ungraded"]
    sam = next(r for r in rows if r[0] == "sam")
    assert sam[2] == "0"


def test_a_deleted_students_answers_go(as_account, teaching_users):
    from deeptutor.practice_sets import review, store

    set_id = _published_set(as_account)
    _answer(as_account, "sara", set_id)
    assert review.forget_student_answers(teaching_users["sara"]["id"]) == 1
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


# ── remedial practice (§3.1) ───────────────────────────────


def test_remedial_from_a_set_copies_the_picked_questions_and_suggests_who_got_them_wrong(as_account, monkeypatch):
    import asyncio

    from deeptutor.practice_sets.remedial import RemedialPractice
    from deeptutor.practice_sets.service import PracticeSetService

    set_id = _published_set(as_account)
    _answer(as_account, "sara", set_id, choice="A")  # wrong on Q1
    _answer(as_account, "sara", set_id, choice="B")  # the retry does not clear the first attempt
    _answer(as_account, "sam", set_id, choice="B")
    asked = {}

    async def generate(self, **kwargs):
        asked.update(kwargs)
        return [{"question_id": "x", "question": "4 - 2 = ?", "question_type": "short_answer", "options": {},
                 "correct_answer": "2", "explanation": "", "points": 1}]

    monkeypatch.setattr(PracticeSetService, "generate", generate)
    with as_account("tina"):
        q1 = PracticeSetService().report(set_id)["questions"][0]["question_id"]
        made = asyncio.run(RemedialPractice().from_set(set_id, [q1], similar=2))
    draft = made["set"]
    assert draft["status"] == "draft" and draft["title"] == "Remedial: Week 3"
    assert [q["question"] for q in draft["questions"]] == ["2 + 2 = ?", "4 - 2 = ?"]
    assert draft["questions"][0]["question_id"] != q1  # a copy, not the original
    assert asked["count"] == 2 and "2 + 2 = ?" in asked["topic"]
    assert [p["username"] for p in made["suggested_students"]] == ["sara"]
    with as_account("tom"), pytest.raises(Exception):  # only the set's author
        asyncio.run(RemedialPractice().from_set(set_id, [q1]))


def test_remedial_from_a_path_asks_for_the_picked_points(as_account, teacher_draft, publish_template, monkeypatch,
                                                        teaching_users):
    import asyncio

    from deeptutor.practice_sets.remedial import RemedialPractice
    from deeptutor.practice_sets.service import PracticeSetService
    from deeptutor.teaching.store import TeachingStore

    teacher_draft()
    template_id = publish_template(students=("sara",))["template"]["id"]
    revision = TeachingStore().get_revision(template_id, 1)
    kp = revision.outline.modules[0].knowledge_points[0]
    asked = {}

    async def generate(self, **kwargs):
        asked.update(kwargs)
        return [{"question_id": "", "question": "Why?", "question_type": "short_answer", "options": {},
                 "correct_answer": "", "explanation": "", "points": 1}]

    monkeypatch.setattr(PracticeSetService, "generate", generate)
    outsider = teaching_users["uma"]["id"]
    with as_account("tina"):
        made = asyncio.run(RemedialPractice().from_path(template_id, [kp.id], [teaching_users["sara"]["id"], outsider]))
    assert kp.name in asked["topic"] and made["set"]["questions"][0]["question"] == "Why?"
    assert [p["username"] for p in made["suggested_students"]] == ["sara"]  # not someone outside her classes
    with as_account("tom"), pytest.raises(Exception):
        asyncio.run(RemedialPractice().from_path(template_id, [kp.id]))


def test_a_student_gets_similar_questions_in_their_own_bank_not_as_mistakes(as_account, monkeypatch):
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


# ── needs attention, workbench, reports (§5, §6.2, §6.3) ───


@pytest.fixture
def fresh_attention():
    from deeptutor.classes import attention

    attention.clear_cache()
    yield attention
    attention.clear_cache()


def _flags(attention, teaching_users, name):
    return {f["kind"]: f for f in attention.for_teacher(teaching_users["tina"]["id"])[teaching_users[name]["id"]]}


def test_a_student_with_no_learning_for_a_week_is_inactive(as_account, teaching_users, fresh_attention, monkeypatch):
    import time

    set_id = _published_set(as_account)
    _answer(as_account, "sara", set_id, choice="B")
    assert "inactive" not in _flags(fresh_attention, teaching_users, "sara")
    assert _flags(fresh_attention, teaching_users, "sam")["inactive"]["days"] is None  # never learned
    later = time.time() + 8 * 86400
    monkeypatch.setattr("deeptutor.classes.attention._now", lambda: later)
    fresh_attention.clear_cache()
    assert _flags(fresh_attention, teaching_users, "sara")["inactive"]["days"] == 8


def test_overdue_counts_only_this_teachers_tasks(as_account, teaching_users, fresh_attention, monkeypatch):
    import time

    from deeptutor.tasks.service import TaskService

    for teacher in ("tina", "tom"):
        with as_account(teacher):
            task = TaskService().create("note", f"Read by {teacher}")
            TaskService().update(task["id"], due_at=time.time() + 60)
            TaskService().publish(task["id"])
    monkeypatch.setattr("deeptutor.tasks.service._now", lambda: time.time() + 3600)
    assert _flags(fresh_attention, teaching_users, "sara")["overdue"]["count"] == 1


def test_mistakes_rising_needs_five_and_twice_the_week_before(as_account, teaching_users, fresh_attention):
    import time

    from deeptutor.practice_sets import store

    set_id = _published_set(as_account)
    for _ in range(3):
        _answer(as_account, "sara", set_id, choice="A", truth="no")  # two wrong each time: 6 this week
    assert _flags(fresh_attention, teaching_users, "sara")["mistakes_rising"] == {
        "kind": "mistakes_rising", "recent": 6, "before": 0}
    # The same number the week before: not rising.
    with store.write() as conn:
        rows = conn.execute("SELECT results_json FROM practice_submissions").fetchall()
        for index, row in enumerate(rows):
            conn.execute(
                "INSERT INTO practice_submissions (id, set_id, revision, student_id, submitted_at, late, score, "
                "max_score, results_json) VALUES (?, ?, 1, ?, ?, 0, 0, 7, ?)",
                (f"old{index}", set_id, teaching_users["sara"]["id"], time.time() - 10 * 86400, row[0]),
            )
    fresh_attention.clear_cache()
    assert "mistakes_rising" not in _flags(fresh_attention, teaching_users, "sara")


async def _no_suggestion(_task_id, _student_id):
    return None


def test_the_workbench_lists_hand_ins_classes_and_what_was_sent(as_account, teaching_users, fresh_attention,
                                                               monkeypatch):
    from deeptutor.tasks.service import StudentTasks, TaskService
    from deeptutor.teaching.workbench import workbench

    with as_account("tina"):
        task = TaskService().create("homework", "Essay")
        TaskService().publish(task["id"])
    import asyncio

    monkeypatch.setattr("deeptutor.tasks.service.suggest_grade", _no_suggestion)
    with as_account("sara"):
        asyncio.run(StudentTasks().submit(task["id"], "Waves carry energy.", []))
    set_id = _published_set(as_account)
    with as_account("tina"):
        board = workbench()
    assert [(i["task_title"], i["username"]) for i in board["to_grade"]] == [("Essay", "sara")]
    assert board["classes"][0]["student_count"] == 2
    kinds = {(i["kind"], i["id"]) for i in board["sent"]}
    assert ("task", task["id"]) in kinds and ("practice_set", set_id) in kinds
    sam = next(row for row in board["attention"] if row["username"] == "sam")
    assert sam["class_id"] and sam["flags"][0]["kind"] == "inactive"
    with as_account("adam"):
        assert workbench() == {"role": "admin"}  # administrators see no students


def test_the_class_report_has_periods_trends_flags_and_exports(as_account, teaching_users, fresh_attention):
    import csv
    import io

    from deeptutor.classes.report import ReportService

    _published_set(as_account)
    with as_account("tina"):
        class_id = _class_id(as_account)
        report = ReportService().class_report(class_id, days=7)
        assert report["period_days"] == 7
        row = report["students"][0]
        assert len(row["overall"]["trend"]) == 8 and "period_minutes" in row["overall"]
        assert any(flag["kind"] == "inactive" for flag in row["flags"])
        assert ReportService().class_report(class_id, days=5)["period_days"] is None  # only 7 or 30
        rows = list(csv.reader(io.StringIO(ReportService().export_csv(class_id))))
    assert rows[0][0] == "student" and {r[0] for r in rows[1:]} == {"sara", "sam"}
    assert "inactive" in rows[1][-1]


def test_a_point_answered_wrong_for_a_week_is_stuck_and_the_report_shows_its_numbers(
    sara_instance, as_account, teaching_users, fresh_attention,
):
    import time

    from deeptutor.classes.report import ReportService
    from deeptutor.learning.models import QuizAttempt
    from deeptutor.learning.storage import LearningStore

    with as_account("sara"):
        store = LearningStore()
        progress = store.load(sara_instance.path_id)
        kp = progress.modules[0].knowledge_points[0]
        old = time.time() - 9 * 86400
        progress.quiz_attempts.extend(
            QuizAttempt(question_id=f"q{i}", knowledge_point_id=kp.id, is_correct=False, result="incorrect",
                        timestamp=old + i * 3600)
            for i in range(3)
        )
        store.save(progress)
    flags = fresh_attention.for_teacher(teaching_users["tina"]["id"])[teaching_users["sara"]["id"]]
    stuck = next(f for f in flags if f["kind"] == "stuck")
    assert (stuck["kp_id"], stuck["days"], stuck["wrong"]) == (kp.id, 9, 3)
    assert not any(f["kind"] == "stuck" for f in fresh_attention.for_teacher(teaching_users["tom"]["id"]).get(
        teaching_users["sara"]["id"], []))  # Tom's report has no stake in Tina's path
    with as_account("tina"):
        detail = ReportService().student_report(_class_id(as_account), teaching_users["sara"]["id"])
    point = next(p for p in detail["teacher_paths"][0]["knowledge_points"] if p["id"] == kp.id)
    assert (point["answers"], point["wrong"], point["stuck_days"]) == (3, 3, 9)


def test_the_class_sees_who_has_mastered_each_point_of_a_path(sara_instance, as_account, teaching_users):
    from deeptutor.classes.report import ReportService
    from deeptutor.classes.service import ClassNotFound

    with as_account("tina"):
        class_id = _class_id(as_account)
        points = ReportService().class_path_points(class_id, sara_instance.template_id)["points"]
    assert points and all(p["students"] == 1 for p in points)
    assert points[0]["not_mastered"] == [teaching_users["sara"]["id"]]
    with as_account("tom"), pytest.raises(ClassNotFound):
        ReportService().class_path_points(class_id, sara_instance.template_id)


def test_the_class_permission_route_accepts_every_key():
    from deeptutor.api.routers.classes import StudentPolicyUpdate
    from deeptutor.multi_user.roles import STUDENT_POLICY_DEFAULTS

    body = {key: True for key in STUDENT_POLICY_DEFAULTS} | {"ai_daily_tokens": 5000}
    assert StudentPolicyUpdate(**body).model_dump(exclude_none=True) == body
