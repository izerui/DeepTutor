"""Tell a teacher when material their path cites stops reaching its students
(spec 2026-11-03 batch 4 §2; roadmap 5.8, 6.3).

Someone else's book or knowledge base cited by a Mastery Path reaches the
path's students only through its own publish scope (citing passes nothing
on, §7.1). When its owner narrows that scope, withdraws it or makes it
private, students of other teachers' paths may lose it: those path authors
are told. The material's own owner is not told about their own paths, and
one path hears about one material at most once a day.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


def _readers(kind: str, item_id: str) -> tuple[set[str] | None, str, str]:
    """(students who can read it — ``None`` for everyone, owner id, name)."""
    from deeptutor.publishing import scope as scopes

    if kind == scopes.KNOWLEDGE_BASE:
        from deeptutor.knowledge import registry as kb_registry

        record = kb_registry.get_kb(item_id)
        name, owner, visibility = (record.name, record.created_by, record.visibility) if record else ("", "", "")
    else:
        from deeptutor.book import registry as book_registry

        record = book_registry.get_book(item_id)
        name, owner, visibility = (record.title, record.created_by, record.visibility) if record else ("", "", "")
    if record is None or visibility != "public":
        return set(), owner, name
    scope = scopes.get_scope(kind, item_id)
    if scope is None:
        return None, owner, name
    return scopes.audience(scope, students_only=True), owner, name


def _citing_paths(kind: str, item_id: str, owner: str) -> list[tuple[Any, set[str]]]:
    """Other teachers' published paths citing the item, each with its students."""
    from deeptutor.publishing import scope as scopes
    from deeptutor.teaching.store import TeachingStore

    teaching = TeachingStore()
    out = []
    for template in teaching.list_templates(owner_user_id=None):
        if template.owner_user_id == owner or template.status != "published" or not template.current_revision:
            continue
        revision = teaching.get_revision(template.id, template.current_revision)
        if revision is None:
            continue
        sources = revision.sources
        cites = (
            any(ref.kb_id == item_id for ref in sources.kb_refs) if kind == scopes.KNOWLEDGE_BASE
            else any(ref.book_id == item_id for ref in sources.book_refs)
        )
        if not cites:
            continue
        scope = scopes.get_scope(scopes.MASTERY_PATH, template.id)
        students = scopes.audience(scope, students_only=True, owner_id=template.owner_user_id) if scope else set()
        out.append((template, students))
    return out


def _missing(kind: str, item_id: str) -> tuple[dict[str, set[str]], dict[str, Any], str]:
    readers, owner, name = _readers(kind, item_id)
    paths = _citing_paths(kind, item_id, owner)
    missing = {
        template.id: set() if readers is None else students - readers
        for template, students in paths
    }
    return missing, {template.id: template for template, _ in paths}, name


@contextmanager
def watching(kind: str, item_id: str) -> Iterator[None]:
    """Around a change to a book's or knowledge base's reach: tell authors whose students lost it."""
    from deeptutor.publishing import scope as scopes

    if kind not in (scopes.KNOWLEDGE_BASE, scopes.BOOK) or scopes.classes_retired():
        # Once classes are retired, catalog.material_notice tells authors instead (platform batch 3 §6).
        yield
        return
    try:
        before, _, _ = _missing(kind, item_id)
    except Exception:
        logger.warning("Could not read who reaches %s %s", kind, item_id, exc_info=True)
        before = None
    yield
    if before is None:
        return
    try:
        after, templates, name = _missing(kind, item_id)
        from deeptutor.notifications.service import notify

        day = time.strftime("%Y-%m-%d")
        for template_id, lost in after.items():
            newly = lost - before.get(template_id, set())
            if not newly:
                continue
            template = templates[template_id]
            notify(
                [template.owner_user_id], "material_withdrawn",
                {"title": template.title, "material": name, "count": len(newly)},
                link="/learning", dedupe_key=f"material_withdrawn:{template_id}:{kind}:{item_id}:{day}",
            )
    except Exception:
        logger.warning("Could not tell path authors about %s %s", kind, item_id, exc_info=True)


__all__ = ["watching"]
