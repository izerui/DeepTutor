"""A student's own copy of a teacher path.

Everything here runs in the *student's* request context: the copy lives in the
student's own ``mastery.sqlite3`` and is created the first time the student's
mastery list is read. The teacher side never writes a student's store.

Every check returns immediately for a path without an ``origin`` and, in that
case, never opens the system teaching database.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import logging
from typing import Any
import uuid

from deeptutor.learning.models import (
    LearningModule,
    LearningProgress,
    PathOrigin,
    TemplateUpdateNotice,
    TopicMetadata,
    TopicSource,
)
from deeptutor.learning.storage import LearningStore
from deeptutor.multi_user.context import get_current_user
from deeptutor.teaching.models import MasteryAssignment, MasteryTemplate, TemplateRevision
from deeptutor.teaching.store import TeachingStore

logger = logging.getLogger(__name__)

INSTANCE_UNAVAILABLE = (
    "This course is no longer available to you. Ask your teacher if you think "
    "this is a mistake."
)
#: Stable code of anything refused because the teacher path closed for this
#: student (revoked assignment, archived path): a live mastery subscription's
#: terminal message, the ``detail.code`` of a refused mastery-path REST route,
#: and the same value a refused mastery turn carries.
INSTANCE_UNAVAILABLE_CODE = "teacher_path_unavailable"
#: Hub reason that wakes live subscriptions to re-check their access.
INSTANCE_ACCESS_CHANGED = "instance.access_changed"


class InstanceUnavailableError(PermissionError):
    """The student's assignment was revoked or the teacher path archived."""


def instance_path_id(template_id: str) -> str:
    return f"assigned_{template_id}"


def _open(template: MasteryTemplate | None, assignment: MasteryAssignment | None) -> bool:
    return (
        template is not None
        and template.status == "published"
        and assignment is not None
        and assignment.status == "active"
    )


@dataclass(frozen=True)
class InstanceAccess:
    origin: PathOrigin | None
    template: MasteryTemplate | None
    assignment: MasteryAssignment | None

    @property
    def is_instance(self) -> bool:
        return self.origin is not None

    @property
    def allowed(self) -> bool:
        return self.origin is None or _open(self.template, self.assignment)


def instance_access(path_id: str, store: LearningStore) -> InstanceAccess:
    origin = store.load_origin(path_id)
    if origin is None:
        return InstanceAccess(None, None, None)
    teaching = TeachingStore()
    return InstanceAccess(
        origin,
        teaching.get_template(origin.template_id),
        teaching.get_assignment(origin.template_id, get_current_user().id),
    )


def ensure_instance_access(path_id: str, store: LearningStore) -> InstanceAccess:
    access = instance_access(path_id, store)
    if not access.allowed:
        raise InstanceUnavailableError(INSTANCE_UNAVAILABLE)
    return access


def notify_instance_closed(template_id: str) -> None:
    """Wake this process's live subscriptions to the template's copies.

    Called after the close is committed; each subscription re-checks its own
    reader's access and ends itself when it is gone. Best effort: the socket
    also re-checks on every event and periodically (other worker processes).
    """
    from deeptutor.learning.event_hub import mastery_topic_event_hub

    try:
        mastery_topic_event_hub.publish_to_path(
            instance_path_id(template_id), INSTANCE_ACCESS_CHANGED
        )
    except Exception:
        logger.exception("Could not notify live subscriptions of teacher path %s", template_id)


def instance_source_id(path_id: str, source_id: str) -> str:
    """The id a template source carries inside the copy *path_id*."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"deeptutor:{path_id}:{source_id}").hex


def instance_outline(
    path_id: str, revision: TemplateRevision
) -> tuple[list[TopicSource], list[LearningModule]]:
    """The revision's sources and modules as the copy *path_id* stores them.

    Source ids are primary keys of the student's whole store, and two
    templates published from one draft carry the same ones (an archived
    template's copy is retained). Every source id is therefore derived from
    the copy, and every reference to one (``KnowledgePoint.topic_source_ids``)
    is rewritten with the same mapping, so objectives keep citing their
    materials. Deterministic: rebuilding a copy from a later revision (the
    revision migration) must go through this function too.
    """
    sources: list[TopicSource] = []
    for source in revision.sources.sources:
        copied = source.model_copy(deep=True)
        copied.id = instance_source_id(path_id, source.id)
        sources.append(copied)
    modules = [module.model_copy(deep=True) for module in revision.outline.modules]
    for module in modules:
        for point in module.knowledge_points:
            point.topic_source_ids = [
                instance_source_id(path_id, source_id) for source_id in point.topic_source_ids
            ]
    return sources, modules


def _metadata(path_id: str, revision: TemplateRevision) -> TopicMetadata:
    outline = revision.outline
    return TopicMetadata(
        path_id=path_id,
        goal=outline.goal,
        description=outline.description,
        emoji=outline.emoji,
        map_seed=LearningStore._default_map_seed(path_id),
    )


def materialize_instance(
    store: LearningStore,
    path_id: str,
    template: MasteryTemplate,
    revision: TemplateRevision,
) -> LearningProgress:
    """Create the student's copy of *revision*: the outline, none of the history."""
    from deeptutor.learning.service import LearningService

    metadata = _metadata(path_id, revision)

    def create(tx) -> None:
        if tx.base_revision:
            # Every stored path has revision >= 1: a concurrent list request
            # (or the student's own path under this id) got here first.
            return
        sources, modules = instance_outline(path_id, revision)
        tx.progress.name = revision.outline.name[:200]
        tx.progress.origin = PathOrigin(template_id=template.id, revision=revision.revision)
        tx.put_topic(metadata, sources)
        LearningService(store).replace_modules(tx.progress, modules)
        tx.progress.current_module_id = modules[0].id if modules else ""
        tx.progress.current_kp_index = 0
        tx.touch()
        tx.emit(
            "topic.created",
            {
                "origin": "teacher_template",
                "template_id": template.id,
                "revision": revision.revision,
                "module_count": len(modules),
            },
        )

    progress, _ = store.mutate(path_id, create, create=True)
    return progress


def _report_squatted(template_id: str, user_id: str, path_id: str) -> None:
    # The student's own path already uses this id. It is never taken over,
    # so the assignment cannot be delivered until that path is renamed away.
    logger.warning(
        "Teacher path %s is assigned to %s but %s is the student's own path; "
        "the copy is not created",
        template_id,
        user_id,
        path_id,
    )


def sync_assigned_instances(store: LearningStore) -> list[str]:
    """Create the current student's missing copies; return the ids created.

    Only students, and only in their default workspace — the one place a
    teacher path lives in a student account. An existing copy is never
    rewritten and its recorded ``applied_revision`` never moved: only a
    revision migration may advance it.
    """
    from deeptutor.multi_user.roles import current_role
    from deeptutor.services.workspace.context import current_workspace_id

    if current_role() != "student" or current_workspace_id():
        return []
    user_id = get_current_user().id
    from deeptutor.publishing.scope import reconcile_student_quietly

    # Spec 2026-10-29 §7.1: paths published to a class or everyone reach later joiners.
    reconcile_student_quietly(user_id)
    teaching = TeachingStore()
    created: list[str] = []
    for assignment in teaching.assignments_for_student(user_id):
        if assignment.status != "active":
            continue
        template = teaching.get_template(assignment.template_id)
        if template is None or template.status != "published":
            continue
        path_id = assignment.instance_path_id or instance_path_id(template.id)
        try:
            if store.exists(path_id):
                if assignment.instance_path_id:
                    continue  # recorded: keep its applied revision as it is
                origin = store.load_origin(path_id)
                if origin is None or origin.template_id != template.id:
                    _report_squatted(template.id, user_id, path_id)
                    continue
                applied = origin.revision  # committed earlier, never recorded
            else:
                revision = teaching.get_revision(template.id, template.current_revision)
                if revision is None:
                    continue
                origin = materialize_instance(store, path_id, template, revision).origin
                if origin is None or origin.template_id != template.id:
                    _report_squatted(template.id, user_id, path_id)
                    continue
                applied = origin.revision
                created.append(path_id)
            teaching.record_instance(
                template.id, user_id, instance_path_id=path_id, applied_revision=applied
            )
        except Exception:
            # One broken copy must not take the student's whole list down.
            logger.exception("Could not create the copy of teacher path %s", template.id)
    return created


def _teacher_name(owner_user_id: str) -> str:
    from deeptutor.multi_user.identity import get_user_by_id

    found = get_user_by_id(owner_user_id)
    return found[0] if found else ""


def origin_payload(
    progress: LearningProgress,
    template: MasteryTemplate,
    *,
    materials_available: bool | None = None,
) -> dict[str, Any]:
    origin = progress.origin
    assert origin is not None
    return {
        "kind": origin.kind,
        "template_id": origin.template_id,
        "revision": origin.revision,
        "teacher_name": _teacher_name(template.owner_user_id),
        "update_notice": (
            origin.update_notice.model_dump(mode="json") if origin.update_notice else None
        ),
        # ``None`` means "not checked here": the list skips the per-KB lookups.
        "materials_available": materials_available,
    }


def _open_templates(template_ids: Iterable[str]) -> dict[str, MasteryTemplate]:
    """The templates the current student may still enter, by id."""
    teaching = TeachingStore()
    assignments = {
        assignment.template_id: assignment
        for assignment in teaching.assignments_for_student(get_current_user().id)
    }
    opened: dict[str, MasteryTemplate] = {}
    for template_id in dict.fromkeys(template_ids):
        template = teaching.get_template(template_id)
        if _open(template, assignments.get(template_id)):
            opened[template_id] = template  # type: ignore[assignment]
    return opened


def describe_instances(progresses: Iterable[LearningProgress]) -> dict[str, dict[str, Any]]:
    """Origin payloads for the instances the current student may still open."""
    instances = [progress for progress in progresses if progress.origin is not None]
    if not instances:
        return {}
    opened = _open_templates(progress.origin.template_id for progress in instances)  # type: ignore[union-attr]
    return {
        progress.book_id: origin_payload(progress, opened[progress.origin.template_id])  # type: ignore[union-attr]
        for progress in instances
        if progress.origin.template_id in opened  # type: ignore[union-attr]
    }


def closed_instance_ids(progresses: Iterable[LearningProgress]) -> set[str]:
    """Ids of revoked or archived copies, for lists that carry no origin payload."""
    instances = [progress for progress in progresses if progress.origin is not None]
    if not instances:
        return set()
    opened = _open_templates(progress.origin.template_id for progress in instances)  # type: ignore[union-attr]
    return {
        progress.book_id
        for progress in instances
        if progress.origin.template_id not in opened  # type: ignore[union-attr]
    }


OUTLINE_LOCKED = (
    "This outline is set by your teacher and cannot be changed here. You can "
    "still study, review or reset your own progress; send outline feedback to "
    "your teacher."
)


class OutlineLockedError(PermissionError):
    """The path's outline belongs to a teacher."""


def ensure_outline_mutable(path_id: str, store: LearningStore) -> None:
    """The one check every outline-changing entry point calls (spec §6)."""
    origin = store.load_origin(path_id)
    if origin is not None and origin.kind == "teacher_template":
        raise OutlineLockedError(OUTLINE_LOCKED)


def describe_instance_materials(
    path_id: str, store: LearningStore
) -> tuple[dict[str, Any] | None, frozenset[str]]:
    """The teacher path's origin payload and the ids of its unavailable sources.

    ``(None, frozenset())`` for a learner's own path. Storage-bound.
    """
    if store.load_origin(path_id) is None:
        return None, frozenset()
    progress = store.load(path_id)
    if progress is None or progress.origin is None:
        return None, frozenset()
    template = TeachingStore().get_template(progress.origin.template_id)
    if template is None:
        return None, frozenset()
    from deeptutor.teaching.access import studying_template

    with studying_template(template.id):
        missing = unavailable_source_ids(path_id, store)
    payload = origin_payload(progress, template, materials_available=not missing)
    return payload, frozenset(missing)


# ---- the path's materials (spec 7.4, R8) -------------------------------------

MATERIALS_UNAVAILABLE = (
    "The course materials for this path are temporarily unavailable. Ask your "
    "teacher to restore them; the tutor will not continue without them."
)
#: Stable code of a turn or switch refused because the materials are gone.
MATERIALS_UNAVAILABLE_CODE = "teacher_materials_unavailable"


def _book_id(source: TopicSource) -> str:
    value = str(source.source_id or "").strip()
    return value[len("book:") :] if value.startswith("book:") else value


def path_book_ids(topic: Any) -> list[str]:
    """The registered books a path cites, in source order."""
    from deeptutor.learning.models import TopicSourceKind

    ids = [
        _book_id(source)
        for source in getattr(topic, "sources", []) or []
        if source.kind == TopicSourceKind.BOOK and source.source_id
    ]
    return list(dict.fromkeys(book_id for book_id in ids if book_id))


def path_book_kb_refs(book_ids: list[str]) -> list[str]:
    """``kb:<id>`` refs of the KBs the path's books are bound to (R8).

    Read from the books' fixed bindings, so a binding whose reference an
    admin revoked still counts (and is reported unavailable) instead of
    silently disappearing. Storage-bound.
    """
    from deeptutor.teaching import books as book_access

    return list(
        dict.fromkeys(
            f"kb:{kb_id}" for book_id in book_ids for kb_id in book_access.book_kb_ids(book_id)
        )
    )


def _kb_source_available(source: TopicSource, user: Any) -> bool:
    from deeptutor.teaching import kb as kb_access

    record = kb_access.resolve(kb_access.source_kb_ref(source))
    if record is None or not kb_access.can_read(record, user):
        return False
    doc_id = kb_access.source_doc_id(source)
    # A cited document is followed by its fixed id; gone means unavailable.
    return not doc_id or kb_access.document_file(record, doc_id) is not None


def _book_source_available(source: TopicSource, user: Any) -> bool:
    from deeptutor.teaching import books as book_access

    book_id = _book_id(source)
    record = book_access.get(book_id) if book_id else None
    if record is None or not book_access.can_read(record, user):
        return False
    # Missing, unresolved, revoked or unshared bindings of the book (preflight
    # item 5): never tutor from a reduced set of its materials.
    return not book_access.unavailable_dependencies(book_id)


def unavailable_materials(path_id: str, store: LearningStore) -> list[str]:
    """Labels of the path's libraries and books the current user cannot use now.

    See :func:`unavailable_sources`. Storage-bound.
    """
    return list(dict.fromkeys(source.label for source in unavailable_sources(path_id, store)))


def unavailable_source_ids(path_id: str, store: LearningStore) -> list[str]:
    """Ids of the path's library and book sources the current user cannot use now.

    See :func:`unavailable_sources`. Storage-bound.
    """
    return [source.id for source in unavailable_sources(path_id, store)]


def unavailable_sources(path_id: str, store: LearningStore) -> list[TopicSource]:
    """The path's library and book sources the current user cannot use now.

    Reads the copy's own sources, i.e. the revision it is on (an instance
    waiting on an old-revision question keeps that revision's materials).
    Call inside ``studying_template(<template id>)``: reference-only materials
    are readable only there. A material whose check fails counts as
    unavailable. Storage-bound.
    """
    from deeptutor.learning.models import TopicSourceKind
    from deeptutor.teaching import kb as kb_access

    topic = store.get_topic(path_id)
    if topic is None:
        return []
    user = get_current_user()
    missing: list[TopicSource] = []
    for source in topic.sources:
        if source.kind == TopicSourceKind.BOOK:
            check = _book_source_available
        elif kb_access.source_kb_ref(source):
            check = _kb_source_available
        else:
            continue
        try:
            available = check(source, user)
        except Exception:
            logger.warning("Could not check material %r of %s", source.label, path_id, exc_info=True)
            available = False
        if not available:
            missing.append(source)
    return missing


# ---- teacher updates (spec 5.3) ---------------------------------------------


def _record_applied(teaching: TeachingStore, template_id: str, user_id: str, path_id: str, store) -> None:
    """Record the revision the copy is on now, then release what nobody needs.

    Read back from the copy rather than taken from the migration, so a slower
    concurrent caller cannot move the record backwards. The material resync
    runs after the teaching write committed and outside ``material_lock`` (it
    takes that lock itself); it only ever releases references. A failure
    leaves the earlier, larger set in place and is retried by the next resync.
    """
    from deeptutor.teaching.access import studying_template
    from deeptutor.teaching.service import sync_material_references

    origin = store.load_origin(path_id)
    if origin is None or origin.template_id != template_id:
        return
    teaching.record_instance(
        template_id, user_id, instance_path_id=path_id, applied_revision=origin.revision
    )
    try:
        # Plan D re-checks that the actor (this student) can read every book
        # the rewrite keeps; a reference-only book is readable to them only
        # while studying this path. It only ever releases references.
        with studying_template(template_id):
            sync_material_references(template_id, store=teaching)
    except Exception:
        logger.warning(
            "Could not release the material references of teacher path %s after %s "
            "moved to revision %s; the earlier references stay until the next resync",
            template_id,
            path_id,
            origin.revision,
            exc_info=True,
        )


def _lease_blocks(store: LearningStore, path_id: str, lease_turn_id: str) -> bool:
    lease = store.get_path_lease(path_id)
    return lease is not None and (not lease_turn_id or lease.turn_id != lease_turn_id)


def apply_pending_revision(
    path_id: str,
    store: LearningStore,
    *,
    respect_lease: bool,
    lease_turn_id: str = "",
) -> TemplateUpdateNotice | None:
    """Bring the student's copy to the teacher's current revision, if it may move now.

    Never while a question is open: the copy stays on its applied revision —
    and keeps that revision's materials — until the question is answered or
    skipped. With ``respect_lease`` it never moves under a live turn's lease,
    except the lease of ``lease_turn_id`` (the turn being admitted applies the
    update itself). Never for a revoked assignment or a closed template.
    Returns the (possibly accumulated) update notice when the copy moved.
    """
    from deeptutor.teaching.progress_migration import apply_revision

    origin = store.load_origin(path_id)
    if origin is None:
        return None
    teaching = TeachingStore()
    template = teaching.get_template(origin.template_id)
    if template is None or template.status != "published":
        return None
    user_id = get_current_user().id
    assignment = teaching.get_assignment(template.id, user_id)
    if assignment is None or assignment.status != "active":
        return None
    if assignment.applied_revision < origin.revision:
        # A migration committed but its record did not: repair it first, even
        # when the next move has to wait, so the retained materials follow the
        # revision the copy is actually on.
        _record_applied(teaching, template.id, user_id, path_id, store)
    if template.current_revision <= origin.revision:
        return None
    if respect_lease and _lease_blocks(store, path_id, lease_turn_id):
        return None
    revisions = teaching.revisions_between(
        template.id, after=origin.revision, upto=template.current_revision
    )
    if not revisions or revisions[-1].revision != template.current_revision:
        return None
    latest = revisions[-1]

    def migrate(tx) -> TemplateUpdateNotice | None:
        current = tx.progress.origin
        if current is None or current.template_id != template.id:
            return None
        if current.revision >= latest.revision:
            return None
        if tx.progress.pending_question is not None or tx.active_interaction() is not None:
            return None
        # Re-check the lease inside the write transaction so a lease acquired
        # between the outer check and BEGIN IMMEDIATE is honoured.
        if respect_lease:
            holder = tx.lease_turn_id()
            if holder is not None and (not lease_turn_id or holder != lease_turn_id):
                return None
        earlier = current.update_notice
        totals = {
            "added": earlier.added if earlier else 0,
            "removed": earlier.removed if earlier else 0,
            "redefined": earlier.redefined if earlier else 0,
        }
        from_revision = earlier.from_revision if earlier else current.revision
        for revision in revisions:
            if revision.revision <= current.revision:
                continue
            for key, value in apply_revision(tx.progress, revision).items():
                totals[key] += value
            current.revision = revision.revision
        sources, _modules = instance_outline(path_id, latest)
        tx.put_topic(_metadata(path_id, latest), sources)
        tx.progress.name = latest.outline.name[:200]
        applied = TemplateUpdateNotice(
            from_revision=from_revision, to_revision=current.revision, **totals
        )
        current.update_notice = applied
        tx.touch()
        tx.emit(
            "path.teacher_revision_applied",
            {"template_id": template.id, **applied.model_dump(mode="json")},
        )
        return applied

    _progress, notice = store.mutate(path_id, migrate)
    if notice is not None:
        _record_applied(teaching, template.id, user_id, path_id, store)
    return notice


def acknowledge_update(path_id: str, store: LearningStore) -> bool:
    """The student has seen "your teacher updated this course"; stop showing it."""
    if store.load_origin(path_id) is None:
        return False

    def clear(tx) -> bool:
        origin = tx.progress.origin
        if origin is None or origin.update_notice is None:
            return False
        origin.update_notice = None
        tx.touch()
        tx.emit("path.teacher_update_acknowledged", {"template_id": origin.template_id})
        return True

    _progress, cleared = store.mutate(path_id, clear)
    return cleared
