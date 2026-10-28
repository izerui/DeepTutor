"""Batch 3 / P6: turn refusal logging, change-suggestion fallback, history
admission across switches, off-loop store construction, and the original
content scope guard.

B3-9: a refused teacher-path turn is an expected outcome, logged as one INFO
line without a stack; the change-suggestion fallback warns with the stack.
T4-M2/M3: the reason length is one constant; a raising model falls back to
the default verdicts. B2-15 leftovers: the turn-start admission is recorded
through a public method, and history survives a switch onto another path of
the same template whose allowlist is a superset. T9-M4: a path switch never
constructs ``LearningStore`` on the event loop. B3-10: the context variables
set by ``enter_mastery_template_scope`` are never reset, so no route it scopes
may run work after the response (``BackgroundTasks``) in a shared context.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from types import SimpleNamespace

from fastapi import BackgroundTasks, Depends, FastAPI
import pytest

from deeptutor.capabilities.mastery import turn_scope
from deeptutor.capabilities.mastery.turn_scope import MasteryTurn, _Admission
from deeptutor.learning.models import KnowledgePoint, KnowledgeType
from deeptutor.teaching import changes
from deeptutor.teaching.instances import INSTANCE_UNAVAILABLE
from deeptutor.teaching.service import TeachingService

# The real turn runtime and courses of the AI-guard suite.
from tests.multi_user.test_teaching_ai_guard import (  # noqa: F401
    chat_runtime,
    courses,
    libraries,
)

_EXECUTOR_LOGGER = "deeptutor.services.session.turns.executor"


# ---------------------------------------------------------------------------
# B3-9: a refused teacher-path turn logs INFO, without a stack
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_refused_teacher_path_turn_logs_one_info_line_without_a_stack(
    courses, chat_runtime, as_account, teaching_users, caplog
):
    with as_account("tina"):
        TeachingService().revoke(courses.template_a, teaching_users["sara"]["id"])

    async def body(_context, out):
        out["ran"] = True

    caplog.set_level(logging.INFO, logger=_EXECUTOR_LOGGER)
    with as_account("sara"):
        seen = await chat_runtime(courses.a, body)

    # The turn contract is unchanged: error event, error code, failed status.
    assert "ran" not in seen
    errors = [e for e in seen["events"] if e["type"] == "error"]
    assert errors and errors[0]["content"] == INSTANCE_UNAVAILABLE
    assert errors[0]["metadata"]["error_code"] == "teacher_path_unavailable"
    assert errors[0]["metadata"]["retryable"] is False
    done = [e for e in seen["events"] if e["type"] == "done"]
    assert done[-1]["metadata"]["status"] == "failed"
    assert done[-1]["metadata"]["error_code"] == "teacher_path_unavailable"

    executor_records = [r for r in caplog.records if r.name == _EXECUTOR_LOGGER]
    assert not [r for r in executor_records if r.levelno >= logging.ERROR]
    refusals = [
        r
        for r in executor_records
        if r.levelno == logging.INFO and INSTANCE_UNAVAILABLE in r.getMessage()
    ]
    assert len(refusals) == 1
    assert refusals[0].exc_info is None


def test_a_mastery_refusal_is_marked_expected():
    assert turn_scope.MasteryPathUnavailable("closed").expected_refusal is True


# ---------------------------------------------------------------------------
# B3-9 / T4-M2 / T4-M3: change suggestions
# ---------------------------------------------------------------------------


def _point(kp_id: str, name: str) -> KnowledgePoint:
    return KnowledgePoint(id=kp_id, name=name, type=KnowledgeType.CONCEPT, module_id="m0")


def _diff() -> changes.OutlineDiff:
    limits = _point("kp_limits", "Limits")
    return changes.OutlineDiff(
        kept=[(limits, limits)],
        added=[_point("kp_rules", "Derivative rules")],
        removed=[_point("kp_derivatives", "Derivatives")],
        objective_changes={},
    )


@pytest.mark.asyncio
async def test_a_raising_change_model_warns_and_falls_back_to_the_defaults(
    monkeypatch, caplog
):
    async def _raise(_diff, _language):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(changes, "_ask_model", _raise)
    caplog.set_level(logging.DEBUG, logger=changes.logger.name)

    suggestions = await changes.suggest_changes(_diff())

    assert suggestions == changes.default_suggestions(_diff())
    warnings = [
        r for r in caplog.records if r.name == changes.logger.name and r.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert warnings[0].exc_info and warnings[0].exc_info[0] is RuntimeError


def test_change_reasons_are_cut_at_one_named_length():
    assert changes._MAX_REASON_LEN == 200
    long = "x" * (changes._MAX_REASON_LEN + 50)
    merged = changes._merge_answer(
        changes.default_suggestions(_diff()),
        _diff(),
        {
            "pairs": [
                {"new": "kp_rules", "old": "kp_derivatives", "kind": "reworded", "reason": long}
            ],
            "kept": [{"id": "kp_limits", "kind": "redefined", "reason": long}],
        },
    )
    assert {len(s.reason) for s in merged} == {changes._MAX_REASON_LEN}


# ---------------------------------------------------------------------------
# B2-15 leftovers: the turn-start admission and history across switches
# ---------------------------------------------------------------------------


class _Scope:
    def restrict(self, _allowed, *, template_id=""):
        return None

    def clear(self):
        return None


def _admission(path_id: str, template_id: str, allowed) -> _Admission:
    return _Admission(
        path_id, template_id, sorted(allowed or ()), None if allowed is None else frozenset(allowed)
    )


def _started_turn(start: _Admission, history: dict[str, str]) -> MasteryTurn:
    turn = MasteryTurn(_Scope(), store=None)
    turn.commit(start, start=True)
    turn.admit_history(history)
    turn.context = SimpleNamespace(
        knowledge_bases=[], metadata={"mastery_topic_source_index": dict(history)}
    )
    return turn


async def _switch_to(turn: MasteryTurn, target: _Admission, monkeypatch) -> dict[str, str]:
    async def _admit(_stream, _store, _path_id):
        return target

    monkeypatch.setattr(turn, "admit", _admit)
    monkeypatch.setattr(
        turn_scope, "_material_manifest", lambda path_id: ("", {f"src_{path_id}": "material"})
    )
    assert await turn.switch(target.path_id, None) is None
    return turn.context.metadata["mastery_topic_source_index"]


_HISTORY = {"hs-tutor-7": "earlier exchange"}


def test_commit_records_the_turn_start_admission_only_when_asked():
    start = _admission("assigned_a", "tpl_a", {"kb_calculus"})
    turn = _started_turn(start, _HISTORY)
    assert turn._history_survives(start)

    later = MasteryTurn(_Scope(), store=None)
    later.commit(start)
    # A plain commit (a switch) never claims to be the turn's start.
    assert later._history_survives(start) is False


@pytest.mark.asyncio
async def test_history_follows_a_switch_onto_another_path_of_the_same_template_with_a_superset(
    monkeypatch,
):
    turn = _started_turn(_admission("assigned_a", "tpl_a", {"kb_calculus"}), _HISTORY)

    index = await _switch_to(
        turn, _admission("assigned_a_2", "tpl_a", {"kb_calculus", "kb_algebra"}), monkeypatch
    )

    assert index == {"src_assigned_a_2": "material", **_HISTORY}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    [
        # Same template, a narrower allowlist (e.g. a revision dropped a library).
        _admission("assigned_a_2", "tpl_a", set()),
        # Another course with an allowlist that happens to be a superset.
        _admission("assigned_b", "tpl_b", {"kb_calculus", "kb_algebra"}),
    ],
)
async def test_history_does_not_follow_a_switch_the_start_scope_does_not_cover(
    target, monkeypatch
):
    turn = _started_turn(_admission("assigned_a", "tpl_a", {"kb_calculus"}), _HISTORY)

    index = await _switch_to(turn, target, monkeypatch)

    assert index == {f"src_{target.path_id}": "material"}


# ---------------------------------------------------------------------------
# T9-M4: a path switch never builds LearningStore on the event loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_path_switches_construct_the_learning_store_off_the_event_loop(
    teacher_draft, as_account, monkeypatch
):
    from deeptutor.capabilities.mastery import binding
    from deeptutor.learning.storage import LearningStore

    await asyncio.to_thread(teacher_draft, "sara", path_id="topic_mine")

    async def _forget(_session_id, _path_id):
        return None

    monkeypatch.setattr(binding, "_remember_on_session", _forget)
    built_on: list[int] = []
    real_init = LearningStore.__init__

    def _recording_init(self, *args, **kwargs):
        built_on.append(threading.get_ident())
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(LearningStore, "__init__", _recording_init)
    loop_thread = threading.get_ident()

    with as_account("sara"):
        assert (
            await binding.rebind_active_path(
                path_id="topic_mine", session_id="s1", turn_id="t1", bind_turn=None
            )
            == "topic_mine"
        )
        await binding.leave_active_path(session_id="s1", turn_id="t1", bind_turn=None)

    assert built_on
    assert loop_thread not in built_on


# ---------------------------------------------------------------------------
# B3-10: no route scoped by enter_mastery_template_scope runs background tasks
# ---------------------------------------------------------------------------


def _effective_routes(app: FastAPI):
    """``(path, methods, dependant)`` of every HTTP route, across FastAPI's
    flat and lazily-included router layouts."""
    for route in app.routes:
        contexts = getattr(route, "effective_route_contexts", None)
        if callable(contexts):
            for context in contexts():
                if getattr(context, "dependant", None) is not None:
                    yield context.path, set(context.methods or ()), context.dependant
        elif getattr(route, "dependant", None) is not None and getattr(route, "methods", None):
            yield route.path, set(route.methods), route.dependant


def _uses_background_tasks(dependant) -> bool:
    if dependant.background_tasks_param_name:
        return True
    return any(_uses_background_tasks(child) for child in dependant.dependencies)


def _scoped_routes(app: FastAPI):
    """The routes ``enter_mastery_template_scope`` may set context variables on."""
    from deeptutor.api.routers.mastery_teaching import (
        LEARNER_STATE_ROUTES,
        ORIGINAL_CONTENT_ROUTES,
    )

    for path, methods, dependant in _effective_routes(app):
        matched = {t for t in ORIGINAL_CONTENT_ROUTES if "GET" in methods and path.endswith(t)}
        matched |= {
            (m, t) for m, t in LEARNER_STATE_ROUTES if m in methods and path.endswith(t)
        }
        if matched:
            yield path, matched, dependant


def test_the_guard_sees_background_tasks_in_a_dependency_tree():
    app = FastAPI()

    def _deep(tasks: BackgroundTasks):
        return tasks

    @app.get("/api/books/{book_id}/spine")
    async def spine(book_id: str, _dep=Depends(_deep)):
        return book_id

    [(path, _matched, dependant)] = list(_scoped_routes(app))
    assert path == "/api/books/{book_id}/spine"
    assert _uses_background_tasks(dependant)


def test_routes_scoped_to_a_mastery_template_never_run_background_tasks():
    from deeptutor.api.main import app
    from deeptutor.api.routers.mastery_teaching import (
        LEARNER_STATE_ROUTES,
        ORIGINAL_CONTENT_ROUTES,
    )

    scoped = list(_scoped_routes(app))
    covered = set().union(*(matched for _path, matched, _dependant in scoped))
    # Every declared route is really mounted, so the guard is not vacuous.
    assert covered == set(ORIGINAL_CONTENT_ROUTES) | set(LEARNER_STATE_ROUTES)
    offenders = sorted(path for path, _matched, dep in scoped if _uses_background_tasks(dep))
    assert offenders == []
