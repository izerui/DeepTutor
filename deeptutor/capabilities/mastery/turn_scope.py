"""Admission and materials scope of every turn on a mastery path.

A turn whose active path is a teacher's path is gated and narrowed the same
way whichever action runs it — the tutor loop, a chat / ask-questions /
deep-solve action in the mastery workspace, or a standalone pipeline. So the
gate does not live in any one pipeline: the turn runtime opens a
:class:`MasteryTurn` for every mastery-workspace turn before it loads the
path's materials, and the tutor pipeline reuses that turn (or opens one
itself for CLI / SDK callers that bypass the runtime).

One gate, :func:`teacher_instance_gate`, admits the turn and every path switch
the turn makes (``mastery_switch`` / ``mastery_leave`` reach it through
:func:`~deeptutor.capabilities.mastery.binding.turn_path_gate`). Every
admission step — assignment, revision, material availability — belongs there.

A path the learner owns never opens the teaching database: the gate returns at
``load_origin is None``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
import contextlib
from contextvars import ContextVar
from dataclasses import dataclass
import logging
from typing import Any

from deeptutor.capabilities.mastery.mode import OUTLINE, STUDY, normalize_mode

logger = logging.getLogger(__name__)

#: Stable code of a turn or switch refused because the course is closed.
TEACHER_PATH_UNAVAILABLE_CODE = "teacher_path_unavailable"


class MasteryPathUnavailable(RuntimeError):
    """A turn refused at admission; carries the turn runtime's failure contract."""

    retryable = False

    def __init__(self, message: str, *, error_code: str = TEACHER_PATH_UNAVAILABLE_CODE) -> None:
        super().__init__(message)
        self.error_code = error_code or TEACHER_PATH_UNAVAILABLE_CODE


async def teacher_instance_gate(stream: Any, store: Any, path_id: str) -> str | None:
    """Admit a turn (or a switch) onto ``path_id``, or close it.

    Returns ``""`` for a path the learner owns, the template id for an open
    teacher path, and ``None`` when the path may not be entered — an error
    has then already been sent to ``stream``. The single gate: later admission
    steps are added here, and apply to turn starts and switches alike.
    """
    if not path_id or store is None:
        return ""
    origin = await asyncio.to_thread(store.load_origin, path_id)
    if origin is None:
        return ""
    from deeptutor.teaching.instances import INSTANCE_UNAVAILABLE, instance_access

    access = await asyncio.to_thread(instance_access, path_id, store)
    if not access.allowed:
        await stream.error(
            INSTANCE_UNAVAILABLE,
            source="mastery_path",
            metadata={"code": TEACHER_PATH_UNAVAILABLE_CODE},
        )
        return None
    return origin.template_id


class RefusalRecorder:
    """Stands in for a stream where a refusal is reported by the caller.

    The turn runtime turns it into a failed turn; a path switch turns it into
    the switch tool's failure (the turn goes on, on the path it was on).
    """

    def __init__(self) -> None:
        self.message = ""
        self.code = ""

    async def error(
        self,
        message: str,
        source: str = "",
        stage: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        del source, stage
        if not self.message:
            self.message = str(message)
            self.code = str((metadata or {}).get("code") or "")

    def refusal(self):
        from deeptutor.capabilities.mastery.binding import PathRefusal

        return PathRefusal(self.message or "This path cannot be entered right now.", code=self.code)

    def unavailable(self) -> MasteryPathUnavailable:
        return MasteryPathUnavailable(
            self.message or "This path cannot be entered right now.", error_code=self.code
        )


def path_kb_refs(topic: Any) -> list[str]:
    """The knowledge bases a path's sources read from, in source order."""
    refs: list[str] = []
    for source in topic.sources if topic else []:
        if source.kind.value == "knowledge_base":
            refs.append(source.source_id)
        elif source.kind.value == "file" and source.metadata.get("kb_name"):
            refs.append(source.metadata["kb_name"])
    return refs


def _material_index(path_id: str) -> dict[str, str]:
    """The ``read_source`` index of a path's materials (storage-bound)."""
    try:
        from deeptutor.learning.storage import LearningStore
        from deeptutor.learning.topic_materials import build_topic_materials, render_topic_manifest

        topic = LearningStore().get_topic(path_id)
        if topic is None or not topic.sources:
            return {}
        return render_topic_manifest(build_topic_materials(topic.sources))[1]
    except Exception:
        logger.exception("Failed to build topic materials for mastery path %s", path_id)
        return {}


@dataclass(frozen=True)
class _Admission:
    path_id: str
    template_id: str
    refs: list[str]
    allowed: frozenset[str] | None


class MasteryTurn:
    """What one turn may read while on its current mastery path.

    Owns the turn's retrieval scope. Admission computes everything first and
    commits at the end, so a refused or failed admission changes nothing.
    """

    def __init__(self, scope: Any, store: Any) -> None:
        self.scope = scope
        self.store = store
        self.context: Any = None
        self.selected: list[str] = []
        #: The tutor attaches the path's own sources to a learner-owned path;
        #: other actions keep exactly what the learner selected.
        self.merge_path_sources = False
        self.path_id = ""
        self.template_id = ""
        self.refs: list[str] = []

    async def admit(self, stream: Any, store: Any, path_id: str) -> _Admission | None:
        # Module global on purpose: one gate, also for tests that stand it in.
        template_id = await teacher_instance_gate(stream, store, path_id)
        if template_id is None:
            return None
        topic = (
            await asyncio.to_thread(store.get_topic, path_id)
            if store is not None and path_id
            else None
        )
        refs = path_kb_refs(topic)
        allowed = None
        if template_id:
            from deeptutor.multi_user.retrieval_scope import canonical_kb_ids

            allowed = await asyncio.to_thread(canonical_kb_ids, refs)
        return _Admission(path_id, template_id, refs, allowed)

    def commit(self, admission: _Admission) -> None:
        self.path_id = admission.path_id
        self.template_id = admission.template_id
        self.refs = list(admission.refs)
        if admission.allowed is not None:
            # R8: a teacher path is taught from its own libraries only.
            self.scope.restrict(admission.allowed, template_id=admission.template_id)
        else:
            self.scope.clear()
        if self.context is not None:
            self._apply(self.context)

    def _apply(self, context: Any) -> None:
        if self.template_id:
            context.knowledge_bases = list(dict.fromkeys(self.refs))
            # Studied or reviewed, never redesigned.
            if normalize_mode(context.metadata.get("mastery_session_mode")) == OUTLINE:
                context.metadata["mastery_session_mode"] = STUDY
        elif self.merge_path_sources:
            context.knowledge_bases = list(dict.fromkeys([*self.selected, *self.refs]))
        else:
            context.knowledge_bases = list(self.selected)

    def attach(self, context: Any, *, merge_path_sources: bool = False) -> None:
        """Bind the turn's context; what it holds now is the learner's selection."""
        if self.context is not context:
            self.context = context
            self.selected = list(context.knowledge_bases)
        self.merge_path_sources = self.merge_path_sources or merge_path_sources
        self._apply(context)

    async def switch(self, path_id: str, store: Any):
        """The :data:`~deeptutor.capabilities.mastery.binding.PathGate` of this turn."""
        refusals = RefusalRecorder()
        admission = await self.admit(refusals, store, path_id)
        if admission is None:
            return refusals.refusal()
        index = None
        if self.context is not None and "mastery_topic_source_index" in self.context.metadata:
            index = await asyncio.to_thread(_material_index, path_id)
        self.commit(admission)
        if index is not None:
            # The tutor's read_source follows the path, not the turn's start.
            self.context.metadata["mastery_topic_source_index"] = index
        return None


_active_turn: ContextVar[MasteryTurn | None] = ContextVar("mastery_active_turn", default=None)


def current_mastery_turn() -> MasteryTurn | None:
    return _active_turn.get()


@contextlib.contextmanager
def _activate(turn: MasteryTurn) -> Iterator[None]:
    token = _active_turn.set(turn)
    try:
        yield
    finally:
        _active_turn.reset(token)


async def open_mastery_turn(
    stack: contextlib.ExitStack, *, path_id: str, stream: Any
) -> MasteryTurn | None:
    """Admit a turn onto ``path_id`` and open its scope on ``stack``.

    Returns ``None`` when the path may not be entered (the error went to
    ``stream``); the caller ends the turn. Everything opened here closes with
    ``stack``, i.e. with the turn.
    """
    from deeptutor.capabilities.mastery.binding import turn_path_gate
    from deeptutor.learning.storage import LearningStore
    from deeptutor.multi_user.retrieval_scope import turn_retrieval_scope

    # Only a turn on a path opens the store (cross-check 2.6).
    store = await asyncio.to_thread(LearningStore) if path_id else None
    turn = MasteryTurn(stack.enter_context(turn_retrieval_scope()), store)
    stack.enter_context(_activate(turn))
    admission = await turn.admit(stream, store, path_id)
    if admission is None:
        return None
    turn.commit(admission)
    stack.enter_context(turn_path_gate(turn.switch))
    return turn


__all__ = [
    "TEACHER_PATH_UNAVAILABLE_CODE",
    "MasteryPathUnavailable",
    "MasteryTurn",
    "RefusalRecorder",
    "current_mastery_turn",
    "open_mastery_turn",
    "path_kb_refs",
    "teacher_instance_gate",
]
