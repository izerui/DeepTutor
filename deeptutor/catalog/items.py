"""The five kinds of content, seen from the market (platform batch 1 §3, §5).

For each kind: who owns it, its name and description, whether it can be
listed, whether it was closed by its author (archived, made private), and
what its introduction page previews. Everything here reads; nothing writes.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
import logging
from typing import Any

from deeptutor.publishing import scope as scopes

logger = logging.getLogger(__name__)

KINDS = (scopes.BOOK, scopes.KNOWLEDGE_BASE, scopes.MASTERY_PATH, scopes.PRACTICE_SET, scopes.COURSE)


@dataclass
class ItemInfo:
    kind: str
    item_id: str
    owner: str
    title: str
    description: str
    #: Archived, made private or deleted by its author: nobody can study it now.
    closed: bool
    #: Why it cannot be put forward now; empty when it can.
    not_ready: str = ""


def owner(kind: str, item_id: str) -> str | None:
    """Who owns it, from the registries only (no manifest or document reads); ``None`` if it is gone.

    Raises when a store cannot be read: callers that must not act on a guess
    (the migrations) let it stop them.
    """
    if kind == scopes.BOOK:
        from deeptutor.book import registry as book_registry

        record = book_registry.get_book(item_id)
        return record.created_by if record else None
    if kind == scopes.KNOWLEDGE_BASE:
        from deeptutor.knowledge import registry as kb_registry

        record = kb_registry.get_kb(item_id)
        return record.created_by if record else None
    if kind == scopes.MASTERY_PATH:
        from deeptutor.teaching.store import TeachingStore

        template = TeachingStore().get_template(item_id)
        return template.owner_user_id if template else None
    table = {scopes.PRACTICE_SET: ("practice_sets", "practice"), scopes.COURSE: ("courses", "tasks"),
             scopes.TASK: ("tasks", "tasks")}.get(kind)
    if table is None:
        return None
    if table[1] == "practice":
        from deeptutor.practice_sets import store as source
    else:
        from deeptutor.tasks.store import db as source
    with source.read() as conn:
        row = conn.execute(
            f"SELECT owner_user_id FROM {table[0]} WHERE id = ?", (item_id,)  # nosec B608
        ).fetchone() if conn else None
    return row["owner_user_id"] if row else None


def info(kind: str, item_id: str) -> ItemInfo | None:
    reader = _READERS.get(kind)
    if reader is None:
        return None
    try:
        return reader(item_id)
    except Exception:
        logger.exception("Could not read %s %s for the market", kind, item_id)
        return None


# ── per kind ───────────────────────────────────────────────


def _book(item_id: str) -> ItemInfo | None:
    from deeptutor.book import registry as book_registry

    record = book_registry.get_book(item_id)
    if record is None:
        return None
    description = ""
    chapters = _book_chapters(record)
    try:
        from deeptutor.multi_user.book_access import storage_for

        book = storage_for(record).load_book(item_id)
        description = book.description if book else ""
    except Exception:
        logger.debug("No manifest for book %s", item_id, exc_info=True)
    # Content first: "make it public" is then the only thing left, safe to do in one step.
    not_ready = ""
    if not any(chapter["pages"] for chapter in chapters):
        not_ready = "The book has no written chapter yet."
    elif record.visibility != "public":
        not_ready = "Make the book public first."
    return ItemInfo(
        scopes.BOOK, item_id, record.created_by, record.title, description,
        closed=record.visibility != "public", not_ready=not_ready,
    )


def _book_chapters(record) -> list[dict[str, Any]]:
    try:
        from deeptutor.multi_user.book_access import storage_for

        spine = storage_for(record).load_spine(record.book_id)
    except Exception:
        return []
    if spine is None:
        return []
    return [
        {"id": chapter.id, "title": chapter.title, "pages": len(chapter.page_ids)}
        for chapter in sorted(spine.chapters, key=lambda c: c.order)
    ]


def _knowledge_base(item_id: str) -> ItemInfo | None:
    from deeptutor.knowledge import registry as kb_registry

    record = kb_registry.get_kb(item_id)
    if record is None:
        return None
    not_ready = ""
    if kb_registry.is_connector(record):
        not_ready = "Connected knowledge bases cannot be listed."
    elif not kb_registry.list_documents(item_id):
        not_ready = "The knowledge base has no document yet."
    elif record.visibility != "public":
        not_ready = "Make the knowledge base public first."
    return ItemInfo(
        scopes.KNOWLEDGE_BASE, item_id, record.created_by, record.name, "",
        closed=record.visibility != "public", not_ready=not_ready,
    )


def _mastery_path(item_id: str) -> ItemInfo | None:
    from deeptutor.teaching.store import TeachingStore

    template = TeachingStore().get_template(item_id)
    if template is None:
        return None
    not_ready = ""
    if template.status == "archived":
        not_ready = "The path is archived."
    elif template.current_revision < 1:
        not_ready = "Publish the path first."
    return ItemInfo(
        scopes.MASTERY_PATH, item_id, template.owner_user_id, template.title, template.description,
        closed=template.status == "archived", not_ready=not_ready,
    )


def _practice_set(item_id: str) -> ItemInfo | None:
    from deeptutor.practice_sets import store as practice_store

    with practice_store.read() as conn:
        row = conn.execute("SELECT * FROM practice_sets WHERE id = ?", (item_id,)).fetchone() if conn else None
    if row is None:
        return None
    not_ready = ""
    if row["status"] == "archived":
        not_ready = "The practice set is archived."
    elif row["status"] != "published" or row["current_revision"] < 1:
        not_ready = "Publish the practice set first."
    elif row["due_at"] is not None:
        # Direction §11.4: the market is self-paced; deadlines belong to course tasks.
        not_ready = "Remove the deadline first: practice in the market has none. Set deadlines on course tasks."
    elif row["reveal"] == "after_due":
        # Without a deadline "after the deadline" never comes: learners would never see answers.
        not_ready = "Show answers right after submitting: practice in the market has no deadline."
    return ItemInfo(
        scopes.PRACTICE_SET, item_id, row["owner_user_id"], row["title"], row["description"],
        closed=row["status"] == "archived", not_ready=not_ready,
    )


def _course(item_id: str) -> ItemInfo | None:
    from deeptutor.tasks.store import db as task_db

    with task_db.read() as conn:
        row = conn.execute("SELECT * FROM courses WHERE id = ?", (item_id,)).fetchone() if conn else None
        count = conn.execute(
            "SELECT COUNT(*) FROM course_items WHERE course_id = ?", (item_id,)
        ).fetchone()[0] if row is not None else 0
    if row is None:
        return None
    not_ready = ""
    if row["status"] == "archived":
        not_ready = "The course is archived."
    elif row["status"] != "published":
        not_ready = "Publish the course first."
    elif not count:
        not_ready = "The course has no item yet."
    else:
        not_ready = _course_foreign_books(item_id, row["owner_user_id"])
    return ItemInfo(
        scopes.COURSE, item_id, row["owner_user_id"], row["title"], row["description"],
        closed=row["status"] == "archived", not_ready=not_ready,
    )


def _course_foreign_books(course_id: str, owner: str) -> str:
    """Platform batch 1 §4.3: a course cites listed content or its author's own."""
    from deeptutor.catalog import listings

    for task in course_tasks(course_id):
        if task["kind"] != "book" or not task["target_id"]:
            continue
        book = info(scopes.BOOK, task["target_id"])
        if book is None or book.owner == owner:
            continue
        if listings.status(scopes.BOOK, task["target_id"]) != "listed":
            return "The course reads a book of someone else that is not in the market."
    return ""


def course_tasks(course_id: str) -> list[dict[str, Any]]:
    """The course's tasks in order: id, kind, title, target."""
    from deeptutor.tasks.store import db as task_db

    with task_db.read() as conn:
        if conn is None:
            return []
        return [
            {"id": r["id"], "kind": r["kind"], "title": r["title"], "target_id": r["target_id"] or ""}
            for r in conn.execute(
                "SELECT t.id, t.kind, t.title, t.target_id FROM course_items i JOIN tasks t ON t.id = i.task_id "
                "WHERE i.course_id = ? ORDER BY i.position",
                (course_id,),
            )
        ]


_READERS = {
    scopes.BOOK: _book,
    scopes.KNOWLEDGE_BASE: _knowledge_base,
    scopes.MASTERY_PATH: _mastery_path,
    scopes.PRACTICE_SET: _practice_set,
    scopes.COURSE: _course,
}


# ── previews ───────────────────────────────────────────────


def preview(kind: str, item_id: str, *, full: bool = False) -> dict[str, Any]:
    """What the introduction page shows; *full* adds what a reviewer reads (§5.1)."""
    try:
        if kind == scopes.BOOK:
            from deeptutor.book import registry as book_registry

            record = book_registry.get_book(item_id)
            return {"chapters": _book_chapters(record) if record else []}
        if kind == scopes.KNOWLEDGE_BASE:
            from deeptutor.knowledge import registry as kb_registry

            return {"documents": [d.rel_path for d in kb_registry.list_documents(item_id)][:200]}
        if kind == scopes.MASTERY_PATH:
            return _path_preview(item_id, full=full)
        if kind == scopes.PRACTICE_SET:
            return _set_preview(item_id, full=full)
        if kind == scopes.COURSE:
            return {"items": [{"kind": t["kind"], "title": t["title"]} for t in course_tasks(item_id)]}
    except Exception:
        logger.exception("Could not preview %s %s", kind, item_id)
    return {}


def _path_preview(item_id: str, *, full: bool) -> dict[str, Any]:
    from deeptutor.teaching.store import TeachingStore

    teaching = TeachingStore()
    template = teaching.get_template(item_id)
    if template is None or template.current_revision < 1:
        return {"modules": []}
    revision = teaching.get_revision(item_id, template.current_revision)
    if revision is None:
        return {"modules": []}
    out: dict[str, Any] = {
        "revision": revision.revision,
        "modules": [
            {"name": module.name, "points": [point.name for point in module.knowledge_points]}
            for module in revision.outline.modules
        ],
    }
    if full:
        out["goal"] = revision.outline.goal
    return out


def _set_preview(item_id: str, *, full: bool) -> dict[str, Any]:
    from deeptutor.practice_sets import store as practice_store

    with practice_store.read() as conn:
        row = conn.execute(
            "SELECT current_revision FROM practice_sets WHERE id = ?", (item_id,)
        ).fetchone() if conn else None
        revision = conn.execute(
            "SELECT questions_json FROM practice_set_revisions WHERE set_id = ? AND revision = ?",
            (item_id, row["current_revision"]),
        ).fetchone() if row is not None else None
    questions = json.loads(revision["questions_json"]) if revision is not None else []
    out: dict[str, Any] = {
        "question_count": len(questions),
        "types": dict(Counter(str(q.get("question_type") or "") for q in questions)),
        "total_points": sum(float(q.get("points") or 0) for q in questions),
    }
    if full:
        out["questions"] = [
            {
                "question": q.get("question", ""), "question_type": q.get("question_type", ""),
                "options": q.get("options") or {}, "correct_answer": q.get("correct_answer", ""),
                "explanation": q.get("explanation", ""), "points": q.get("points", 0),
            }
            for q in questions
        ]
    return out


__all__ = ["KINDS", "ItemInfo", "course_tasks", "info", "preview"]
