"""The teacher's home: a workbench (spec 2026-11-03 batch 3 §5.1).

What I sent out, with how far learners got; no "learners who need
attention" any more (spec 2026-11-05 remove-teacher-management §1.3). The
teacher's own learning is the student home's "mine" block, loaded by the
page itself.
Administrators get no workbench (they see no students' data): their home is
links plus their own learning.
"""

from __future__ import annotations

from typing import Any

from deeptutor.multi_user.context import get_current_user

SENT_LIMIT = 8


def _sent(actor) -> list[dict[str, Any]]:
    """Recent tasks, practice sets, paths and courses with completion (done / receivers)."""
    from deeptutor.practice_sets.service import PracticeSetService
    from deeptutor.publishing.scope import PRACTICE_SET, audience, get_scope
    from deeptutor.tasks.courses import CourseService
    from deeptutor.tasks.service import TaskService

    items: list[dict[str, Any]] = []
    for task in TaskService(actor).list_mine():
        if task["status"] == "published":
            items.append({"kind": "task", "id": task["id"], "title": task["title"], "done": task["completed"],
                          "total": task["total"], "updated_at": task["updated_at"]})
    for practice in PracticeSetService(actor).list_mine():
        if practice["status"] == "published":
            scope = get_scope(PRACTICE_SET, practice["id"])
            total = len(audience(scope, students_only=True, owner_id=actor.id)) if scope else 0
            items.append({"kind": "practice_set", "id": practice["id"], "title": practice["title"],
                          "done": practice["submitted_students"], "total": total,
                          "updated_at": practice["updated_at"]})
    for course in CourseService(actor).list_mine():
        if course["status"] == "published":
            items.append({"kind": "course", "id": course["id"], "title": course["title"], "done": None,
                          "total": course["task_count"], "updated_at": course["updated_at"]})
    try:
        from deeptutor.teaching.service import TeachingService

        for template in TeachingService().list_for_actor():
            if template.get("status") == "published":
                items.append({"kind": "mastery_path", "id": template["id"], "title": template["title"],
                              "done": None, "total": template.get("assigned_count", 0),
                              "updated_at": template.get("updated_at", 0)})
    except Exception:
        pass
    items.sort(key=lambda item: float(item["updated_at"] or 0), reverse=True)
    return items[:SENT_LIMIT]


def workbench(actor=None) -> dict[str, Any]:
    """A teacher's home: what they sent; anyone else gets only their role."""
    from deeptutor.multi_user.accounts import live_role as _role

    actor = actor or get_current_user()
    role = _role(actor)
    if role != "teacher":
        return {"role": role}
    return {"role": role, "sent": _sent(actor)}


__all__ = ["workbench"]
