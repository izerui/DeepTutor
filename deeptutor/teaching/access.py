"""Reading a teacher path's knowledge bases and books.

A library or book a teacher shared "by reference only" stays private. Plans
B and D ask the registered reader of each reference whether a user may read
it through that reference; this module answers for ``mastery_template``
references: yes only while that user is studying that very path, their
assignment is active and the path is still published. Chat, writing and every
other surface never study a path, so these materials cannot be used there
(spec 7.4); revoking or archiving closes them at once.

"Studying a path" is one of:

- an explicit :func:`studying_template` block (admission checks, one
  Study-page original-content request — R9);
- a mastery turn on a teacher path: the turn's retrieval scope
  (:class:`~deeptutor.multi_user.retrieval_scope.TurnRetrievalScope`) records
  the template the turn is on *now*. That scope is a shared mutable object, so
  a path switch inside the turn moves the reading scope with it — a context
  variable set at turn start could not follow a switch made in a copied
  context.

The readers are KB/book-level. Which documents of a referenced library a
student may open, and which revision's materials they are entitled to, are
enforced where the content is served: the turn's retrieval allowlist (R8) and
:func:`entitled_materials` / :func:`original_document_path` for originals.

Registration (cross-check 1.3, D2 loader contract): plans B and D import the
modules in their ``BUILTIN_*_READER_MODULES`` lazily and call their hooks
(:func:`register_kb_reader` / :func:`register_book_readers`) with
``setdefault`` semantics, outside their reader-map locks. The import-time
registration below follows the same never-clobber rule, so an explicitly
registered custom reader always wins.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import logging
from pathlib import PurePosixPath
from typing import Any

logger = logging.getLogger(__name__)

REFERRER_KIND = "mastery_template"

#: The teacher template an explicit block or one original-content request studies.
active_mastery_template: ContextVar[str] = ContextVar("active_mastery_template", default="")
#: One original-content request of a path material (R9): the live path of
#: the cited document it names, and the canonical book it reads. Set by the
#: request's dependency for that request's own context only.
original_document: ContextVar[str] = ContextVar("mastery_original_document", default="")
original_book: ContextVar[str] = ContextVar("mastery_original_book", default="")


@contextmanager
def studying_template(template_id: str) -> Iterator[None]:
    token = active_mastery_template.set(template_id or "")
    try:
        yield
    finally:
        active_mastery_template.reset(token)


def studied_template() -> str:
    """The template being studied right here, or ``""``.

    An explicit :func:`studying_template` wins; otherwise the innermost
    restricted turn scope names it (``""`` on a learner's own path).
    """
    explicit = active_mastery_template.get()
    if explicit:
        return explicit
    from deeptutor.multi_user.retrieval_scope import current_retrieval_scope

    scope = current_retrieval_scope()
    while scope is not None:
        if scope.kb_ids is not None:
            return scope.template_id
        scope = scope.parent
    return ""


def _assignment_open(template_id: str, user_id: str) -> bool:
    from deeptutor.teaching.store import TeachingStore

    store = TeachingStore()  # read only: never writes inside ``material_lock``
    template = store.get_template(template_id)
    if template is None or template.status != "published":
        return False
    assignment = store.get_assignment(template_id, user_id)
    return assignment is not None and assignment.status == "active"


def open_template_for_current_user(template_id: str) -> bool:
    """May the caller open this path's materials right now (published, assigned)?"""
    from deeptutor.multi_user.context import get_current_user

    return bool(template_id) and _assignment_open(template_id, get_current_user().id)


def template_reader(template_id: str, user_id: str) -> bool:
    """Reader for ``referrer_kind="mastery_template"`` (knowledge bases and books)."""
    if not template_id or studied_template() != template_id:
        return False
    from deeptutor.multi_user.context import get_current_user

    # The scope belongs to the account that opened it; an inherited context
    # never lets it answer for someone else.
    if get_current_user().id != user_id:
        return False
    return _assignment_open(template_id, user_id)


def register_kb_readers() -> None:
    """Register the knowledge-base reader (explicit registration)."""
    from deeptutor.knowledge import registry

    registry.register_reference_reader(REFERRER_KIND, template_reader)


def register_kb_reader() -> None:
    """Plan B's builtin-loader hook: staged by the loader with ``setdefault`` semantics."""
    register_kb_readers()


def register_book_readers() -> None:
    """Plan D's loader calls this every time it loads its builtin reader modules."""
    from deeptutor.book import registry as book_registry

    book_registry.register_book_reference_reader(REFERRER_KIND, template_reader)


def _register_defaults() -> None:
    """Import-time registration that never replaces an existing reader.

    Both registries are imported before any reader lock is taken, so this
    never waits on an import while holding a lock a loader needs.
    """
    from deeptutor.book import registry as book_registry
    from deeptutor.knowledge import registry

    with registry._reader_lock:
        if REFERRER_KIND not in registry._readers:
            registry.register_reference_reader(REFERRER_KIND, template_reader)
    with book_registry._reader_lock:
        if REFERRER_KIND not in book_registry._readers:
            book_registry.register_book_reference_reader(REFERRER_KIND, template_reader)


# ---- what an assigned student may open (R9, preflight items 3, 4, 6) -------


@dataclass(frozen=True)
class EntitledMaterials:
    """The materials of the revision a student's copy is on.

    ``kbs`` maps a KB id to the document ids the path cites (``None`` = the
    whole library). ``captured`` maps a KB id to ``{rel_path as published:
    doc_id}`` so a stale published path still names its document.
    ``book_kbs`` maps a KB id to the path's books bound to it.
    """

    template_id: str
    revision: int
    kbs: dict[str, frozenset[str] | None] = field(default_factory=dict)
    captured: dict[str, dict[str, str]] = field(default_factory=dict)
    books: tuple[str, ...] = ()
    book_kbs: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def cites_kb(self, kb_id: str) -> bool:
        return kb_id in self.kbs or kb_id in self.book_kbs

    def document_scope(self, kb_id: str) -> frozenset[str] | None:
        """``None`` when the whole library is entitled."""
        if kb_id in self.book_kbs:
            return None  # a book cites its libraries whole
        return self.kbs.get(kb_id, frozenset())


def _applied_revision(template: Any, assignment: Any) -> int:
    """The revision the student's copy is on (preflight item 6)."""
    from deeptutor.learning.storage import LearningStore
    from deeptutor.services.workspace.context import current_workspace_id, workspace_context
    from deeptutor.teaching.instances import instance_path_id

    path_id = assignment.instance_path_id or instance_path_id(template.id)
    try:
        if current_workspace_id():
            with workspace_context(""):
                origin = LearningStore().load_origin(path_id)
        else:
            origin = LearningStore().load_origin(path_id)
    except Exception:
        logger.warning("Could not read the copy of teacher path %s", template.id, exc_info=True)
        origin = None
    if origin is not None and origin.template_id == template.id and origin.revision > 0:
        return origin.revision
    # No copy yet: it will start on the current revision.
    return assignment.applied_revision or template.current_revision


def entitled_materials(template_id: str) -> EntitledMaterials | None:
    """What the current user may open through *template_id*; ``None`` if closed.

    Storage-bound. Reads the teaching database only.
    """
    from deeptutor.multi_user.context import get_current_user
    from deeptutor.teaching import books as book_access
    from deeptutor.teaching.store import TeachingStore

    if not template_id:
        return None
    store = TeachingStore()
    template = store.get_template(template_id)
    if template is None or template.status != "published":
        return None
    assignment = store.get_assignment(template_id, get_current_user().id)
    if assignment is None or assignment.status != "active":
        return None
    number = _applied_revision(template, assignment)
    revision = store.get_revision(template_id, number)
    if revision is None:
        return EntitledMaterials(template_id, number)
    kbs: dict[str, frozenset[str] | None] = {}
    captured: dict[str, dict[str, str]] = {}
    for ref in revision.sources.kb_refs:
        kbs[ref.kb_id] = None if ref.doc_ids is None else frozenset(ref.doc_ids)
        captured[ref.kb_id] = {doc.rel_path: doc.doc_id for doc in ref.documents}
    books = tuple(dict.fromkeys(ref.book_id for ref in revision.sources.book_refs))
    book_kbs: dict[str, list[str]] = {}
    for book_id in books:
        for kb_id in book_access.book_kb_ids(book_id):
            book_kbs.setdefault(kb_id, []).append(book_id)
    return EntitledMaterials(
        template_id,
        revision.revision,
        kbs,
        captured,
        books,
        {kb_id: tuple(ids) for kb_id, ids in book_kbs.items()},
    )


def _clean_rel_path(value: str) -> str:
    parts = [p for p in PurePosixPath(str(value or "").replace("\\", "/")).parts if p not in ("/", ".")]
    if not parts or ".." in parts:
        return ""
    return "/".join(parts)


def original_document_path(
    materials: EntitledMaterials, kb_id: str, requested: str, *, doc_id: str = ""
) -> str | None:
    """The live path of the entitled document a request names, or ``None``.

    Follows the document's fixed id, never the path captured at publish: an
    explicit ``doc_id`` first, then a path the revision captured for one of
    its documents (which may since hold an unrelated file), then the
    requested path itself. ``None`` when the named document is not one the
    entitled revision cites, or no longer exists.
    """
    from deeptutor.teaching import kb as kb_access

    scope = materials.document_scope(kb_id)
    if doc_id:
        doc = kb_access.live_document(kb_id, doc_id)
        if doc is None or (scope is not None and doc.doc_id not in scope):
            return None
        return doc.rel_path
    rel = _clean_rel_path(requested)
    if not rel:
        return None
    cited = materials.captured.get(kb_id, {}).get(rel)
    if cited and (scope is None or cited in scope):
        doc = kb_access.live_document(kb_id, cited)
        return doc.rel_path if doc is not None else None
    if scope is None:
        return rel
    live = kb_access.find_document(kb_id, rel)
    if live is not None and not live.deleted and live.doc_id in scope:
        return rel
    return None


def document_entitled(materials: EntitledMaterials, kb_id: str, rel_path: str) -> bool:
    """Whether the live document now at *rel_path* is one the revision cites.

    For originals addressed by something stable other than a path (a visual
    asset follows its source when it moves), so no captured-path mapping.
    """
    from deeptutor.teaching import kb as kb_access

    scope = materials.document_scope(kb_id)
    if scope is None:
        return True
    rel = _clean_rel_path(rel_path)
    live = kb_access.find_document(kb_id, rel) if rel else None
    return live is not None and not live.deleted and live.doc_id in scope


# ---- one Study-page original-content request (R9) ---------------------------


@dataclass(frozen=True)
class OriginalGrant:
    """What one original-content request of a path material may do.

    ``rel_path``: the live path to serve (``""`` when not path-addressed);
    ``studying``: the request needs the template's reference access (the
    caller cannot read the material on its own); ``books``: precomputed plan
    D reading bindings of the path's books the material belongs to.
    """

    rel_path: str = ""
    studying: bool = False
    books: tuple = ()
    book_id: str = ""


class OriginalDenied(PermissionError):
    """The path names this library, but not the requested document of it."""


def _asset_source(record: Any, asset_id: str) -> str:
    from pathlib import Path

    from deeptutor.services.rag.visual_assets import VisualAssetStore

    entry = VisualAssetStore(Path(record.base_dir) / record.dir_name).records().get(asset_id)
    return str(entry.get("source_path") or "") if isinstance(entry, dict) else ""


def _path_book_bindings(template_id: str, book_ids: tuple[str, ...]) -> tuple:
    from deeptutor.book.registry import reading_bindings

    with studying_template(template_id):
        return reading_bindings(*book_ids)


def kb_original_grant(
    materials: EntitledMaterials,
    kb_ref: str,
    *,
    document: str = "",
    doc_id: str = "",
    asset_id: str = "",
) -> OriginalGrant | None:
    """Authorize one original of a path library, document by document.

    ``None``: the library is not one of the path's materials (or unknown);
    the request keeps its ordinary rules. A cited document is served at its
    live path, followed by fixed id. A document the revision does not cite
    is served only to a caller who can read the library independently
    (owner, public, another reference) — library-level retrieval access never
    widens downloads. Book-derived libraries authorize path -> canonical book
    -> library server-side and carry that book's reading scope. Storage-bound.
    """
    from deeptutor.knowledge import registry
    from deeptutor.multi_user.context import get_current_user

    try:
        record = registry.resolve_ref(str(kb_ref or ""))
    except Exception:
        return None
    if record is None or not materials.cites_kb(record.kb_id):
        return None
    independent = registry.can_read(record, get_current_user())
    if asset_id:
        source = _asset_source(record, asset_id)
        entitled = bool(source) and document_entitled(materials, record.kb_id, source)
        live = ""
    else:
        live = original_document_path(materials, record.kb_id, document, doc_id=doc_id) or ""
        entitled = bool(live)
    if not entitled:
        if independent:
            return None
        raise OriginalDenied(record.kb_id)
    books: tuple = ()
    if not independent:
        book_ids = materials.book_kbs.get(record.kb_id, ())
        if book_ids:
            books = _path_book_bindings(materials.template_id, book_ids)
    return OriginalGrant(rel_path=live, studying=not independent, books=books)


def book_original_grant(materials: EntitledMaterials, book_ref: str) -> OriginalGrant | None:
    """Authorize a path book's own originals (book, spine, pages); ``None`` otherwise.

    Only a book the entitled revision cites, bound to its registered
    identity (never a same-id local copy). Storage-bound.
    """
    from deeptutor.book import registry as book_registry

    book_id = book_registry.strip_ref(str(book_ref or ""))
    if not book_id or book_id not in materials.books:
        return None
    bindings = _path_book_bindings(materials.template_id, (book_id,))
    if not bindings:
        return None
    return OriginalGrant(studying=True, books=bindings, book_id=book_id)


_register_defaults()
