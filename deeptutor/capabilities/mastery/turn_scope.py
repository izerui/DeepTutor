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
    #: An expected outcome, not a fault: the turn runtime logs it as one INFO
    #: line without a stack (and needs no import of this package to tell).
    expected_refusal = True

    def __init__(
        self,
        message: str,
        *,
        error_code: str = TEACHER_PATH_UNAVAILABLE_CODE,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code or TEACHER_PATH_UNAVAILABLE_CODE
        #: Extra fields of the terminal error event (``materials``, ...).
        self.error_details = dict(details or {})


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
    from deeptutor.teaching.instances import (
        INSTANCE_UNAVAILABLE,
        MATERIALS_UNAVAILABLE,
        MATERIALS_UNAVAILABLE_CODE,
        apply_pending_revision,
        instance_access,
    )

    access = await asyncio.to_thread(instance_access, path_id, store)
    if not access.allowed:
        await stream.error(
            INSTANCE_UNAVAILABLE,
            source="mastery_path",
            metadata={"code": TEACHER_PATH_UNAVAILABLE_CODE},
        )
        return None
    # Revision admission: a newer teacher revision is applied before the turn
    # reads the path, unless a question is still open (the copy then stays on
    # its applied revision and that revision's materials) or another turn
    # holds the path's lease. The admitted turn's own lease does not block it.
    turn = current_mastery_turn()
    await asyncio.to_thread(
        apply_pending_revision,
        path_id,
        store,
        respect_lease=True,
        lease_turn_id=turn.turn_id if turn is not None else "",
    )
    # Material availability, for the revision the copy is on now: never teach
    # a teacher's course from the model's own knowledge (spec 7.4).
    missing = await asyncio.to_thread(_unavailable_materials, origin.template_id, path_id, store)
    if missing:
        await stream.error(
            MATERIALS_UNAVAILABLE,
            source="mastery_path",
            metadata={"code": MATERIALS_UNAVAILABLE_CODE, "materials": missing},
        )
        return None
    return origin.template_id


def _unavailable_materials(template_id: str, path_id: str, store: Any) -> list[str]:
    from deeptutor.teaching.access import studying_template
    from deeptutor.teaching.instances import unavailable_materials

    with studying_template(template_id):
        return unavailable_materials(path_id, store)


class RefusalRecorder:
    """Stands in for a stream where a refusal is reported by the caller.

    The turn runtime turns it into a failed turn; a path switch turns it into
    the switch tool's failure (the turn goes on, on the path it was on).
    """

    def __init__(self) -> None:
        self.message = ""
        self.code = ""
        self.details: dict[str, Any] = {}

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
            fields = dict(metadata or {})
            self.code = str(fields.pop("code", "") or "")
            self.details = fields

    def refusal(self):
        from deeptutor.capabilities.mastery.binding import PathRefusal

        return PathRefusal(
            self.message or "This path cannot be entered right now.",
            code=self.code,
            details=dict(self.details),
        )

    def unavailable(self) -> MasteryPathUnavailable:
        return MasteryPathUnavailable(
            self.message or "This path cannot be entered right now.",
            error_code=self.code,
            details=self.details,
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


def _material_manifest(path_id: str) -> tuple[str, dict[str, str]]:
    """A path's materials manifest and ``read_source`` index (storage-bound).

    Runs inside the turn's (already re-pointed) scope, so a reference-only
    book of the path being entered is readable here.
    """
    try:
        from deeptutor.learning.storage import LearningStore
        from deeptutor.learning.topic_materials import build_topic_materials, render_topic_manifest

        topic = LearningStore().get_topic(path_id)
        if topic is None or not topic.sources:
            return "", {}
        return render_topic_manifest(build_topic_materials(topic.sources))
    except Exception:
        logger.exception("Failed to build topic materials for mastery path %s", path_id)
        return "", {}


def _book_bindings(template_id: str, book_ids: list[str]) -> tuple:
    """Plan D reading bindings of a teacher path's books, as its student."""
    from deeptutor.book.registry import reading_bindings
    from deeptutor.teaching.access import studying_template

    with studying_template(template_id):
        return reading_bindings(*book_ids)


@dataclass(frozen=True)
class _Admission:
    path_id: str
    template_id: str
    refs: list[str]
    allowed: frozenset[str] | None
    #: Plan D reading bindings of the path's books (teacher paths only).
    books: tuple = ()


class MasteryTurn:
    """What one turn may read while on its current mastery path.

    Owns the turn's retrieval scope (R8 allowlist and the template being
    studied, which opens reference-only libraries and books) and its book
    reading scope (which opens the KBs of the path's books). Both are shared
    mutable objects, so a path switch re-points them for the whole turn and
    the previous path's materials close. Admission computes everything first
    and commits at the end, so a refused or failed admission changes nothing.
    """

    def __init__(self, scope: Any, store: Any, *, turn_id: str = "", books: Any = None) -> None:
        self.scope = scope
        self.books = books
        self.store = store
        #: The turn's id, the one its path lease (if any) is held under.
        self.turn_id = turn_id
        self.context: Any = None
        self.selected: list[str] = []
        #: The tutor attaches the path's own sources to a learner-owned path;
        #: other actions keep exactly what the learner selected.
        self.merge_path_sources = False
        self.path_id = ""
        self.template_id = ""
        self.refs: list[str] = []
        #: The topic-materials manifest the runtime put at the head of the
        #: turn's ``source_manifest`` (``None``: this turn carries none).
        self.manifest: str | None = None
        #: The conversation's earlier exchanges (``read_source`` archives)
        #: merged into the turn's index at its start, and the admission they
        #: were merged under.
        self.history_sources: dict[str, str] = {}
        self._history_admission: _Admission | None = None

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
        books: tuple = ()
        if template_id:
            from deeptutor.multi_user.retrieval_scope import canonical_kb_ids
            from deeptutor.teaching.instances import path_book_ids, path_book_kb_refs

            # R8: a teacher path is taught from its own materials only — the
            # KBs it cites and the KBs its books are bound to.
            book_ids = path_book_ids(topic)
            refs = list(
                dict.fromkeys([*refs, *await asyncio.to_thread(path_book_kb_refs, book_ids)])
            )
            allowed = await asyncio.to_thread(canonical_kb_ids, refs)
            books = await asyncio.to_thread(_book_bindings, template_id, book_ids)
        return _Admission(path_id, template_id, refs, allowed, books)

    def commit(self, admission: _Admission, *, start: bool = False) -> None:
        """Point the turn at ``admission``; ``start`` marks the turn's own
        admission, the scope its history sources are admitted under."""
        if start:
            self._history_admission = admission
        self.path_id = admission.path_id
        self.template_id = admission.template_id
        self.refs = list(admission.refs)
        if admission.allowed is not None:
            # R8: a teacher path is taught from its own libraries only; the
            # scope also names the template the turn studies now.
            self.scope.restrict(admission.allowed, template_id=admission.template_id)
        else:
            self.scope.clear()
        if self.books is not None:
            self.books.bind(admission.books)
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

    def admit_history(self, sources: dict[str, str]) -> None:
        """Record the history sources merged at turn start; they were admitted
        under the admission committed with ``start=True``."""
        self.history_sources = dict(sources or {})

    def _history_survives(self, admission: _Admission) -> bool:
        """Whether the turn-start history may follow a switch onto ``admission``.

        Scope only narrows (R8): an archive carries no record of which
        libraries it quoted, so it may enter a teacher path's allowlist only
        when it was admitted under that same course's scope or a narrower one.
        A learner-owned path has no allowlist and keeps it.
        """
        start = self._history_admission
        if start is None:
            return False
        if admission.allowed is None:
            return True
        if start.allowed is None:
            return False
        return admission.template_id == start.template_id and start.allowed <= admission.allowed

    def _replace_manifest(self, manifest: str) -> None:
        current = str(getattr(self.context, "source_manifest", "") or "")
        old = self.manifest or ""
        if old and old in current:
            current = current.replace(old, manifest, 1)
        elif not old:
            current = f"{manifest}{current}"
        else:
            logger.warning("Mastery turn manifest changed shape; replacing it whole")
            current = manifest
        self.context.source_manifest = current
        self.manifest = manifest

    async def switch(self, path_id: str, store: Any):
        """The :data:`~deeptutor.capabilities.mastery.binding.PathGate` of this turn."""
        refusals = RefusalRecorder()
        admission = await self.admit(refusals, store, path_id)
        if admission is None:
            return refusals.refusal()
        self.commit(admission)
        if self.context is None:
            return None
        has_index = "mastery_topic_source_index" in self.context.metadata
        if has_index or self.manifest is not None:
            # The materials follow the path, not the turn's start: loaded in
            # the re-pointed scope, so the previous path's never linger.
            manifest, index = await asyncio.to_thread(_material_manifest, path_id)
            if has_index:
                # The conversation's own history follows only where the new
                # path's scope allows it (``_history_survives``).
                if self.history_sources and self._history_survives(admission):
                    index = {**index, **self.history_sources}
                self.context.metadata["mastery_topic_source_index"] = index
            if self.manifest is not None:
                self._replace_manifest(manifest)
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
    stack: contextlib.ExitStack, *, path_id: str, stream: Any, turn_id: str = ""
) -> MasteryTurn | None:
    """Admit a turn onto ``path_id`` and open its scope on ``stack``.

    Returns ``None`` when the path may not be entered (the error went to
    ``stream``); the caller ends the turn. Everything opened here closes with
    ``stack``, i.e. with the turn. ``turn_id`` names the turn's path lease:
    admission may update the path under that lease, never under another.
    """
    from deeptutor.capabilities.mastery.binding import turn_path_gate
    from deeptutor.learning.storage import LearningStore
    from deeptutor.multi_user.retrieval_scope import turn_retrieval_scope

    # Only a turn on a path opens the store (cross-check 2.6).
    from deeptutor.book.registry import turn_reading_scope

    store = await asyncio.to_thread(LearningStore) if path_id else None
    turn = MasteryTurn(
        stack.enter_context(turn_retrieval_scope()),
        store,
        turn_id=turn_id,
        books=stack.enter_context(turn_reading_scope()),
    )
    stack.enter_context(_activate(turn))
    admission = await turn.admit(stream, store, path_id)
    if admission is None:
        return None
    turn.commit(admission, start=True)
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
