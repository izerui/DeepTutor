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

from deeptutor.learning.models import LearningProgress, PathOrigin, TopicMetadata, TopicSource
from deeptutor.learning.storage import LearningStore
from deeptutor.multi_user.context import get_current_user
from deeptutor.teaching.models import MasteryAssignment, MasteryTemplate, TemplateRevision
from deeptutor.teaching.store import TeachingStore

logger = logging.getLogger(__name__)

INSTANCE_UNAVAILABLE = (
    "This course is no longer available to you. Ask your teacher if you think "
    "this is a mistake."
)


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


def instance_sources(path_id: str, revision: TemplateRevision) -> list[TopicSource]:
    """The revision's sources with ids unique to this copy.

    Source ids are primary keys of the student's whole store, and two
    templates published from one draft carry the same ones; deriving them from
    the copy keeps every copy insertable, and stable across rewrites.
    """
    sources: list[TopicSource] = []
    for source in revision.sources.sources:
        copied = source.model_copy(deep=True)
        copied.id = uuid.uuid5(uuid.NAMESPACE_URL, f"deeptutor:{path_id}:{source.id}").hex
        sources.append(copied)
    return sources


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
        modules = [module.model_copy(deep=True) for module in revision.outline.modules]
        tx.progress.name = revision.outline.name[:200]
        tx.progress.origin = PathOrigin(template_id=template.id, revision=revision.revision)
        tx.put_topic(metadata, instance_sources(path_id, revision))
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
                    continue  # the student's own path happens to use this id
                applied = origin.revision  # committed earlier, never recorded
            else:
                revision = teaching.get_revision(template.id, template.current_revision)
                if revision is None:
                    continue
                origin = materialize_instance(store, path_id, template, revision).origin
                if origin is None or origin.template_id != template.id:
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


def describe_instance(path_id: str, store: LearningStore) -> dict[str, Any] | None:
    if store.load_origin(path_id) is None:
        return None
    progress = store.load(path_id)
    if progress is None or progress.origin is None:
        return None
    template = TeachingStore().get_template(progress.origin.template_id)
    if template is None:
        return None
    return origin_payload(progress, template)
