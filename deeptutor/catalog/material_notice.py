"""Tell teachers when material their content cites goes away (platform batch 3 §6).

A book or knowledge base cited by another teacher's
published Mastery Path — or a book read by a task of their course — that an
administrator takes down, or its owner makes private or deletes, is no longer
there for their learners. Those authors hear of it, once a day per material
and item. The material's own owner is not told about their own content.
Taking it out of the market is not a loss: those already using it keep it.
"""

from __future__ import annotations

import logging
import time

from deeptutor.publishing import scope as scopes

logger = logging.getLogger(__name__)


def _citing(kind: str, item_id: str, material_owner: str) -> list[tuple[str, str, str, str]]:
    """(item kind, id, title, owner) of other people's published content citing it."""
    from deeptutor.teaching.store import TeachingStore

    teaching = TeachingStore()
    out: list[tuple[str, str, str, str]] = []
    for template in teaching.list_templates(owner_user_id=None):
        if template.owner_user_id == material_owner or template.status != "published" or not template.current_revision:
            continue
        revision = teaching.get_revision(template.id, template.current_revision)
        if revision is None:
            continue
        sources = revision.sources
        cites = (
            any(ref.kb_id == item_id for ref in sources.kb_refs) if kind == scopes.KNOWLEDGE_BASE
            else any(ref.book_id == item_id for ref in sources.book_refs)
        )
        if cites:
            out.append((scopes.MASTERY_PATH, template.id, template.title, template.owner_user_id))
    if kind == scopes.BOOK:
        from deeptutor.tasks.store import db as task_db

        with task_db.read() as conn:
            try:
                rows = conn.execute(
                    "SELECT DISTINCT c.id, c.title, c.owner_user_id FROM courses c "
                    "JOIN course_items i ON i.course_id = c.id JOIN tasks t ON t.id = i.task_id "
                    "WHERE t.kind = 'book' AND t.target_id = ? AND c.status = 'published'",
                    (item_id,),
                ).fetchall() if conn else []
            except Exception:
                rows = []
        out += [(scopes.COURSE, r["id"], r["title"], r["owner_user_id"]) for r in rows if r["owner_user_id"] != material_owner]
    return out


def notify_lost(kind: str, item_id: str, *, owner: str, name: str) -> None:
    """Best effort: a notice never makes the change that caused it fail."""
    try:
        if kind not in (scopes.BOOK, scopes.KNOWLEDGE_BASE):
            return
        from deeptutor.catalog.enrollment import members
        from deeptutor.notifications.service import notify

        day = time.strftime("%Y-%m-%d")
        for citing_kind, citing_id, title, author in _citing(kind, item_id, owner):
            link = f"/tasks?course={citing_id}" if citing_kind == scopes.COURSE else "/learning"
            notify(
                [author], "material_withdrawn",
                {"title": title, "material": name, "count": len(members(citing_kind, citing_id))},
                link=link, dedupe_key=f"material_withdrawn:{citing_kind}:{citing_id}:{kind}:{item_id}:{day}",
            )
    except Exception:
        logger.warning("Could not tell authors that %s %s went away", kind, item_id, exc_info=True)


__all__ = ["notify_lost"]
