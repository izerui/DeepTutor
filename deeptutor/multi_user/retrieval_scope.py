"""Which knowledge bases a turn may retrieve from (R8).

A tutoring turn on a teacher's path is taught from that path's own materials.
Narrowing the KBs attached to the turn is not enough for that: the model can
still name any readable library in an explicit ``rag(kb_name=...)`` call, and
visual inspection resolves its library separately. So the restriction lives
here, where every retrieval resolves its library
(:func:`deeptutor.multi_user.knowledge_access.resolve_for_rag`), and is
compared by registry identity rather than by the spelling of a reference.

The scope is turn-owned and *mutable*: a turn may move to another path
mid-way (``mastery_switch``), and the tool that does so can run in a task or a
worker thread with a copied context, where setting a context variable would
not reach the rest of the turn. Every task of the turn therefore shares one
:class:`TurnRetrievalScope` object, and a path switch re-points that object.
Leaving the ``with`` block restores whatever was active before, so nothing
outlives the turn or crosses into another account's request.

The restriction only ever narrows: it is checked *after* the ordinary access
rules (workspace selection, public/private, plan D's book reading scope), so
an allowlisted library the learner cannot otherwise read stays unreadable.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)

#: What a refused retrieval reports. Stable English; the model relays it.
TEACHER_PATH_RETRIEVAL_DENIED = (
    "This course is taught from its teacher's materials only, and that "
    "knowledge base is not one of them. Retrieve from the course's own "
    "knowledge bases instead."
)
#: Machine-readable code carried on a refused tool result.
TEACHER_PATH_SCOPE_CODE = "teacher_path_scope"


@dataclass
class TurnRetrievalScope:
    """One turn's retrieval allowlist. ``kb_ids is None`` means unrestricted."""

    kb_ids: frozenset[str] | None = None
    #: The teacher template the turn is currently on, ``""`` otherwise.
    template_id: str = ""
    #: The scope that was active when this one opened. A nested turn can never
    #: widen what its enclosing turn was allowed to read.
    parent: TurnRetrievalScope | None = None

    def restrict(self, kb_ids: Iterable[str], *, template_id: str) -> None:
        self.kb_ids = frozenset(kb_ids)
        self.template_id = template_id

    def clear(self) -> None:
        self.kb_ids = None
        self.template_id = ""

    @property
    def restricted(self) -> bool:
        scope: TurnRetrievalScope | None = self
        while scope is not None:
            if scope.kb_ids is not None:
                return True
            scope = scope.parent
        return False

    def allows(self, kb_id: str) -> bool:
        scope: TurnRetrievalScope | None = self
        while scope is not None:
            if scope.kb_ids is not None and kb_id not in scope.kb_ids:
                return False
            scope = scope.parent
        return True


_active_scope: ContextVar[TurnRetrievalScope | None] = ContextVar(
    "turn_retrieval_scope", default=None
)


def current_retrieval_scope() -> TurnRetrievalScope | None:
    return _active_scope.get()


@contextmanager
def turn_retrieval_scope() -> Iterator[TurnRetrievalScope]:
    """Open an (initially unrestricted) scope for the rest of this turn."""
    scope = TurnRetrievalScope(parent=_active_scope.get())
    token = _active_scope.set(scope)
    try:
        yield scope
    finally:
        _active_scope.reset(token)


def canonical_kb_ids(refs: Iterable[str]) -> frozenset[str]:
    """The registry identities behind ``refs``; unresolvable refs are dropped.

    Dropping fails closed: a reference that names no registered library adds
    nothing to an allowlist. Storage-bound; async callers use a worker thread.
    """
    from deeptutor.knowledge import registry

    ids: set[str] = set()
    for ref in refs:
        try:
            record = registry.resolve_ref(str(ref or ""))
        except Exception:
            logger.warning("Path material %r has no registry identity", ref, exc_info=True)
            continue
        if record is not None:
            ids.add(record.kb_id)
    return frozenset(ids)


def _resource_kb_id(base_dir: Path | str, name: str) -> str:
    from deeptutor.knowledge import registry

    record = registry.find_by_location(base_dir, name)
    return record.kb_id if record is not None else ""


def location_allowed(base_dir: Path | str, name: str) -> bool:
    """Whether the library stored at ``base_dir/name`` may be retrieved from."""
    scope = _active_scope.get()
    if scope is None or not scope.restricted:
        return True
    kb_id = _resource_kb_id(base_dir, name)
    return bool(kb_id) and scope.allows(kb_id)


def enforce_retrieval_scope(resource: Any) -> None:
    """Refuse an already access-checked resource outside the turn's allowlist."""
    if not location_allowed(resource.base_dir, resource.name):
        raise HTTPException(status_code=403, detail=TEACHER_PATH_RETRIEVAL_DENIED)


def retrieval_refusal(kb_ref: str) -> str:
    """Why ``kb_ref`` may not be retrieved from in this turn, or ``""``.

    ``""`` also when the reference is inaccessible for an ordinary reason: the
    caller's normal resolution reports that in its own words. Storage-bound.
    """
    scope = _active_scope.get()
    if scope is None or not scope.restricted:
        return ""
    from deeptutor.multi_user.knowledge_access import resolve_kb

    try:
        resource = resolve_kb(str(kb_ref or ""))
    except HTTPException:
        return ""
    if location_allowed(resource.base_dir, resource.name):
        return ""
    return TEACHER_PATH_RETRIEVAL_DENIED


__all__ = [
    "TEACHER_PATH_RETRIEVAL_DENIED",
    "TEACHER_PATH_SCOPE_CODE",
    "TurnRetrievalScope",
    "canonical_kb_ids",
    "current_retrieval_scope",
    "enforce_retrieval_scope",
    "location_allowed",
    "retrieval_refusal",
    "turn_retrieval_scope",
]
