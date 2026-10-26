"""Change detection between two revisions of a teacher's outline."""

from __future__ import annotations

import asyncio

import pytest

from deeptutor.learning.models import KnowledgePoint, KnowledgeType, LearningModule
from deeptutor.teaching import changes
from deeptutor.teaching.changes import (
    ChangeDecisionError,
    build_change_log,
    default_suggestions,
    diff_outlines,
    first_revision_log,
    suggest_changes,
)
from deeptutor.teaching.models import KpDecision


def _kp(kp_id: str, name: str) -> KnowledgePoint:
    return KnowledgePoint(id=kp_id, name=name, type=KnowledgeType.CONCEPT, module_id="m0")


def _module(*points: KnowledgePoint, objective: str = "Differentiate") -> LearningModule:
    return LearningModule(
        id="m0", name="Calculus", order=0, objective=objective, knowledge_points=list(points)
    )


OLD = [_module(_kp("a", "Limits"), _kp("b", "Derivatives"), _kp("c", "Chain rule"))]
NEW = [_module(_kp("a", "Limits"), _kp("b2", "Derivative rules"), _kp("d", "Integrals"))]


def test_diff_is_keyed_by_stable_id_not_by_name():
    diff = diff_outlines(OLD, NEW)

    assert [(old.id, new.id) for old, new in diff.kept] == [("a", "a")]
    assert [kp.id for kp in diff.added] == ["b2", "d"]
    assert [kp.id for kp in diff.removed] == ["b", "c"]


def test_defaults_keep_existing_points_and_add_new_ones():
    suggestions = {s.kp_id: s for s in default_suggestions(diff_outlines(OLD, NEW))}

    assert suggestions["a"].suggested == "unchanged"
    assert suggestions["b2"].suggested == "added"
    assert suggestions["d"].suggested == "added"


def test_model_pairs_are_validated_before_they_become_suggestions(monkeypatch):
    async def fake_model(_diff, _language):
        return {
            "pairs": [
                {"new": "b2", "old": "b", "kind": "reworded", "reason": "same skill"},
                {"new": "d", "old": "b", "kind": "redefined"},  # "b" already used
                {"new": "a", "old": "c", "kind": "reworded"},  # "a" is not new
            ],
            "kept": [{"id": "a", "kind": "redefined"}, {"id": "zz", "kind": "redefined"}],
        }

    monkeypatch.setattr(changes, "_ask_model", fake_model)

    result = {s.kp_id: s for s in asyncio.run(suggest_changes(diff_outlines(OLD, NEW)))}

    assert (result["b2"].suggested, result["b2"].from_id, result["b2"].from_name) == (
        "reworded",
        "b",
        "Derivatives",
    )
    assert result["d"].suggested == "added"
    assert result["a"].suggested == "redefined"


def test_model_failure_falls_back_to_defaults(monkeypatch):
    async def broken(_diff, _language):
        return None

    monkeypatch.setattr(changes, "_ask_model", broken)

    result = asyncio.run(suggest_changes(diff_outlines(OLD, NEW)))

    assert [s.suggested for s in result] == ["unchanged", "added", "added"]


def test_unchanged_outline_never_asks_the_model(monkeypatch):
    async def must_not_run(_diff, _language):
        raise AssertionError("no change to judge")

    monkeypatch.setattr(changes, "_ask_model", must_not_run)

    assert [s.suggested for s in asyncio.run(suggest_changes(diff_outlines(OLD, OLD)))] == [
        "unchanged"
    ] * 3


def test_build_change_log_follows_the_teachers_decisions():
    log, aliases = build_change_log(
        diff_outlines(OLD, NEW),
        {
            "a": KpDecision(kind="redefined"),
            "b2": KpDecision(kind="reworded", from_id="b"),
        },
    )

    assert {key: entry.kind for key, entry in log.items()} == {
        "a": "redefined",
        "b": "reworded",
        "d": "added",
        "c": "removed",
    }
    assert log["b"].previous_name == "Derivatives"
    assert aliases == {"b2": "b"}


def test_a_removed_point_can_be_continued_only_once():
    with pytest.raises(ChangeDecisionError):
        build_change_log(
            diff_outlines(OLD, NEW),
            {
                "b2": KpDecision(kind="reworded", from_id="b"),
                "d": KpDecision(kind="redefined", from_id="b"),
            },
        )


def test_kept_points_cannot_be_marked_added_and_unknown_ids_are_rejected():
    with pytest.raises(ChangeDecisionError):
        build_change_log(diff_outlines(OLD, NEW), {"a": KpDecision(kind="added")})
    with pytest.raises(ChangeDecisionError):
        build_change_log(diff_outlines(OLD, NEW), {"ghost": KpDecision(kind="unchanged")})


def test_first_revision_marks_everything_added():
    assert {k: v.kind for k, v in first_revision_log(OLD).items()} == {
        "a": "added",
        "b": "added",
        "c": "added",
    }
