"""Local editing and canonical registry access, with reader-owned learning state."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from deeptutor.book import registry
from deeptutor.book.learning_overlay import BookLearningOverlay
from deeptutor.book.models import Book, Progress
from deeptutor.book.storage import BookStorage, get_book_storage
from deeptutor.services.path_service import PathService

from .book_permission import permission_for_user
from .context import get_current_user
from .models import LOCAL_ADMIN_ID, CurrentUser, UserScope
from .paths import get_current_path_service

if TYPE_CHECKING:
    from deeptutor.book.engine import BookEngine

BookSource = Literal["own", "shared"]
logger = logging.getLogger(__name__)


def _auth_enabled() -> bool:
    from deeptutor.services.auth import AUTH_ENABLED

    return bool(AUTH_ENABLED)


def _owner_id() -> str:
    user = get_current_user()
    return LOCAL_ADMIN_ID if user.is_admin else user.id


def _my_owner_ids(user: CurrentUser) -> set[str]:
    return {user.id, LOCAL_ADMIN_ID} if user.is_admin else {user.id}


def storage_for(record: registry.BookRecord) -> BookStorage:
    return BookStorage(path_service=PathService(workspace_root=Path(record.base_dir).parents[2]))


def engine_for(record: registry.BookRecord) -> BookEngine:
    from deeptutor.book.engine import get_book_engine

    return get_book_engine(storage=storage_for(record))


def _initialize_links(book: Book, record: registry.BookRecord) -> bool:
    """Repair only in the physical source scope, under its registered owner.

    The synthetic scope is internal: a foreign reader's workspace, selections
    and local same-name KBs must never influence a pending binding.
    """
    from deeptutor.book.registry_backfill import _confirmed_missing
    from deeptutor.multi_user.paths import user_context
    from deeptutor.services.workspace.context import WorkspaceScope, workspace_context
    from deeptutor.services.workspace.knowledge import learning_source_kbs

    if registry.kb_links_initialized(record.book_id):
        return True
    root = storage_for(record).path_service.workspace_root
    admin = record.created_by == LOCAL_ADMIN_ID
    actor = CurrentUser(
        id=record.created_by, username=record.created_by,
        role="admin" if admin else "user",
        scope=UserScope(kind="admin" if admin else "user", user_id=record.created_by, root=root),
    )
    with user_context(actor), workspace_context(WorkspaceScope("", root, root)):
        token = learning_source_kbs.set(frozenset())
        try:
            links = registry.book_kb_links(record.book_id)
            refs = [ref for ref in book.knowledge_bases if ref not in links]
            missing = _confirmed_missing(refs) if any(not r.startswith("kb:") for r in refs) else []
            return registry.initialize_book_kb_links(
                record.book_id, book.knowledge_bases, confirmed_missing=missing,
            )
        finally:
            learning_source_kbs.reset(token)


def _retry_links(book: Book, record: registry.BookRecord) -> None:
    try:
        _initialize_links(book, record)
    except Exception:
        logger.warning("Book %s KB binding remains pending", book.id, exc_info=True)


def ensure_book_registered(
    book: Book, storage: BookStorage | None = None,
) -> registry.BookRecord | None:
    store = storage or get_book_storage()
    base_dir = store.path_service.get_book_dir()
    dirname = registry.dir_name_for(book.id)
    record = registry.find_book_by_location(base_dir, dirname)
    if record is None:
        try:
            record = registry.register_book(
                base_dir=base_dir, dir_name=dirname, title=book.title, created_by=_owner_id(),
            )
        except registry.BookRegistryError:
            # Only a proven identity collision is a legitimate local-only book.
            other = registry.get_book(book.id)
            if other is None or other.base_dir == str(base_dir.resolve()):
                raise
            logger.warning("Book %s has a canonical identity elsewhere; serving locally.", book.id)
            return None
    _retry_links(book, record)
    return record


def register_current_scope_book(book_id: str) -> registry.BookRecord | None:
    store = get_book_storage()
    book = store.load_book(registry.strip_ref(book_id))
    return ensure_book_registered(book, store) if book is not None else None


def prepare_new_book(book: Book, storage: BookStorage) -> registry.BookRecord:
    """Complete birth prerequisites before publishing a manifest or ready event."""
    from deeptutor.book.registry_backfill import ensure_shelf_creation_boundary
    from deeptutor.multi_user.paths import get_admin_path_service

    base_dir = storage.path_service.get_book_dir()
    if base_dir.resolve() == get_admin_path_service().get_book_dir().resolve():
        ensure_shelf_creation_boundary()
    if storage.book_exists(book.id):
        raise registry.BookRegistryError(f"Book {book.id} already exists.")
    record = registry.register_new_book(
        base_dir=base_dir, dir_name=registry.dir_name_for(book.id),
        title=book.title, created_by=_owner_id(),
    )
    if not _initialize_links(book, record):
        raise registry.BookKbResolutionError("Book KB bindings are pending; retry creation.")
    return record


@dataclass(frozen=True, slots=True)
class ResolvedBook:
    """A Book bound to its canonical store and the caller's learning store."""

    engine: BookEngine
    source: BookSource
    permission: Literal["read", "edit"]
    can_edit: bool
    can_delete: bool
    learning: BookStorage | BookLearningOverlay
    record: registry.BookRecord | None = None

    @property
    def is_shared(self) -> bool:
        return self.source == "shared"

    def capabilities(self) -> dict[str, Any]:
        from .roles import is_teacher_or_admin

        record = self.record
        owned = self.source == "own" or (
            record is not None and record.created_by in _my_owner_ids(get_current_user())
        )
        return {
            "source": self.source,
            "permission": self.permission,
            "can_edit": self.can_edit,
            "can_delete": self.can_delete,
            "ref": record.ref if record else "",
            "visibility": record.visibility if record else "private",
            "owner_id": record.created_by if record else "",
            "group": "mine" if owned else "public",
            "can_set_visibility": bool(
                record and self.can_edit and registry.can_write_book(record, get_current_user())
                and is_teacher_or_admin()
            ),
        }

    def load_progress(self, book_id: str) -> Progress:
        progress = self.learning.load_progress(book_id)
        return progress or Progress(book_id=book_id)

    def reading_summary(self, book: Book) -> dict[str, Any]:
        progress = self.learning.load_progress(book.id)
        total = max(0, book.page_count)
        if progress is None:
            return {
                "current_page_id": "",
                "visited_pages": 0,
                "total_pages": total,
                "percent": 0,
            }
        visited = len(set(progress.visited_page_ids))
        return {
            "current_page_id": progress.current_page_id,
            "visited_pages": visited,
            "total_pages": total,
            "percent": round(min(100.0, visited * 100.0 / total)) if total else 0,
        }


def can_create_book() -> bool:
    if not _auth_enabled() or get_current_user().is_admin:
        return True
    return permission_for_user(get_current_user()).create


def resolve_book(book_id: str) -> ResolvedBook | None:
    """Bare IDs prefer local editing; explicit refs always bind the registry."""

    from deeptutor.book.engine import get_book_engine

    explicit = str(book_id).strip().startswith(registry.REF_PREFIX)
    book_id = registry.strip_ref(book_id)
    if not book_id:
        return None
    own_storage = get_book_storage()
    user = get_current_user()
    record = registry.get_book(book_id) if explicit else None
    own_location = str(own_storage.path_service.get_book_dir().resolve())
    local_target = not explicit or record is not None and record.base_dir == own_location
    if local_target and own_storage.book_exists(book_id):
        book = own_storage.load_book(book_id)
        if book is None:
            return None
        return ResolvedBook(
            engine=get_book_engine(),
            source="own",
            permission="edit",
            can_edit=True,
            can_delete=True,
            learning=own_storage,
            record=ensure_book_registered(book, own_storage),
        )
    record = record if explicit else registry.get_book(book_id)
    if record is None or not registry.can_read_book(record, user):
        return None
    return _located(record, user)


def _located(record: registry.BookRecord, user: CurrentUser) -> ResolvedBook | None:
    engine = engine_for(record)
    book = engine.load_book(record.book_id)
    if book is None:
        return None
    _retry_links(book, record)
    writable = registry.can_write_book(record, user)
    return ResolvedBook(
        engine=engine,
        source="shared",
        permission="edit" if writable else "read",
        can_edit=writable,
        can_delete=writable,
        learning=BookLearningOverlay(get_current_path_service()),
        record=record,
    )


def accessible_books() -> list[tuple[Book, ResolvedBook]]:
    """List local books and account-level public/transferred canonical books."""

    from deeptutor.book.engine import get_book_engine
    from deeptutor.services.workspace.context import current_workspace_id

    own_storage = get_book_storage()
    own_engine = get_book_engine()
    own_books = own_engine.list_books()
    records = registry.sync_book_dir(
        own_storage.path_service.get_book_dir(),
        {book.id: book.title or book.id for book in own_books},
        created_by=_owner_id(),
    )
    results: list[tuple[Book, ResolvedBook]] = []
    own_ids: set[str] = set()
    for book in own_books:
        record = records.get(book.id)
        if record is not None:
            _retry_links(book, record)
        own_ids.add(book.id)
        results.append(
            (
                book,
                ResolvedBook(
                    engine=own_engine,
                    source="own",
                    permission="edit",
                    can_edit=True,
                    can_delete=True,
                    learning=own_storage,
                    record=record,
                ),
            )
        )

    user = get_current_user()
    if current_workspace_id() == "":
        candidates = registry.list_book_records(visibility="public")
        for owner in sorted(_my_owner_ids(user)):
            candidates.extend(registry.list_transferred_books(owner))
        for record in candidates:
            if record.book_id in own_ids or not registry.can_read_book(record, user):
                continue
            own_ids.add(record.book_id)
            resolved = _located(record, user)
            if resolved is not None:
                book = resolved.engine.load_book(record.book_id)
                if book is not None:
                    results.append((book, resolved))
    results.sort(key=lambda item: item[0].updated_at, reverse=True)
    return results


def readable_book_storage(book_id: str) -> BookStorage | None:
    """Resolve the requested identity before stripping its canonical prefix."""
    resolved = resolve_book(book_id)
    return resolved.engine.storage if resolved is not None else None


@contextmanager
def book_reading_scope(book_references: Any) -> Iterator[list[str]]:
    """Open bound books for this turn and yield only usable fixed KB refs.

    A local collision has no registry identity. Raw/pending KB names are never
    resolved in the reader's namespace; the D2 reader rechecks permission on
    every subsequent retrieval.
    """
    from fastapi import HTTPException

    from deeptutor.book.context import normalize_book_references
    from deeptutor.knowledge import registry as kb_registry
    from deeptutor.multi_user.knowledge_access import resolve_kb

    records = []
    for ref in normalize_book_references(book_references):
        resolved = resolve_book(ref.book_id)
        if resolved is not None and resolved.record is not None:
            records.append(resolved.record)
    with registry.reading_book(*(record.ref for record in records)):
        usable: list[str] = []
        for record in records:
            if not registry.book_kb_reader(record.book_id, get_current_user().id):
                continue
            for kb_id in registry.book_kb_links(record.book_id).values():
                kb = kb_registry.get_kb(kb_id) if kb_id else None
                if kb is None or kb_registry.is_connector(kb) or kb.ref in usable:
                    continue
                try:
                    resolve_kb(kb.ref)
                except (HTTPException, ValueError):
                    continue
                usable.append(kb.ref)
        yield usable


def public_book_catalog() -> list[dict[str, Any]]:
    """Manifest summaries of public canonical books, across all owners."""
    rows = []
    for record in registry.list_book_records(visibility="public"):
        book = engine_for(record).load_book(record.book_id)
        if book is None:
            continue
        rows.append({
            "book_id": book.id,
            "title": book.title,
            "status": book.status.value,
            "updated_at": book.updated_at,
        })
    return rows


__all__ = [
    "ResolvedBook",
    "accessible_books",
    "public_book_catalog",
    "book_reading_scope",
    "can_create_book",
    "engine_for",
    "ensure_book_registered",
    "prepare_new_book",
    "readable_book_storage",
    "register_current_scope_book",
    "resolve_book",
    "storage_for",
]
