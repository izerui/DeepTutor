"""The teacher's home: a workbench (spec 2026-11-03 batch 3 §5.1).

From top to bottom: homework to grade, students who need attention, my
classes, what I sent out and how far students got. The teacher's own
learning is the student home's "mine" block, loaded by the page itself.
Administrators get no workbench (they see no students' data): their home is
links plus their own learning.
"""

from __future__ import annotations

from typing import Any

from deeptutor.multi_user.context import get_current_user

ATTENTION_LIMIT = 20
SENT_LIMIT = 8


def to_grade(teacher_id: str) -> list[dict[str, Any]]:
    """Every hand-in waiting for this teacher, oldest first."""
    from deeptutor.multi_user.accounts import accounts as _accounts
    from deeptutor.tasks.store import db

    with db.read() as conn:
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT s.task_id, s.student_id, s.submitted_at, s.late, s.attempt, t.title "
            "FROM task_submissions s JOIN tasks t ON t.id = s.task_id "
            "WHERE t.owner_user_id = ? AND s.status = 'submitted' ORDER BY s.submitted_at",
            (teacher_id,),
        ).fetchall()
    accounts = _accounts()
    return [
        {
            "task_id": r["task_id"], "task_title": r["title"], "student_id": r["student_id"],
            "username": (accounts.get(r["student_id"]) or {}).get("username", ""),
            "submitted_at": r["submitted_at"], "late": bool(r["late"]), "attempt": r["attempt"],
        }
        for r in rows
    ]



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
    """A teacher's home: what to grade, learners who need attention (with the course or path to open), what they sent."""
    from deeptutor.catalog import attention, items
    from deeptutor.catalog.learners import where_learners_are
    from deeptutor.multi_user.accounts import accounts as _accounts, live_role as _role

    actor = actor or get_current_user()
    role = _role(actor)
    if role != "teacher":
        return {"role": role}
    where = where_learners_are(actor.id)
    flags = attention.for_teacher(actor.id)
    accounts = _accounts()
    needing = []
    for s, f in flags.items():
        if not f:
            continue
        kind, item_id = where.get(s, ("", ""))
        info = items.info(kind, item_id) if kind else None
        needing.append({
            "student_id": s, "username": (accounts.get(s) or {}).get("username", ""),
            "item": {"kind": kind, "id": item_id, "title": info.title if info else ""} if kind else None,
            "flags": f,
        })
    needing.sort(key=lambda row: (-len(row["flags"]), row["username"]))
    return {
        "role": role, "to_grade": to_grade(actor.id), "attention": needing[:ATTENTION_LIMIT],
        "attention_total": len(needing), "sent": _sent(actor),
    }


__all__ = ["to_grade", "workbench"]
