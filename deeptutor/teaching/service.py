"""Teacher-side operations on mastery templates.

Teachers manage their own templates; admins manage everyone's assignments,
archives and deletes but never publish another teacher's template (D17): its
draft lives in that teacher's workspace, so the owner is the only one who can
read it. Preview and publication also refuse to plan sources under any
identity but the owner's own request, so a bare book id is never lazily
registered under someone else's account.

Publishing validates everything first, then runs its side effects in a fixed
order. A failure before the revision is written aborts the publish and names
the step; a failure after it is logged and returned as a warning.

Consent: only the choices a publish request states explicitly are recorded as
durable visibility intent. Defaults (including an earlier revision's choice)
and previews record nothing.

Material protection follows applied revisions (preflight item 6): while an
active student's instance is still on an older revision (for example with a
pending old-revision question), the references that revision needs stay in
place. :func:`sync_material_references` is the hook that releases them once
that instance's migration commits (Task 10); revoke and archive release
immediately.
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field
from functools import partial
import logging
import sqlite3
from typing import Any, Callable

from deeptutor.learning.models import LearningProgress, MasteryTopic
from deeptutor.learning.storage import LearningStore
from deeptutor.multi_user.context import get_current_user
from deeptutor.teaching import books as book_access
from deeptutor.teaching import kb as kb_access
from deeptutor.teaching.changes import (
    ChangeDecisionError,
    build_change_log,
    diff_outlines,
    first_revision_log,
    suggest_changes,
)
from deeptutor.teaching.models import (
    MasteryTemplate,
    PublishRequest,
    TemplateKbRef,
    TemplateRevision,
    TemplateSources,
)
from deeptutor.teaching.snapshot import (
    SnapshotError,
    SourcePlan,
    outline_issues,
    plan_sources,
    remap_modules,
    snapshot_outline,
)
from deeptutor.teaching.store import TeachingStore

logger = logging.getLogger(__name__)


class TeachingError(Exception):
    status_code = 400

    def __init__(self, detail: Any) -> None:
        super().__init__(str(detail))
        self.detail = detail


class TemplateNotFound(TeachingError):
    status_code = 404


class TemplateForbidden(TeachingError):
    status_code = 403


class TemplateConflict(TeachingError):
    status_code = 409


class PublishBlocked(TeachingError):
    status_code = 422


class InvalidStudents(TeachingError):
    status_code = 422


class _StepFailed(Exception):
    def __init__(self, step: str, message: str) -> None:
        super().__init__(message)
        self.step = step


def _language() -> str:
    try:
        from deeptutor.services.settings.interface_settings import get_response_language

        return get_response_language(default="en")
    except Exception:
        return "en"


def _scope(workspace_id: str):
    """Work in the draft's own content workspace, whatever the request's is."""
    from deeptutor.services.workspace.context import current_workspace_id, workspace_context

    if workspace_id == current_workspace_id():
        return nullcontext()
    return workspace_context(workspace_id)


def _accounts() -> dict[str, dict[str, Any]]:
    from deeptutor.multi_user.identity import list_user_info

    return {str(row["id"]): row for row in list_user_info()}


def _blocked(outline: list[str], sources: list[dict], **extra: Any) -> PublishBlocked:
    return PublishBlocked({"outline_issues": outline, "source_issues": sources, **extra})


# ---- per-applied-revision material references ------------------------------


@dataclass
class _References:
    """What a template's KB and book references should hold right now."""

    kb_refs: list[TemplateKbRef] = field(default_factory=list)
    #: Revoked KBs a still-applied revision cites: keep the withdrawal visible.
    retain_revoked_kb_ids: list[str] = field(default_factory=list)
    book_ids: list[str] = field(default_factory=list)


def _retained_revisions(
    store: TeachingStore, template_id: str, latest: int
) -> list[TemplateRevision]:
    """Older revisions an active student's instance is still on.

    ``applied_revision`` is 0 until the instance exists (it then starts on the
    latest revision) and moves forward only when a migration commits, so an
    instance waiting on an old-revision question keeps its revision here.
    Revoked assignments are not entitled to anything.
    """
    numbers = sorted(
        {
            assignment.applied_revision
            for assignment in store.list_assignments(template_id)
            if assignment.status == "active" and 0 < assignment.applied_revision < latest
        }
    )
    revisions = [store.get_revision(template_id, number) for number in numbers]
    return [revision for revision in revisions if revision is not None]


def _references(
    store: TeachingStore,
    template_id: str,
    sources: TemplateSources,
    *,
    latest: int,
    reduce_only: bool,
) -> _References:
    """The references for revision *latest* plus every older revision still applied.

    Older revisions only keep what the template still holds: a material an
    admin revoked (or that is gone) is never granted again through them, its
    withdrawal stays recorded instead. With ``reduce_only`` the latest
    revision is held to the same rule, so a resync can only take access away.
    """
    retained = _retained_revisions(store, template_id, latest)
    needs_live = reduce_only or bool(retained)
    live_kbs = kb_access.referenced_kb_ids(template_id) if needs_live else set()
    live_books = book_access.referenced_book_ids(template_id) if needs_live else set()
    revoked = {row.kb_id for row in kb_access.unavailable_references(template_id)}

    result = _References()
    wanted_kbs: set[str] = set()
    for ref in sources.kb_refs:
        wanted_kbs.add(ref.kb_id)
        if not reduce_only or ref.kb_id in live_kbs:
            result.kb_refs.append(ref)
    result.book_ids = [
        ref.book_id
        for ref in sources.book_refs
        if not reduce_only or ref.book_id in live_books
    ]
    for revision in retained:
        for ref in revision.sources.kb_refs:
            wanted_kbs.add(ref.kb_id)
            if ref.kb_id in live_kbs:
                result.kb_refs.append(ref)
        result.book_ids += [
            ref.book_id for ref in revision.sources.book_refs if ref.book_id in live_books
        ]
    result.book_ids = list(dict.fromkeys(result.book_ids))
    result.retain_revoked_kb_ids = sorted(revoked & wanted_kbs)
    return result


def sync_material_references(template_id: str, *, store: TeachingStore | None = None) -> None:
    """Release references no entitled instance needs any more; never add any.

    Call it after an instance's revision migration commits (Task 10) or after
    an assignment is revoked. Archived and unpublished templates are left
    alone: archiving already released everything. The KB references are
    rewritten in place (withdrawals kept); the book references are only
    rewritten when a book is no longer needed, because plan D re-derives book
    protection on every rewrite. Plan D also rechecks that the current actor
    can read each kept book.
    """
    store = store or TeachingStore()
    # Read and rewrite under one cross-process lock, so a publish (or an
    # archive) cannot land between them and lose what its revision needs.
    with store.material_lock():
        template = store.get_template(template_id)
        if template is None or template.status != "published":
            return
        current = store.get_revision(template_id, template.current_revision)
        if current is None:
            return
        refs = _references(
            store, template_id, current.sources, latest=current.revision, reduce_only=True
        )
        kb_access.replace_template_references(
            template_id,
            refs.kb_refs,
            created_by=template.owner_user_id,
            retain_revoked_kb_ids=refs.retain_revoked_kb_ids,
        )
        if set(refs.book_ids) != book_access.referenced_book_ids(template_id):
            book_access.replace_template_references(
                template_id, refs.book_ids, created_by=template.owner_user_id
            )


class TeachingService:
    def __init__(self, store: TeachingStore | None = None) -> None:
        self.store = store or TeachingStore()

    # ---- identity ---------------------------------------------------------

    @staticmethod
    def _actor() -> tuple[str, bool]:
        from deeptutor.multi_user.roles import current_role

        return get_current_user().id, current_role() == "admin"

    def get(self, template_id: str, *, owner_only: bool = False) -> MasteryTemplate:
        template = self.store.get_template(template_id)
        if template is None:
            raise TemplateNotFound("Teacher path not found")
        user_id, is_admin = self._actor()
        if template.owner_user_id == user_id:
            return template
        if not is_admin:
            # Another teacher's template is not theirs to know about.
            raise TemplateNotFound("Teacher path not found")
        if owner_only:
            raise TemplateForbidden(
                "Only the teacher who owns this path can edit and publish it (its draft "
                "lives in their workspace). Admins can assign, revoke, archive or delete it."
            )
        return template

    @staticmethod
    def _ensure_not_archived(template: MasteryTemplate) -> None:
        if template.status == "archived":
            raise TemplateConflict("This teacher path is archived.")

    # ---- reading ----------------------------------------------------------

    def register(self, path_id: str, workspace_id: str) -> MasteryTemplate:
        """Make one of the caller's own paths a template; idempotent per draft."""
        user_id, _ = self._actor()
        try:
            LearningStore._validate_id(path_id)
        except ValueError as exc:
            raise TeachingError("Invalid path id") from exc
        existing = self.store.find_by_draft(user_id, path_id, workspace_id)
        if existing is not None:
            return existing
        with _scope(workspace_id):
            store = LearningStore()
            progress = store.load(path_id)
            topic = store.get_topic(path_id, progress=progress) if progress else None
        if progress is None or topic is None:
            raise TemplateNotFound("Mastery path not found")
        if progress.origin is not None:
            raise TemplateConflict("A path assigned by a teacher cannot be published again.")
        from deeptutor.learning import policy as learning_policy

        try:
            return self.store.create_template(
                owner_user_id=user_id,
                title=learning_policy.path_display_name(progress),
                description=topic.metadata.description or topic.metadata.goal,
                draft_path_id=path_id,
                draft_workspace_id=workspace_id,
            )
        except sqlite3.IntegrityError:
            # A concurrent register of the same draft won; it is the same template.
            existing = self.store.find_by_draft(user_id, path_id, workspace_id)
            if existing is None:
                raise
            return existing

    def _current_sources(self, template: MasteryTemplate) -> TemplateSources | None:
        if template.current_revision == 0:
            return None
        revision = self.store.get_revision(template.id, template.current_revision)
        return revision.sources if revision is not None else None

    def _unavailable_materials(self, template: MasteryTemplate) -> list[str]:
        """Labels of published materials students can no longer read (spec 7.4).

        Gone from the registries, or revoked by an admin: plans B and D move a
        reference they force-revoke (made private, deleted) to a separate list
        that no longer grants access.
        """
        sources = self._current_sources(template)
        if sources is None:
            return []
        gone_kbs = {ref.kb_id for ref in sources.kb_refs if kb_access.resolve_id(ref.kb_id) is None}
        gone_books = {
            ref.book_id for ref in sources.book_refs if book_access.get(ref.book_id) is None
        }
        gone_kbs |= {revoked.kb_id for revoked in kb_access.unavailable_references(template.id)}
        gone_books |= {
            revoked.book_id for revoked in book_access.unavailable_references(template.id)
        }
        # A cited book whose own KB bindings are missing, revoked or unshared
        # (preflight item 5): its students' tutor stops too.
        for ref in sources.book_refs:
            if ref.book_id in gone_books:
                continue
            try:
                if book_access.unavailable_dependencies(ref.book_id):
                    gone_books.add(ref.book_id)
            except Exception:
                logger.warning("Could not check the bindings of book %s", ref.book_id, exc_info=True)
                gone_books.add(ref.book_id)
        labels = []
        for source in sources.sources:
            ref = kb_access.source_kb_ref(source) or str(source.source_id or "")
            if (ref.startswith("kb:") and ref[3:] in gone_kbs) or (
                ref.startswith("book:") and ref[5:] in gone_books
            ):
                labels.append(source.label)
        return list(dict.fromkeys(labels))

    def describe(self, template: MasteryTemplate, *, accounts: dict | None = None) -> dict:
        accounts = accounts if accounts is not None else _accounts()
        user_id, _ = self._actor()
        owner = accounts.get(template.owner_user_id) or {}
        return {
            **template.model_dump(mode="json", exclude={"kp_aliases"}),
            "owner_name": str(owner.get("username") or ""),
            "is_owner": template.owner_user_id == user_id,
            "assigned_count": self.store.count_active(template.id),
            "materials_unavailable": self._unavailable_materials(template),
        }

    def list_for_actor(self) -> list[dict]:
        user_id, is_admin = self._actor()
        accounts = _accounts()
        templates = self.store.list_templates(owner_user_id=None if is_admin else user_id)
        return [self.describe(template, accounts=accounts) for template in templates]

    def _load_draft(self, template: MasteryTemplate) -> tuple[LearningProgress, MasteryTopic]:
        if not template.draft_path_id:
            raise TemplateConflict(
                "This teacher path has no editable draft any more (its teacher's "
                "account was removed). It can still be assigned, revoked or archived."
            )
        with _scope(template.draft_workspace_id):
            store = LearningStore()
            progress = store.load(template.draft_path_id)
            topic = store.get_topic(template.draft_path_id, progress=progress) if progress else None
        if progress is None or topic is None:
            raise TemplateConflict("The draft path of this teacher path was deleted.")
        return progress, topic

    def _plan(self, template: MasteryTemplate, topic: MasteryTopic) -> SourcePlan:
        """Resolve the draft's sources where the draft lives (cross-check 2.1).

        Planning may register a legacy local book under the current account,
        so it runs only as the template's owner in their own request.
        """
        user = get_current_user()
        if user.id != template.owner_user_id:
            raise TemplateForbidden("Only the teacher who owns this path can publish it.")
        with _scope(template.draft_workspace_id):
            return plan_sources(topic.sources, user, previous=self._current_sources(template))

    # ---- publishing -------------------------------------------------------

    async def preview_publish(self, template_id: str) -> dict:
        """Everything the teacher confirms before publishing; writes nothing."""
        # Every synchronous store/registry section runs off the event loop:
        # the teaching store's writer lock can be held by another thread.
        template = await asyncio.to_thread(self._publishable, template_id)
        progress, topic = await asyncio.to_thread(self._load_draft, template)
        plan = await asyncio.to_thread(self._plan, template, topic)
        issues = outline_issues(progress.modules, topic.sources)
        changes: dict[str, Any] = {
            "first_publish": template.current_revision == 0,
            "items": [],
            "removed": [],
        }
        try:
            outline = snapshot_outline(progress, topic, template.kp_aliases)
        except SnapshotError as exc:
            issues.append(str(exc))
            outline = None
        if outline is not None and template.current_revision > 0:
            previous = await asyncio.to_thread(self._current_revision, template)
            diff = diff_outlines(previous.outline.modules, outline.modules)
            # The model call stays async and outside every writer.
            suggestions = await suggest_changes(diff, language=_language())
            changes["items"] = [suggestion.model_dump() for suggestion in suggestions]
            changes["removed"] = [{"kp_id": p.id, "name": p.name} for p in diff.removed]
        described, affected = await asyncio.to_thread(self._describe_with_count, template)
        return {
            "template": described,
            "draft_revision": progress.version,
            "outline_issues": issues,
            "source_issues": [asdict(issue) for issue in plan.issues],
            "private_kbs": [
                {
                    "kb_id": choice.key,
                    "name": choice.name,
                    "default": choice.default,
                    "via": choice.via,
                    "via_name": choice.via_name,
                }
                for choice in plan.private_kbs
            ],
            "private_books": [
                {"book_id": choice.key, "name": choice.name, "default": choice.default}
                for choice in plan.private_books
            ],
            "books": [{"book_id": book_id, "title": title} for book_id, title in plan.books.items()],
            "changes": changes,
            "module_count": len(progress.modules),
            "knowledge_point_count": sum(len(m.knowledge_points) for m in progress.modules),
            "affected_students": affected,
            "can_publish": not issues and not plan.issues,
        }

    @staticmethod
    def _check_still_present(plan: SourcePlan) -> None:
        gone = [
            (kb_id, "missing_kb")
            for kb_id in plan.kb_refs
            if kb_access.resolve_id(kb_id) is None
        ]
        gone += [
            (title or book_id, "missing_book")
            for book_id, title in plan.books.items()
            if book_access.get(book_id) is None
        ]
        if gone:
            raise _blocked(
                [],
                [
                    {
                        "source_id": "",
                        "label": item,
                        "code": code,
                        "message": f"{item} disappeared while publishing.",
                    }
                    for item, code in gone
                ],
            )

    @staticmethod
    def _run_steps(
        template: MasteryTemplate, steps: list[tuple[str, Callable[[], Any]]], done: list[str]
    ) -> None:
        """Side effects before the revision exists; the first failure stops them."""
        for step, run in steps:
            try:
                run()
            except Exception as exc:
                logger.exception("Publishing teacher path %s failed at %s", template.id, step)
                raise _StepFailed(step, str(exc)) from exc
            done.append(step)

    @staticmethod
    def _share_steps(
        plan: SourcePlan,
        kb_choices: dict[str, str],
        book_choices: dict[str, str],
        explicit_kbs: list[str],
    ) -> list[tuple[str, Callable[[], Any]]]:
        steps: list[tuple[str, Callable[[], Any]]] = [("documents", plan.register_documents)]
        for choice in plan.private_kbs:
            if not choice.via and kb_choices[choice.key] == "make_public":
                steps.append(
                    (f"make_public:kb:{choice.key}", partial(kb_access.make_public, choice.key))
                )
        # Plan D applies these to each book's own KBs: "make_public" (the
        # confirmed choice, default included) publishes a KB; "reference_only"
        # records private intent, so only explicit request choices carry it.
        # A private KB missing from the mapping is shared by reference anyway.
        book_kb_choices = {key: value for key, value in kb_choices.items() if value == "make_public"}
        book_kb_choices.update(dict.fromkeys(explicit_kbs, "reference_only"))
        for choice in plan.private_books:
            if book_choices[choice.key] == "make_public":
                steps.append(
                    (
                        f"make_public:book:{choice.key}",
                        partial(book_access.make_public, choice.key, book_kb_choices),
                    )
                )
            else:
                steps.append(
                    (
                        f"share:book:{choice.key}",
                        partial(book_access.share_kbs, choice.key, book_kb_choices),
                    )
                )
        return steps

    def _after_revision(
        self,
        template: MasteryTemplate,
        revision: TemplateRevision,
        students: list[str],
        user: Any,
        explicit_kbs: list[str],
        explicit_books: list[str],
    ) -> list[dict[str, str]]:
        """References and assignments; each failure becomes a warning.

        The references are computed and written under the store's material
        lock, against the template state read inside it: an archive, a newer
        publish or a concurrent :func:`sync_material_references` cannot
        interleave with them, in this process or another one.
        """
        warnings: list[dict[str, str]] = []

        def warn(step: str, error: BaseException) -> None:
            logger.error(
                "Teacher path %s published, but %s failed", template.id, step, exc_info=error
            )
            warnings.append({"step": step, "message": str(error)})

        archived = False
        try:
            with self.store.material_lock():
                archived = not self._write_references(
                    template, revision, user, explicit_kbs, explicit_books, warn
                )
        except Exception as exc:  # the lock itself (e.g. the store is unwritable)
            warn("knowledge_base_references", exc)
            warn("book_references", exc)
        if students and archived:
            warn("assignments", TemplateConflict("The path was archived while publishing."))
        elif students:
            try:
                self.store.assign(template.id, students, assigned_by=user.id)
            except Exception as exc:
                warn("assignments", exc)
        return warnings

    def _write_references(
        self,
        template: MasteryTemplate,
        revision: TemplateRevision,
        user: Any,
        explicit_kbs: list[str],
        explicit_books: list[str],
        warn: Callable[[str, BaseException], None],
    ) -> bool:
        """Write the references; ``False`` when the path was archived meanwhile."""
        current = self.store.get_template(template.id)
        if current is None or current.status == "archived":
            # Archived (or deleted) meanwhile: its materials stay released.
            archived = TemplateConflict("The path was archived while publishing; nothing was shared.")
            warn("knowledge_base_references", archived)
            warn("book_references", archived)
            return False
        if current.current_revision != revision.revision:
            # A newer publish owns the references now; never write stale ones.
            return True
        try:
            refs = _references(
                self.store,
                template.id,
                revision.sources,
                latest=revision.revision,
                reduce_only=False,
            )
        except Exception as exc:
            warn("knowledge_base_references", exc)
            warn("book_references", exc)
            return True
        try:
            kb_access.replace_template_references(
                template.id,
                refs.kb_refs,
                created_by=user.id,
                explicit_reference_only=explicit_kbs,
                readable_by=user,
                retain_revoked_kb_ids=refs.retain_revoked_kb_ids,
            )
        except Exception as exc:
            warn("knowledge_base_references", exc)
        try:
            book_access.replace_template_references(
                template.id,
                refs.book_ids,
                created_by=user.id,
                explicit_reference_only=explicit_books,
            )
        except Exception as exc:
            warn("book_references", exc)
        return True

    async def publish(self, template_id: str, request: PublishRequest) -> dict:
        # 1. Validate everything; nothing below writes until this block passes.
        # Synchronous store/registry sections run in worker threads (never on
        # the event loop), and nothing awaits while holding a writer.
        template = await asyncio.to_thread(self._publishable, template_id)
        progress, topic = await asyncio.to_thread(self._load_draft, template)
        if progress.version != request.draft_revision:
            raise TemplateConflict(
                "The draft changed after the preview. Review the changes again before publishing."
            )
        user = get_current_user()
        issues = outline_issues(progress.modules, topic.sources)
        plan = await asyncio.to_thread(self._plan, template, topic)
        if issues or plan.issues:
            raise _blocked(issues, [asdict(issue) for issue in plan.issues])
        students = await asyncio.to_thread(self._validate_students, request.student_ids)
        try:
            outline = snapshot_outline(progress, topic, template.kp_aliases)
        except SnapshotError as exc:
            raise _blocked([str(exc)], []) from exc
        aliases = dict(template.kp_aliases)
        if template.current_revision == 0:
            change_log = first_revision_log(outline.modules)
        else:
            previous = await asyncio.to_thread(self._current_revision, template)
            diff = diff_outlines(previous.outline.modules, outline.modules)
            try:
                change_log, new_aliases = build_change_log(diff, request.kp_decisions)
            except ChangeDecisionError as exc:
                raise _blocked([str(exc)], []) from exc
            if new_aliases:
                aliases.update(new_aliases)
                try:
                    outline.modules = remap_modules(outline.modules, new_aliases)
                except SnapshotError as exc:
                    raise _blocked([str(exc)], []) from exc
        await asyncio.to_thread(self._check_still_present, plan)
        # The confirmed choice per private material (defaults included) ...
        kb_choices = {
            choice.key: request.kb_choices.get(choice.key, choice.default)
            for choice in plan.private_kbs
        }
        book_choices = {
            choice.key: request.book_choices.get(choice.key, choice.default)
            for choice in plan.private_books
        }
        # ... and the explicit reference-only consent among them.
        explicit_kbs = [
            choice.key
            for choice in plan.private_kbs
            if request.kb_choices.get(choice.key) == "reference_only"
        ]
        explicit_books = [
            choice.key
            for choice in plan.private_books
            if request.book_choices.get(choice.key) == "reference_only"
        ]

        # 2. Side effects before the revision: documents, visibility, books.
        done: list[str] = []
        steps = self._share_steps(plan, kb_choices, book_choices, explicit_kbs)
        try:
            await asyncio.to_thread(self._run_steps, template, steps, done)
        except _StepFailed as exc:
            raise _blocked(
                [],
                [
                    {
                        "source_id": "",
                        "label": exc.step,
                        "code": "publish_step_failed",
                        "message": str(exc),
                    }
                ],
                failed_step=exc.step,
                completed_steps=done,
            ) from exc

        # 3. The revision.
        sources = await asyncio.to_thread(plan.to_sources, kb_choices, book_choices)
        revision = TemplateRevision(
            template_id=template.id,
            revision=template.current_revision + 1,
            outline=outline,
            sources=sources,
            change_log=change_log,
            published_by=user.id,
        )
        try:
            updated = await asyncio.to_thread(
                self.store.add_revision, revision, aliases=aliases, title=outline.name
            )
        except ValueError as exc:
            logger.exception(
                "Teacher path %s: revision %s could not be written", template.id, revision.revision
            )
            raise TemplateConflict(
                "This path changed while publishing (another publish finished first, or it "
                "was archived). Preview again."
            ) from exc

        # 4. Side effects after the revision: references and assignments.
        warnings = await asyncio.to_thread(
            self._after_revision, template, revision, students, user, explicit_kbs, explicit_books
        )
        described, affected = await asyncio.to_thread(self._describe_with_count, updated)
        return {
            "template": described,
            "revision": revision.revision,
            "change_log": {key: entry.model_dump() for key, entry in change_log.items()},
            "affected_students": affected,
            "warnings": warnings,
        }

    def _publishable(self, template_id: str) -> MasteryTemplate:
        template = self.get(template_id, owner_only=True)
        self._ensure_not_archived(template)
        return template

    def _current_revision(self, template: MasteryTemplate) -> TemplateRevision:
        previous = self.store.get_revision(template.id, template.current_revision)
        if previous is None:
            raise TemplateConflict("The current revision of this teacher path is missing.")
        return previous

    def _describe_with_count(self, template: MasteryTemplate) -> tuple[dict, int]:
        return self.describe(template), self.store.count_active(template.id)

    # ---- assignments ------------------------------------------------------

    def _validate_students(self, student_ids: list[str]) -> list[str]:
        """Only active student accounts (R4); teachers trial their own draft."""
        wanted = [sid for sid in dict.fromkeys(str(i).strip() for i in student_ids) if sid]
        if not wanted:
            return []
        accounts = _accounts()
        students = {
            user_id
            for user_id, row in accounts.items()
            if row.get("role") == "student" and not row.get("disabled")
        }
        if any(student_id not in students for student_id in wanted):
            raise InvalidStudents("Only active student accounts can be assigned.")
        return wanted

    def assignments(self, template_id: str) -> list[dict]:
        template = self.get(template_id)
        accounts = _accounts()
        rows = []
        for assignment in self.store.list_assignments(template.id):
            account = accounts.get(assignment.student_user_id) or {}
            rows.append(
                {
                    **assignment.model_dump(mode="json"),
                    "username": str(account.get("username") or ""),
                    "avatar": str(account.get("avatar") or ""),
                }
            )
        return rows

    def assign(self, template_id: str, student_ids: list[str]) -> list[dict]:
        template = self.get(template_id)
        self._ensure_not_archived(template)
        if template.status != "published":
            raise TemplateConflict("Publish this path before assigning students.")
        students = self._validate_students(student_ids)
        user_id, _ = self._actor()
        self.store.assign(template.id, students, assigned_by=user_id)
        return self.assignments(template.id)

    def revoke(self, template_id: str, student_id: str) -> None:
        """Deny at once; then release old-revision materials nobody else needs."""
        template = self.get(template_id)
        if self.store.revoke(template.id, student_id) is None:
            raise TemplateNotFound("Assignment not found")
        try:
            sync_material_references(template.id, store=self.store)
        except Exception:
            # The revoked assignment already denies access; this only tidies.
            logger.exception("Could not release old materials of teacher path %s", template.id)

    def _release_references(self, template: MasteryTemplate) -> None:
        for step, run in (
            ("knowledge_base_references", kb_access.remove_template_references),
            ("book_references", book_access.remove_template_references),
        ):
            try:
                run(template.id)
            except Exception:
                logger.exception("Could not release %s of teacher path %s", step, template.id)

    def archive(self, template_id: str) -> MasteryTemplate:
        """Stop offering the path; students keep their records but cannot enter."""
        template = self.get(template_id)
        updated = self.store.update_template(template.id, status="archived")
        self._release_references(template)
        return updated

    def delete(self, template_id: str) -> None:
        template = self.get(template_id)
        if template.current_revision > 0 or self.store.has_assignments(template.id):
            raise TemplateConflict(
                "Published or assigned teacher paths cannot be deleted; archive them instead."
            )
        self.store.delete_template(template.id)
        self._release_references(template)

    def list_students(self, query: str = "") -> list[dict]:
        needle = query.strip().casefold()
        return [
            {"id": user_id, "username": str(row["username"]), "avatar": str(row.get("avatar") or "")}
            for user_id, row in _accounts().items()
            if row.get("role") == "student"
            and not row.get("disabled")
            and (not needle or needle in str(row["username"]).casefold())
        ][:200]


def handle_account_deleted(user_id: str, *, successor_id: str) -> None:
    """Keep teaching data alive when an account goes (spec §7.4).

    A deleted teacher's paths move to the admin who deleted them, together
    with their active KB/book reference ownership, so assigned students keep
    learning; the draft is detached, so the path can be assigned, revoked or
    archived but never republished (D17). A deleted student's assignments are
    revoked so the counts teachers see stay true.
    """
    store = TeachingStore()
    # Independent hand-overs: one failing must not skip the other.
    try:
        moved = store.transfer_owner(user_id, successor_id)
        if moved:
            logger.info("Transferred %d teacher path(s) of %s to %s", moved, user_id, successor_id)
    except Exception:
        logger.exception("Could not transfer teacher paths of deleted user %s", user_id)
    try:
        studied = {
            assignment.template_id
            for assignment in store.assignments_for_student(user_id)
            if assignment.status == "active"
        }
        store.revoke_all_for_student(user_id)
    except Exception:
        logger.exception("Could not revoke assignments of deleted user %s", user_id)
        return
    for template_id in sorted(studied):
        try:
            # Like a single revoke: release old-revision materials nobody needs.
            sync_material_references(template_id, store=store)
        except Exception:
            logger.exception("Could not release old materials of teacher path %s", template_id)
