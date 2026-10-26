"""Apply one published teacher revision to a student's copy (spec §5.3).

Identity is the stable ``kp_id``: a reworded point keeps its id (the template's
alias table saw to that), so everything the student earned on it stays put.
The copy's outline is rebuilt through :func:`~deeptutor.teaching.instances.instance_outline`,
so its sources keep the ids derived for this copy, exactly as when it was
created.
"""

from __future__ import annotations

import time

from deeptutor.learning.models import LearningProgress
from deeptutor.learning.policy import REDEFINED_VOID_REASON
from deeptutor.teaching.models import TemplateRevision


def _current_point(progress: LearningProgress) -> str:
    for module in progress.modules:
        if module.id != progress.current_module_id:
            continue
        if 0 <= progress.current_kp_index < len(module.knowledge_points):
            return module.knowledge_points[progress.current_kp_index].id
    return ""


def _forget_schedule(progress: LearningProgress, kp_id: str) -> None:
    """What drives routing and review for a point, but is not its history."""
    progress.repetition_states.pop(kp_id, None)
    progress.review_queue = [t for t in progress.review_queue if t.knowledge_point_id != kp_id]
    progress.learner_mastery_overrides.pop(kp_id, None)
    progress.deferred_objectives.pop(kp_id, None)
    progress.explained_objectives.pop(kp_id, None)
    progress.feynman_retries.pop(kp_id, None)
    progress.feynman_explanations.pop(kp_id, None)


def _reset_objective(progress: LearningProgress, kp_id: str, moment: float) -> None:
    """A redefined point goes back to "needs re-assessment"; nothing else moves."""
    for attempt in progress.quiz_attempts:
        if attempt.knowledge_point_id == kp_id and not attempt.voided:
            attempt.voided = True
            attempt.void_reason = REDEFINED_VOID_REASON
    progress.mastery_levels.pop(kp_id, None)
    progress.qualitative_mastery.pop(kp_id, None)
    progress.lesson_evidence.pop(kp_id, None)
    _forget_schedule(progress, kp_id)
    # R11: recorded from the change itself, whatever the point's history was.
    progress.reassessment_required[kp_id] = moment


def apply_revision(progress: LearningProgress, revision: TemplateRevision) -> dict[str, int]:
    """Move *progress* (a student's copy) onto *revision*; return what changed."""
    from deeptutor.teaching.instances import instance_outline

    old_ids = {p.id for m in progress.modules for p in m.knowledge_points}
    _sources, modules = instance_outline(progress.book_id, revision)
    new_ids = {p.id for m in modules for p in m.knowledge_points}
    current = _current_point(progress)
    redefined = {
        kp_id
        for kp_id, entry in revision.change_log.items()
        if entry.kind == "redefined" and kp_id in new_ids and kp_id in old_ids
    }
    removed = old_ids - new_ids
    moment = time.time()
    for kp_id in redefined:
        _reset_objective(progress, kp_id, moment)
    for kp_id in removed:
        # Answers, evidence, error records and mastery levels stay as history.
        _forget_schedule(progress, kp_id)
        progress.reassessment_required.pop(kp_id, None)
    progress.modules = modules
    for module in modules:
        for point in module.knowledge_points:
            progress.knowledge_types[point.id] = point.type
    locations = {
        point.id: (module.id, index)
        for module in modules
        for index, point in enumerate(module.knowledge_points)
    }
    if current in locations:
        progress.current_module_id, progress.current_kp_index = locations[current]
    else:
        progress.current_module_id = modules[0].id if modules else ""
        progress.current_kp_index = 0
    progress.stage_failure_counts = {}
    progress.stage_failure_notes = {}
    return {
        "added": len(new_ids - old_ids),
        "removed": len(removed),
        "redefined": len(redefined),
    }


__all__ = ["apply_revision"]
