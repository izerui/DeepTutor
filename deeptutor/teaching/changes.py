"""What changed between two published revisions of a teacher's outline.

Identity is the stable ``kp_id``, never the name. A knowledge point the
teacher renamed gets a new draft id (the outline editors mint one whenever a
point's name or type changes — see ``assign_objective_identities``), so it
shows up here as *added* while its predecessor shows up as *removed*. The
model may suggest that the two are one objective reworded or redefined; only
the teacher's confirmation turns that into an alias and lets the student's
progress follow it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
from typing import Any, Literal

from pydantic import BaseModel

from deeptutor.learning.models import KnowledgePoint, LearningModule
from deeptutor.teaching.models import ChangeEntry, KpDecision

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 20.0

_SYSTEM_PROMPT = (
    "You compare two versions of a course outline written by a teacher. "
    "Some knowledge points disappeared and some appeared; decide which new point "
    "continues which removed one. 'reworded' means the same learning objective in "
    "new words; 'redefined' means the learner must now be able to do something "
    "materially different. Only pair points that teach the same thing, and leave "
    "out anything you are unsure about. For kept points whose module objective "
    "changed, say whether the point itself is 'unchanged' or 'redefined'. Answer "
    'with JSON only: {"pairs": [{"new": "<id>", "old": "<id>", "kind": '
    '"reworded|redefined", "reason": "<short>"}], "kept": [{"id": "<id>", '
    '"kind": "unchanged|redefined", "reason": "<short>"}]}. Write reasons in the '
    "requested language."
)


@dataclass(frozen=True)
class OutlineDiff:
    kept: list[tuple[KnowledgePoint, KnowledgePoint]]
    added: list[KnowledgePoint]
    removed: list[KnowledgePoint]
    #: Kept kp id -> (old module objective, new module objective), when different.
    objective_changes: dict[str, tuple[str, str]]


class KpSuggestion(BaseModel):
    kp_id: str
    name: str
    status: Literal["kept", "new"]
    suggested: Literal["unchanged", "reworded", "redefined", "added"]
    from_id: str = ""
    from_name: str = ""
    reason: str = ""


class ChangeDecisionError(ValueError):
    """The teacher's per-point decisions do not describe a valid update."""


def _points(modules: list[LearningModule]) -> dict[str, KnowledgePoint]:
    return {point.id: point for module in modules for point in module.knowledge_points}


def _objectives(modules: list[LearningModule]) -> dict[str, str]:
    return {
        point.id: (module.objective or module.name)
        for module in modules
        for point in module.knowledge_points
    }


def diff_outlines(
    old_modules: list[LearningModule], new_modules: list[LearningModule]
) -> OutlineDiff:
    old, new = _points(old_modules), _points(new_modules)
    old_objectives, new_objectives = _objectives(old_modules), _objectives(new_modules)
    kept = [(old[kp_id], point) for kp_id, point in new.items() if kp_id in old]
    return OutlineDiff(
        kept=kept,
        added=[point for kp_id, point in new.items() if kp_id not in old],
        removed=[point for kp_id, point in old.items() if kp_id not in new],
        objective_changes={
            new_point.id: (old_objectives[new_point.id], new_objectives[new_point.id])
            for _old_point, new_point in kept
            if old_objectives[new_point.id] != new_objectives[new_point.id]
        },
    )


def default_suggestions(diff: OutlineDiff) -> list[KpSuggestion]:
    return [
        KpSuggestion(kp_id=new.id, name=new.name, status="kept", suggested="unchanged")
        for _old, new in diff.kept
    ] + [
        KpSuggestion(kp_id=point.id, name=point.name, status="new", suggested="added")
        for point in diff.added
    ]


async def _ask_model(diff: OutlineDiff, language: str) -> dict[str, Any] | None:
    payload = {
        "reply_language": language,
        "removed": [{"id": p.id, "name": p.name, "type": p.type.value} for p in diff.removed],
        "added": [{"id": p.id, "name": p.name, "type": p.type.value} for p in diff.added],
        "kept_with_changed_module_objective": [
            {"id": kp_id, "old_objective": old, "new_objective": new}
            for kp_id, (old, new) in diff.objective_changes.items()
        ],
    }
    try:
        from deeptutor.services.llm import complete
        from deeptutor.utils.json_parser import parse_json_response

        raw = await asyncio.wait_for(
            complete(
                prompt=json.dumps(payload, ensure_ascii=False),
                system_prompt=_SYSTEM_PROMPT,
                temperature=0.0,
                max_tokens=800,
                max_retries=0,
            ),
            timeout=_TIMEOUT_SECONDS,
        )
        data = parse_json_response(str(raw or ""), fallback=None)
    except Exception:
        logger.debug("teacher change suggestion failed", exc_info=True)
        return None
    return data if isinstance(data, dict) else None


def _merge_answer(
    base: list[KpSuggestion], diff: OutlineDiff, answer: dict[str, Any]
) -> list[KpSuggestion]:
    by_id = {suggestion.kp_id: suggestion for suggestion in base}
    removed = {point.id: point for point in diff.removed}
    used: set[str] = set()
    for pair in answer.get("pairs") or []:
        if not isinstance(pair, dict):
            continue
        new_id, old_id = str(pair.get("new") or ""), str(pair.get("old") or "")
        kind = str(pair.get("kind") or "")
        suggestion = by_id.get(new_id)
        if (
            suggestion is None
            or suggestion.status != "new"
            or suggestion.from_id
            or old_id not in removed
            or old_id in used
            or kind not in ("reworded", "redefined")
        ):
            continue
        used.add(old_id)
        suggestion.suggested = kind  # type: ignore[assignment]
        suggestion.from_id = old_id
        suggestion.from_name = removed[old_id].name
        suggestion.reason = str(pair.get("reason") or "")[:200]
    kept_ids = {new.id for _old, new in diff.kept}
    for item in answer.get("kept") or []:
        if not isinstance(item, dict):
            continue
        kp_id, kind = str(item.get("id") or ""), str(item.get("kind") or "")
        if kp_id in kept_ids and kind in ("unchanged", "redefined"):
            by_id[kp_id].suggested = kind  # type: ignore[assignment]
            by_id[kp_id].reason = str(item.get("reason") or "")[:200]
    return base


async def suggest_changes(diff: OutlineDiff, *, language: str = "en") -> list[KpSuggestion]:
    """Default verdicts, refined by the model when there is something to judge."""
    base = default_suggestions(diff)
    if not diff.added and not diff.objective_changes:
        return base
    answer = await _ask_model(diff, language)
    if not isinstance(answer, dict):
        return base
    return _merge_answer(base, diff, answer)


def build_change_log(
    diff: OutlineDiff, decisions: dict[str, KpDecision]
) -> tuple[dict[str, ChangeEntry], dict[str, str]]:
    """Turn the teacher's confirmed verdicts into a change log and new aliases."""
    kept_ids = {new.id for _old, new in diff.kept}
    added_ids = {point.id for point in diff.added}
    unknown = sorted(set(decisions) - kept_ids - added_ids)
    if unknown:
        raise ChangeDecisionError(
            f"Unknown knowledge point(s) {', '.join(unknown)}; refresh the preview."
        )
    removed = {point.id: point for point in diff.removed}
    log: dict[str, ChangeEntry] = {}
    aliases: dict[str, str] = {}
    used: set[str] = set()
    for _old, new in diff.kept:
        decision = decisions.get(new.id)
        kind = decision.kind if decision is not None else "unchanged"
        if kind not in ("unchanged", "reworded", "redefined"):
            raise ChangeDecisionError(
                f"{new.name!r} already exists; mark it unchanged, reworded or redefined."
            )
        log[new.id] = ChangeEntry(kind=kind)
    for point in diff.added:
        decision = decisions.get(point.id)
        if decision is None or decision.kind == "added":
            log[point.id] = ChangeEntry(kind="added")
            continue
        if (
            decision.kind not in ("reworded", "redefined")
            or decision.from_id not in removed
            or decision.from_id in used
        ):
            raise ChangeDecisionError(
                f"{point.name!r} must continue exactly one removed knowledge point."
            )
        used.add(decision.from_id)
        aliases[point.id] = decision.from_id
        log[decision.from_id] = ChangeEntry(
            kind=decision.kind, previous_name=removed[decision.from_id].name
        )
    for kp_id, point in removed.items():
        if kp_id not in used:
            log[kp_id] = ChangeEntry(kind="removed", previous_name=point.name)
    return log, aliases


def first_revision_log(modules: list[LearningModule]) -> dict[str, ChangeEntry]:
    return {kp_id: ChangeEntry(kind="added") for kp_id in _points(modules)}
