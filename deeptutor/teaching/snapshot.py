"""Turn a teacher's draft path into an immutable, shareable snapshot.

Only the curriculum travels: modules, knowledge points (with stable ids),
prerequisites, pass thresholds and the sources students may read. Nothing the
teacher produced while trialling the path — mastery, answers, reviews — is
part of a snapshot, because :class:`TemplateOutline` has no field for it.

Planning is read-only. Registering missing document ids is a separate step
(:meth:`SourcePlan.register_documents`) the publisher runs after every check
has passed.
"""

from __future__ import annotations

from contextlib import ExitStack, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deeptutor.learning import policy as learning_policy
from deeptutor.learning.models import (
    LearningModule,
    LearningProgress,
    MasteryTopic,
    TopicSource,
    TopicSourceKind,
)
from deeptutor.learning.objective_relations import (
    ObjectiveRelationError,
    runnable_module_issues,
    validate_objective_relations,
)
from deeptutor.teaching import books as book_access
from deeptutor.teaching import kb as kb_access
from deeptutor.teaching.models import (
    TemplateBookRef,
    TemplateDocRef,
    TemplateKbRef,
    TemplateOutline,
    TemplateSources,
)

#: Sources that live only in the teacher's own account (spec 5.1, 7.4).
PERSONAL_KINDS = frozenset(
    {
        TopicSourceKind.NOTEBOOK,
        TopicSourceKind.CHAT,
        TopicSourceKind.QUESTION_BANK,
        TopicSourceKind.COWRITER,
        TopicSourceKind.PARTNER_GROUP,
    }
)


class SnapshotError(ValueError):
    """The draft cannot be expressed as a consistent snapshot."""


@dataclass(frozen=True)
class SourceIssue:
    source_id: str
    label: str
    code: str
    message: str


@dataclass(frozen=True)
class PrivateChoice:
    """A private KB or book of the teacher: make public, or share by reference."""

    key: str
    name: str
    default: str
    #: The book (id, title) that cites this KB, when the path does not cite it directly.
    via: str = ""
    via_name: str = ""


@dataclass
class PlannedDocument:
    source_id: str
    kb_id: str
    rel_path: str
    path: Path
    #: Plan B's ``KbDocument``; ``None`` until registered.
    doc: Any | None


@dataclass
class SourcePlan:
    sources: list[TopicSource] = field(default_factory=list)
    #: kb_id -> ``None`` for the whole base, or the documents' ``rel_path`` list.
    kb_refs: dict[str, list[str] | None] = field(default_factory=dict)
    documents: list[PlannedDocument] = field(default_factory=list)
    #: book_id -> title.
    books: dict[str, str] = field(default_factory=dict)
    issues: list[SourceIssue] = field(default_factory=list)
    private_kbs: list[PrivateChoice] = field(default_factory=list)
    private_books: list[PrivateChoice] = field(default_factory=list)

    def register_documents(self) -> None:
        for planned in self.documents:
            if planned.doc is None or planned.doc.deleted:
                planned.doc = kb_access.record_document(
                    planned.kb_id, planned.rel_path, planned.path
                )

    def to_sources(
        self, kb_choices: dict[str, str], book_choices: dict[str, str]
    ) -> TemplateSources:
        if any(planned.doc is None or planned.doc.deleted for planned in self.documents):
            raise SnapshotError("Register the documents before taking the snapshot.")
        private_kbs = {choice.key: choice.default for choice in self.private_kbs}
        private_books = {choice.key: choice.default for choice in self.private_books}
        by_source = {planned.source_id: planned for planned in self.documents}
        sources: list[TopicSource] = []
        for source in self.sources:
            copy = source.model_copy(deep=True)
            planned = by_source.get(source.id)
            if planned is not None:
                copy.metadata["doc_id"] = planned.doc.doc_id
                copy.metadata["doc_version"] = planned.doc.version
            sources.append(copy)
        kb_refs = []
        for kb_id, rel_paths in self.kb_refs.items():
            documents = [planned for planned in self.documents if planned.kb_id == kb_id]
            kb_refs.append(
                TemplateKbRef(
                    kb_id=kb_id,
                    doc_ids=(
                        None
                        if rel_paths is None
                        else list(dict.fromkeys(planned.doc.doc_id for planned in documents))
                    ),
                    documents=[
                        TemplateDocRef(
                            doc_id=planned.doc.doc_id,
                            rel_path=planned.rel_path,
                            version=planned.doc.version,
                        )
                        for planned in documents
                    ],
                    access=_access(kb_id, private_kbs, kb_choices),
                )
            )
        book_refs = [
            TemplateBookRef(
                book_id=book_id,
                access=_access(book_id, private_books, book_choices),
                kb_ids=book_access.book_kb_ids(book_id),
            )
            for book_id in self.books
        ]
        return TemplateSources(sources=sources, kb_refs=kb_refs, book_refs=book_refs)


def _access(key: str, private: dict[str, str], choices: dict[str, str]) -> str:
    if key not in private:
        return "public"
    return "reference_only" if choices.get(key, private[key]) == "reference_only" else "public"


def _issue(source: TopicSource, code: str, message: str) -> SourceIssue:
    return SourceIssue(source_id=source.id, label=source.label, code=code, message=message)


def source_scope(source: TopicSource):
    """Resolve a source in the content workspace it was picked from, if it says so."""
    workspace_id = (source.metadata or {}).get("content_workspace_id")
    if workspace_id is None:
        return nullcontext()
    from deeptutor.services.workspace.context import current_workspace_id, workspace_context

    workspace_id = str(workspace_id)
    return nullcontext() if workspace_id == current_workspace_id() else workspace_context(workspace_id)


def _default(previous: dict[str, str], key: str, fallback: str = "make_public") -> str:
    if key in previous:
        return "reference_only" if previous[key] == "reference_only" else "make_public"
    return fallback


def plan_sources(
    sources: list[TopicSource], user: Any, *, previous: TemplateSources | None = None
) -> SourcePlan:
    """Classify every draft source for sharing and rewrite the shareable ones.

    Call it in the owner's request, inside the draft's workspace. ``previous``
    is the last published revision's sources: a republish defaults each private
    KB or book to the choice made then (cross-check 6.4).
    """
    plan = SourcePlan()
    prior_kbs = {ref.kb_id: ref.access for ref in previous.kb_refs} if previous else {}
    prior_books = {ref.book_id: ref.access for ref in previous.book_refs} if previous else {}
    seen_private_kbs: set[str] = set()
    for source in sorted(sources, key=lambda item: item.position):
        if source.kind == TopicSourceKind.GOAL:
            plan.sources.append(source.model_copy(deep=True))
            continue
        if source.kind in PERSONAL_KINDS:
            plan.issues.append(
                _issue(
                    source,
                    "personal_source",
                    f"{source.label} is a personal {source.kind.value} source and cannot be "
                    "shared with students. Upload it to a knowledge base and reference "
                    "that knowledge base instead.",
                )
            )
            continue
        with ExitStack() as stack:
            try:
                stack.enter_context(source_scope(source))
            except Exception:
                plan.issues.append(
                    _issue(
                        source,
                        "workspace_unavailable",
                        f"{source.label}: the workspace it came from is unavailable.",
                    )
                )
                continue
            if source.kind == TopicSourceKind.BOOK:
                _plan_book(plan, source, user, prior_books, prior_kbs, seen_private_kbs)
            else:
                _plan_kb(plan, source, user, prior_kbs, seen_private_kbs)
    return plan


def _plan_kb(
    plan: SourcePlan,
    source: TopicSource,
    user: Any,
    prior_kbs: dict[str, str],
    seen_private_kbs: set[str],
) -> None:
    record = kb_access.resolve(kb_access.source_kb_ref(source))
    if record is None:
        plan.issues.append(
            _issue(source, "missing_kb", f"{source.label}: the knowledge base no longer exists.")
        )
        return
    if not kb_access.can_read(record, user):
        # E.g. an admin forced another account's public KB back to private.
        plan.issues.append(
            _issue(
                source,
                "not_shareable",
                f"{source.label} is someone else's private knowledge base.",
            )
        )
        return
    if kb_access.is_connector(record):
        plan.issues.append(
            _issue(
                source,
                "connector",
                f"{source.label} is a personal connector (a folder, vault or external "
                "service) and cannot be shared. Upload the material to a knowledge "
                "base instead.",
            )
        )
        return
    if record.visibility != "public":
        if not kb_access.can_write(record, user):
            plan.issues.append(
                _issue(
                    source,
                    "not_shareable",
                    f"{source.label} is someone else's private knowledge base.",
                )
            )
            return
        if record.kb_id not in seen_private_kbs:
            seen_private_kbs.add(record.kb_id)
            plan.private_kbs.append(
                PrivateChoice(record.kb_id, record.name, _default(prior_kbs, record.kb_id))
            )
    if not kb_access.kb_ready(record):
        plan.issues.append(
            _issue(source, "not_indexed", f"{source.label} has not finished indexing.")
        )
        return
    rewritten = source.model_copy(deep=True)
    # A teacher's content workspace means nothing in a student's account.
    rewritten.metadata.pop("content_workspace_id", None)
    if source.kind == TopicSourceKind.KNOWLEDGE_BASE:
        rewritten.source_id = record.ref
        plan.kb_refs[record.kb_id] = None
        plan.sources.append(rewritten)
        return
    rel_path = kb_access.source_rel_path(source)
    doc_id = kb_access.source_doc_id(source)
    doc = None
    if doc_id:
        # A source that already names its document follows the fixed id to
        # wherever it lives now; whatever occupies the old path is unrelated.
        doc = kb_access.live_document(record.kb_id, doc_id)
        rel_path = doc.rel_path if doc is not None else ""
    path = kb_access.raw_file(record, rel_path)
    if path is None:
        plan.issues.append(
            _issue(
                source,
                "missing_document",
                f"{source.label}: the document "
                f"{kb_access.source_rel_path(source)!r} no longer exists in {record.name}.",
            )
        )
        return
    rewritten.metadata.pop("knowledge_base", None)
    rewritten.metadata.pop("doc_id", None)
    rewritten.metadata.pop("doc_version", None)
    rewritten.metadata["kb_name"] = record.ref
    rewritten.metadata["path"] = rel_path
    plan.documents.append(
        PlannedDocument(
            source_id=source.id,
            kb_id=record.kb_id,
            rel_path=rel_path,
            path=path,
            doc=doc if doc is not None else kb_access.find_document(record.kb_id, rel_path),
        )
    )
    existing = plan.kb_refs.get(record.kb_id, [])
    if existing is not None and rel_path not in existing:
        plan.kb_refs[record.kb_id] = [*existing, rel_path]
    plan.sources.append(rewritten)


def _book_kb_blocked(plan: SourcePlan, source: TopicSource, detail: str) -> None:
    plan.issues.append(_issue(source, "book_kb_blocked", f"{source.label}: {detail}"))


def _plan_book(
    plan: SourcePlan,
    source: TopicSource,
    user: Any,
    prior_books: dict[str, str],
    prior_kbs: dict[str, str],
    seen_private_kbs: set[str],
) -> None:
    try:
        record = book_access.resolve(str(source.source_id or ""))
    except book_access.BookCollisionError:
        plan.issues.append(
            _issue(
                source,
                "not_shareable",
                f"{source.label} exists only in this workspace and shares its id with "
                "another registered book, so it cannot be shared.",
            )
        )
        return
    if record is None:
        plan.issues.append(_issue(source, "missing_book", f"{source.label}: the book no longer exists."))
        return
    if record.visibility != "public":
        if not (book_access.can_read(record, user) and book_access.can_write(record, user)):
            plan.issues.append(
                _issue(source, "not_shareable", f"{source.label} is someone else's private book.")
            )
            return
        plan.private_books.append(
            PrivateChoice(record.book_id, record.title, _default(prior_books, record.book_id))
        )
        # Sharing a private book shares the KBs it cites (plan D).
        try:
            book_plan = book_access.plan_kbs(record.book_id)
        except book_access.BookRegistryError as exc:
            _book_kb_blocked(plan, source, str(exc))
            return
        for blocked in book_plan.issues:
            _book_kb_blocked(plan, source, f"{blocked.name}: {blocked.message}")
        for ref in book_plan.missing:
            # A missing source is reported, never dropped from the book's set.
            _book_kb_blocked(plan, source, f"{ref}: the knowledge base no longer exists.")
        for kb in book_plan.to_dict()["private_kbs"]:
            if kb["kb_id"] in seen_private_kbs:
                continue
            seen_private_kbs.add(kb["kb_id"])
            plan.private_kbs.append(
                PrivateChoice(
                    kb["kb_id"],
                    kb["name"],
                    _default(prior_kbs, kb["kb_id"], kb["default"]),
                    via=record.book_id,
                    via_name=record.title,
                )
            )
    else:
        # A public book's KBs were settled when it was made public; what an
        # admin has since revoked (or what went missing) must not shrink it.
        for dependency in book_access.unavailable_dependencies(record.book_id):
            name = dependency.name or dependency.ref or "its knowledge bases"
            _book_kb_blocked(plan, source, f"{name} is unavailable ({dependency.reason}).")
    rewritten = source.model_copy(deep=True)
    rewritten.metadata.pop("content_workspace_id", None)
    rewritten.source_id = record.ref
    plan.books[record.book_id] = record.title
    plan.sources.append(rewritten)


def outline_issues(modules: list[LearningModule], sources: list[TopicSource]) -> list[str]:
    issues = runnable_module_issues(modules)
    if issues:
        return issues
    try:
        validate_objective_relations(modules, sources)
    except ObjectiveRelationError as exc:
        issues.append(str(exc))
    return issues


def remap_modules(modules: list[LearningModule], aliases: dict[str, str]) -> list[LearningModule]:
    """Copy *modules* with draft objective ids replaced by their stable ids."""
    mapped: list[LearningModule] = []
    for module in modules:
        copy = module.model_copy(deep=True)
        for point in copy.knowledge_points:
            point.id = aliases.get(point.id, point.id)
            point.prerequisite_ids = [aliases.get(ref, ref) for ref in point.prerequisite_ids]
        mapped.append(copy)
    ids = [point.id for module in mapped for point in module.knowledge_points]
    if len(ids) != len(set(ids)):
        raise SnapshotError(
            "Two knowledge points map to the same stable id. Rename or remove one of "
            "them and preview again."
        )
    return mapped


def snapshot_outline(
    progress: LearningProgress, topic: MasteryTopic, aliases: dict[str, str]
) -> TemplateOutline:
    return TemplateOutline(
        name=learning_policy.path_display_name(progress),
        goal=topic.metadata.goal,
        description=topic.metadata.description,
        emoji=topic.metadata.emoji,
        modules=remap_modules(progress.modules, aliases),
    )
