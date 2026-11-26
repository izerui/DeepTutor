"""One ranking of "what to do next", shared by every surface (course page,
Course Study). Reviews about to be forgotten come first, then open gaps,
then work already started, then the learner's own plan, then new material."""

from deeptutor.learning.next_steps import rank_next_steps


def _state(**overrides):
    state = {
        "resources": [{"kind": "knowledge_base", "available": True}],
        "mastery": {"paths": []},
        "question_bank": {"total": 0, "wrong": 0, "due": 0, "weak_categories": []},
        "syllabus": {"total": 0, "covered": 0, "next": None, "units": []},
        "reading": {"workspaces": []},
    }
    state.update(overrides)
    return state


def _kinds(state):
    return [step["kind"] for step in rank_next_steps(state)]


def test_full_ranking_puts_due_reviews_before_gaps_before_progress():
    state = _state(
        mastery={
            "paths": [
                {"path_id": "p1", "name": "Calc", "objectives_total": 4, "objectives_mastered": 1, "due_reviews": 2},
                {"path_id": "p2", "name": "Alg", "objectives_total": 3, "objectives_mastered": 3, "due_reviews": 0},
            ]
        },
        question_bank={"total": 9, "wrong": 3, "due": 5, "weak_categories": [{"name": "Limits", "wrong": 3}]},
        syllabus={"total": 4, "covered": 1, "next": {"id": "u2", "title": "Series", "position": 1}, "units": []},
        reading={"workspaces": [{"workspace_id": "w", "title": "Notes", "materials": 2}]},
    )
    steps = rank_next_steps(state)
    assert [s["kind"] for s in steps] == [
        "practice_review",
        "path_review",
        "open_mistakes",
        "continue_path",
        "syllabus_next",
        "continue_reading",
        "plan",
    ]
    assert steps[0]["count"] == 5
    assert steps[1] == {"kind": "path_review", "path_id": "p1", "name": "Calc", "count": 2}
    assert steps[2]["name"] == "Limits" and steps[2]["count"] == 3
    assert steps[3]["path_id"] == "p1"
    assert steps[4]["unit"]["title"] == "Series"


def test_bigger_review_backlog_goes_first():
    state = _state(
        mastery={"paths": [{"path_id": "p1", "name": "A", "objectives_total": 1, "objectives_mastered": 1, "due_reviews": 9}]},
        question_bank={"total": 3, "wrong": 0, "due": 2, "weak_categories": []},
    )
    assert _kinds(state)[:2] == ["path_review", "practice_review"]


def test_nothing_attached_asks_for_material_first():
    assert _kinds(_state(resources=[]))[0] == "attach_material"


def test_quiet_course_ends_with_a_plan():
    assert _kinds(_state()) == ["plan"]


def test_open_mistakes_without_categories_still_count():
    state = _state(question_bank={"total": 2, "wrong": 2, "due": 0, "weak_categories": []})
    steps = rank_next_steps(state)
    assert steps[0] == {"kind": "open_mistakes", "name": "", "count": 2}


def test_course_study_is_told_the_same_ranking_the_page_shows():
    from deeptutor.capabilities.course_study.capability import summarize_course_state
    from deeptutor.capabilities.course_study.tools import _render_course_overview

    state = _state(
        question_bank={"total": 9, "wrong": 3, "due": 5, "weak_categories": [{"name": "Limits", "wrong": 3}]},
        syllabus={"total": 4, "covered": 1, "next": {"id": "u2", "title": "Series", "position": 1}, "units": []},
    )
    state["course"] = {"id": "c1", "name": "Calculus"}
    state["next_steps"] = rank_next_steps(state)
    expected = (
        "Next (as the course page ranks it): "
        "1. 5 practice questions due for review; "
        "2. open mistakes in Limits (3); "
        "3. syllabus unit Series (1/4 covered)."
    )
    assert expected in summarize_course_state(state)
    assert expected in _render_course_overview(state)
