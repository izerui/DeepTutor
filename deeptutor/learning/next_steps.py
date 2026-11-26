"""The single ranking of "what should the learner do next".

Every surface that recommends a next move — the course page, Course Study —
reads this list instead of deciding on its own, so two screens can never give
the same learner contradicting advice. Steps are data (a ``kind`` plus its
evidence); each surface renders its own copy for a kind.

Order, most urgent first:

1. Reviews that are due — something is about to be forgotten.
2. Open mistakes — a known gap.
3. A mastery path already in progress — a commitment already made.
4. The next uncovered syllabus unit — the learner's own plan.
5. Reading that was opened — material already in hand.
6. Planning — material attached but nothing built on it yet.

A course with nothing attached asks for material before anything else.
"""

from __future__ import annotations

from typing import Any


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _rows(value: Any) -> list[dict[str, Any]]:
    return [item for item in (value or []) if isinstance(item, dict)]


def rank_next_steps(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Ordered next steps for one course state (see ``build_course_state``)."""
    steps: list[dict[str, Any]] = []
    if not _rows(state.get("resources")):
        steps.append({"kind": "attach_material"})

    paths = _rows((state.get("mastery") or {}).get("paths"))
    bank = state.get("question_bank") or {}

    reviews: list[dict[str, Any]] = []
    practice_due = _int(bank.get("due"))
    if practice_due:
        reviews.append({"kind": "practice_review", "count": practice_due})
    for path in paths:
        due = _int(path.get("due_reviews"))
        if due:
            reviews.append(
                {
                    "kind": "path_review",
                    "path_id": str(path.get("path_id") or ""),
                    "name": str(path.get("name") or path.get("path_id") or ""),
                    "count": due,
                }
            )
    # The bigger backlog first; ties keep practice ahead of paths.
    reviews.sort(key=lambda step: -step["count"])
    steps.extend(reviews)

    wrong = _int(bank.get("wrong"))
    if wrong:
        weakest = max(
            _rows(bank.get("weak_categories")),
            key=lambda item: _int(item.get("wrong")),
            default=None,
        )
        if weakest and _int(weakest.get("wrong")):
            steps.append(
                {
                    "kind": "open_mistakes",
                    "name": str(weakest.get("name") or ""),
                    "count": _int(weakest.get("wrong")),
                }
            )
        else:
            steps.append({"kind": "open_mistakes", "name": "", "count": wrong})

    stalled = next(
        (
            path
            for path in paths
            if _int(path.get("objectives_total"))
            and _int(path.get("objectives_mastered")) < _int(path.get("objectives_total"))
        ),
        None,
    )
    if stalled:
        weak_points = stalled.get("weak_points") or []
        steps.append(
            {
                "kind": "continue_path",
                "path_id": str(stalled.get("path_id") or ""),
                "name": str(stalled.get("name") or stalled.get("path_id") or ""),
                "done": _int(stalled.get("objectives_mastered")),
                "total": _int(stalled.get("objectives_total")),
                "weak_point": str(weak_points[0]) if weak_points else "",
            }
        )

    syllabus = state.get("syllabus") or {}
    unit = syllabus.get("next")
    if isinstance(unit, dict) and unit.get("title"):
        steps.append(
            {
                "kind": "syllabus_next",
                "unit": {
                    "id": str(unit.get("id") or ""),
                    "title": str(unit.get("title") or ""),
                    "position": _int(unit.get("position")),
                },
                "covered": _int(syllabus.get("covered")),
                "total": _int(syllabus.get("total")),
            }
        )

    workspace = next(
        (item for item in _rows((state.get("reading") or {}).get("workspaces")) if _int(item.get("materials"))),
        None,
    )
    if workspace:
        steps.append(
            {
                "kind": "continue_reading",
                "workspace_id": str(workspace.get("workspace_id") or ""),
                "title": str(workspace.get("title") or ""),
                "materials": _int(workspace.get("materials")),
            }
        )

    if steps and steps[0]["kind"] == "attach_material":
        return steps
    steps.append({"kind": "plan"})
    return steps


def _describe(step: dict[str, Any]) -> str:
    kind = step.get("kind")
    if kind == "attach_material":
        return "attach a textbook or knowledge base"
    if kind == "practice_review":
        return f"{step['count']} practice questions due for review"
    if kind == "path_review":
        return f"{step['count']} reviews due on path {step['name']} (id={step['path_id']})"
    if kind == "open_mistakes":
        where = f" in {step['name']}" if step.get("name") else ""
        return f"open mistakes{where} ({step['count']})"
    if kind == "continue_path":
        return f"continue path {step['name']} ({step['done']}/{step['total']} mastered, id={step['path_id']})"
    if kind == "syllabus_next":
        return f"syllabus unit {step['unit']['title']} ({step['covered']}/{step['total']} covered)"
    if kind == "continue_reading":
        return f"continue reading {step['title']} (id={step['workspace_id']})"
    return "plan this subject"


def describe_next_steps(steps: list[dict[str, Any]], *, limit: int = 3) -> str:
    """One line for a tutor: the same ranking, most urgent first."""
    if not steps:
        return "Recommended next: nothing ranked."
    shown = [f"{index}. {_describe(step)}" for index, step in enumerate(steps[:limit], start=1)]
    return "Next (as the course page ranks it): " + "; ".join(shown) + "."


__all__ = ["describe_next_steps", "rank_next_steps"]
