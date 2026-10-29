"""Deployment-wide book identities, references and access.

Books stay in their original directories; the identity is the existing Book.id.
Storage primitives take trusted identities and locations; sharing entry points
also check the current actor. Connection-taking helpers compose within one
immediate transaction. Changes affecting sharing must use sharing.shared_write
and sync_protection, never nested independent registry writers.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import importlib
import logging
from pathlib import Path
import sqlite3
import threading
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from deeptutor.knowledge.registry import KbRecord
    from deeptutor.multi_user.models import CurrentUser

logger = logging.getLogger(__name__)

Visibility = Literal["public", "private"]
BookRevocationReason = Literal["made_private", "book_deleted"]
VISIBILITIES: frozenset[str] = frozenset({"public", "private"})
REF_PREFIX = "book:"
DIR_PREFIX = "book_"
REGISTRY_FILENAME = "book_registry.sqlite3"
DB_PATH_OVERRIDE: Path | None = None
KB_REFERRER_KIND = "book"
#: Referrers whose withdrawal needs the owner's explicit confirmation (rule
#: R1): published teacher paths. Others are revoked and shown as unavailable.
CONFIRM_REFERRER_KINDS: frozenset[str] = frozenset({"mastery_template"})
KbChoice = Literal["make_public", "reference_only"]
KB_CHOICES: frozenset[str] = frozenset({"make_public", "reference_only"})

# Cached modules may expose register_book_readers() to restore a reset reader
# map. Hooks run with setdefault semantics, preserving explicit custom readers.
BUILTIN_BOOK_READER_MODULES: tuple[str, ...] = ("deeptutor.teaching.access",)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS book_registry (
    book_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    base_dir TEXT NOT NULL,
    dir_name TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility IN ('public', 'private')),
    created_by TEXT NOT NULL,
    transferred_from TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (base_dir, dir_name)
);
CREATE TABLE IF NOT EXISTS book_references (
    book_id TEXT NOT NULL REFERENCES book_registry(book_id) ON DELETE CASCADE,
    referrer_kind TEXT NOT NULL,
    referrer_id TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (book_id, referrer_kind, referrer_id)
);
CREATE INDEX IF NOT EXISTS book_references_by_referrer
    ON book_references (referrer_kind, referrer_id);
-- No foreign key: unavailable-material evidence survives book deletion.
CREATE TABLE IF NOT EXISTS book_revoked_references (
    book_id TEXT NOT NULL,
    book_title TEXT NOT NULL,
    referrer_kind TEXT NOT NULL,
    referrer_id TEXT NOT NULL,
    created_by TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (reason IN ('made_private', 'book_deleted')),
    revoked_at TEXT NOT NULL,
    PRIMARY KEY (book_id, referrer_kind, referrer_id)
);
CREATE INDEX IF NOT EXISTS book_revoked_by_referrer
    ON book_revoked_references (referrer_kind, referrer_id);
CREATE TABLE IF NOT EXISTS book_kb_links (
    book_id TEXT NOT NULL REFERENCES book_registry(book_id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    ref TEXT NOT NULL,
    kb_id TEXT NOT NULL,
    PRIMARY KEY (book_id, ref)
);
CREATE TABLE IF NOT EXISTS registry_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS book_kb_binding_state (
    book_id TEXT PRIMARY KEY REFERENCES book_registry(book_id) ON DELETE CASCADE,
    complete INTEGER NOT NULL CHECK (complete IN (0, 1))
);
CREATE TABLE IF NOT EXISTS book_kb_pending (
    book_id TEXT NOT NULL REFERENCES book_registry(book_id) ON DELETE CASCADE,
    ref TEXT NOT NULL,
    PRIMARY KEY (book_id, ref)
);
"""

_write_lock = threading.RLock()
_writer_state = threading.local()
_reader_lock = threading.RLock()
_builtin_readers_loaded = False
_builtin_reader_state = threading.local()
_readers: dict[str, Callable[[str, str], bool]] = {}


class BookRegistryError(ValueError):
    """Invalid or conflicting book registration."""


class BookVisibilityError(BookRegistryError):
    """Invalid or refused visibility change."""


class BookReferenceError(BookRegistryError):
    """Invalid or unavailable reference target."""


class BookWithdrawalNeedsConfirmation(BookVisibilityError):
    """Making private / deleting would cut off published teacher paths (rule R1).

    Raised before anything is written; repeat with ``confirm=True``.
    ``referrer_ids`` are the affected template ids, for aggregate counts only.
    """

    def __init__(self, referrer_ids: Sequence[str]) -> None:
        self.referrer_ids = tuple(sorted(set(referrer_ids)))
        super().__init__(
            f"Withdrawing this book affects {len(self.referrer_ids)} teacher path(s); "
            "confirm to proceed."
        )


def require_withdrawal_confirmation(
    references: Sequence[BookReference], *, confirm: bool,
    confirmed_referrers: Collection[str] | None = None,
) -> None:
    """Refuse a withdrawal that would revoke teacher-path references not confirmed.

    ``confirmed_referrers`` binds ``confirm`` to the paths it was given for.
    """
    blocking = {ref.referrer_id for ref in references if ref.referrer_kind in CONFIRM_REFERRER_KINDS}
    if not blocking:
        return
    if confirm and (confirmed_referrers is None or blocking <= set(confirmed_referrers)):
        return
    raise BookWithdrawalNeedsConfirmation(sorted(blocking))


class BookKbResolutionError(BookRegistryError):
    """Binding is incomplete; retry in the canonical owner's storage scope."""


@dataclass(frozen=True)
class BookRecord:
    book_id: str
    title: str
    base_dir: str
    dir_name: str
    visibility: Visibility
    created_by: str
    created_at: str
    updated_at: str

    @property
    def ref(self) -> str:
        return f"{REF_PREFIX}{self.book_id}"


@dataclass(frozen=True)
class BookReference:
    book_id: str
    referrer_kind: str
    referrer_id: str
    created_by: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class BookKbIssue:
    ref: str
    kb_id: str
    name: str
    code: Literal["connector", "foreign_private"]
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BookKbPlan:
    book_id: str
    public_kbs: tuple[KbRecord, ...]
    private_kbs: tuple[KbRecord, ...]
    referenced_kb_ids: frozenset[str]
    issues: tuple[BookKbIssue, ...]
    missing: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "public_kbs": [{"kb_id": kb.kb_id, "name": kb.name} for kb in self.public_kbs],
            "private_kbs": [
                {"kb_id": kb.kb_id, "name": kb.name,
                 "default": "reference_only" if kb.kb_id in self.referenced_kb_ids else "make_public"}
                for kb in self.private_kbs
            ],
            "issues": [issue.to_dict() for issue in self.issues],
            "missing": list(self.missing),
        }


@dataclass(frozen=True)
class RevokedBookReference:
    book_id: str
    book_title: str
    referrer_kind: str
    referrer_id: str
    created_by: str
    reason: BookRevocationReason
    revoked_at: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _db_path() -> Path:
    if DB_PATH_OVERRIDE is not None:
        return DB_PATH_OVERRIDE
    from deeptutor.multi_user import paths

    return paths.SYSTEM_ROOT / REGISTRY_FILENAME


def _open(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro" if readonly else path,
        uri=readonly, timeout=30, isolation_level=None,
    )
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if not readonly:
            conn.executescript(_SCHEMA)
        return conn
    except BaseException:
        conn.close()
        raise


@contextmanager
def _read() -> Iterator[sqlite3.Connection | None]:
    """Never create a database or run schema writes from a read."""
    path = _db_path()
    if not path.exists():
        yield None
        return
    conn = _open(path, readonly=True)
    try:
        # Keep authorization's record and references in the same snapshot.
        conn.execute("BEGIN")
        yield conn
    finally:
        conn.close()


@contextmanager
def _write() -> Iterator[sqlite3.Connection]:
    """Lock before checks, including across processes; rollback on cancellation.

    Use connection-taking helpers inside this boundary. A second writer would
    wait on our own SQLite lock, so reject it before opening a connection.
    """
    if getattr(_writer_state, "active", False):
        raise BookRegistryError("Use connection-taking helpers instead of nested book writers.")
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock:
        conn = _open(path)
        _writer_state.active = True
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        finally:
            _writer_state.active = False
            conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _location(base_dir: Path | str) -> str:
    return str(Path(base_dir).expanduser().resolve())


def strip_ref(value: str) -> str:
    text = str(value or "").strip()
    return text[len(REF_PREFIX):] if text.startswith(REF_PREFIX) else text


def _validate_id(book_id: str) -> str:
    if (
            not book_id or book_id != book_id.strip() or book_id in {".", ".."}
            or any(char in book_id for char in ("/", "\\", "\0", ":"))
    ):
        raise BookRegistryError("Book id must be a nonempty, unambiguous directory component.")
    return book_id


def dir_name_for(book_id: str) -> str:
    return f"{DIR_PREFIX}{_validate_id(strip_ref(book_id))}"


def _book_id_of(dir_name: str) -> str:
    if not dir_name.startswith(DIR_PREFIX):
        raise BookRegistryError(f"{dir_name!r} is not a book directory (expected 'book_<id>').")
    return _validate_id(dir_name[len(DIR_PREFIX):])


def _record(row: sqlite3.Row) -> BookRecord:
    return BookRecord(**{name: row[name] for name in BookRecord.__dataclass_fields__})


def _reference(row: sqlite3.Row) -> BookReference:
    return BookReference(**{name: row[name] for name in BookReference.__dataclass_fields__})


def _revoked(row: sqlite3.Row) -> RevokedBookReference:
    return RevokedBookReference(**dict(row))


def _get_book(conn: sqlite3.Connection, book_id: str) -> BookRecord | None:
    row = conn.execute(
        "SELECT * FROM book_registry WHERE book_id = ?", (strip_ref(book_id),),
    ).fetchone()
    return _record(row) if row is not None else None


def _target(conn: sqlite3.Connection, book_id: str) -> BookRecord:
    record = _get_book(conn, book_id)
    if record is None:
        raise BookReferenceError(f"Book {book_id} is not registered.")
    return record


def _find_by_location(
        conn: sqlite3.Connection, base_dir: Path | str, dir_name: str,
) -> BookRecord | None:
    row = conn.execute(
        "SELECT * FROM book_registry WHERE base_dir = ? AND dir_name = ?",
        (_location(base_dir), dir_name),
    ).fetchone()
    return _record(row) if row is not None else None


def _register(
        conn: sqlite3.Connection, *, base_dir: Path | str, dir_name: str, title: str,
        created_by: str, visibility: str = "private",
) -> BookRecord:
    if visibility not in VISIBILITIES:
        raise BookVisibilityError("Visibility must be 'public' or 'private'.")
    book_id = _book_id_of(dir_name)
    location = _location(base_dir)
    existing = _get_book(conn, book_id)
    if existing is not None:
        if (existing.base_dir, existing.dir_name) != (location, dir_name):
            raise BookRegistryError(f"Book {book_id} is already registered at another location.")
        return existing
    now = _now()
    conn.execute(
        "INSERT INTO book_registry (book_id, title, base_dir, dir_name, visibility, "
        "created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (book_id, title or book_id, location, dir_name, visibility, created_by, now, now),
    )
    return _target(conn, book_id)


def register_book(
        *, base_dir: Path | str, dir_name: str, title: str, created_by: str,
        visibility: str = "private",
) -> BookRecord:
    """Idempotent at one location; never adopt a foreign global identity."""
    if visibility not in VISIBILITIES:
        raise BookVisibilityError("Visibility must be 'public' or 'private'.")
    _book_id_of(dir_name)
    with _write() as conn:
        return _register(
            conn, base_dir=base_dir, dir_name=dir_name, title=title,
            created_by=created_by, visibility=visibility,
        )


def get_book(book_id: str) -> BookRecord | None:
    with _read() as conn:
        return None if conn is None else _get_book(conn, book_id)


def resolve_book_ref(ref: str) -> BookRecord | None:
    """Look up canonical refs; lazily register unknown bare IDs in this scope.

    Registry first: a bare id resolves to the registered identity even when
    the caller's own scope holds a local book with the same id (no local
    collision check). Callers needing local-first semantics use
    ``multi_user.book_access.resolve_book`` or ``teaching.books.resolve``.

    This does not authorize the returned record. In particular, a local
    collision's edit permission never confers authority on this identity.
    """
    book_id = strip_ref(ref)
    if not book_id:
        return None
    record = get_book(book_id)
    if record is not None or str(ref).strip().startswith(REF_PREFIX):
        return record
    from deeptutor.multi_user.book_access import register_current_scope_book

    return register_current_scope_book(book_id)


def register_new_book(
    *, base_dir: Path | str, dir_name: str, title: str, created_by: str,
) -> BookRecord:
    """Reserve a genuinely new private identity and its intent atomically.

    Existing identities are never adopted, including at the same location.
    Legacy registration and repairs must continue to use register_book.
    """
    from deeptutor.core.visibility_intent import record_choice

    book_id = _book_id_of(dir_name)
    with _write() as conn:
        if _get_book(conn, book_id) is not None:
            raise BookRegistryError(f"Book {book_id} already has a canonical identity.")
        record = _register(
            conn, base_dir=base_dir, dir_name=dir_name, title=title, created_by=created_by,
        )
        record_choice(conn, book_id, "private", schema="main")
        return record


def find_book_by_location(base_dir: Path | str, dir_name: str) -> BookRecord | None:
    with _read() as conn:
        return None if conn is None else _find_by_location(conn, base_dir, dir_name)


def list_book_records(
        *, visibility: str | None = None, created_by: str | None = None,
        base_dir: Path | str | None = None,
) -> list[BookRecord]:
    clauses: list[str] = []
    params: list[str] = []
    for column, value in (
            ("visibility", visibility), ("created_by", created_by),
            ("base_dir", _location(base_dir) if base_dir is not None else None),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with _read() as conn:
        if conn is None:
            return []
        rows = conn.execute(
            f"SELECT * FROM book_registry{where} ORDER BY title, book_id",  # nosec B608 - fixed columns
            params,
        ).fetchall()
        return [_record(row) for row in rows]


def sync_book_dir(
        base_dir: Path | str, titles: Mapping[str, str], *, created_by: str,
) -> dict[str, BookRecord]:
    """Sync trusted canonical storage; collision copies keep their local IDs."""
    if not titles:
        return {}
    for book_id in titles:
        _validate_id(book_id)
    known = {r.book_id: r for r in list_book_records(base_dir=base_dir)}
    if all(
            book_id in known and (not title or title == known[book_id].title)
            for book_id, title in titles.items()
    ):
        return {book_id: known[book_id] for book_id in titles}
    records: dict[str, BookRecord] = {}
    with _write() as conn:
        for book_id, title in titles.items():
            try:
                record = _register(
                    conn, base_dir=base_dir, dir_name=dir_name_for(book_id),
                    title=title, created_by=created_by,
                )
            except BookRegistryError:
                logger.warning("Book %s in %s is registered at another location; skipped.", book_id, base_dir)
                continue
            if title and title != record.title:
                conn.execute(
                    "UPDATE book_registry SET title = ?, updated_at = ? WHERE book_id = ?",
                    (title, _now(), book_id),
                )
            records[book_id] = _target(conn, book_id)
    return records


def _references_of(conn: sqlite3.Connection, book_id: str) -> list[BookReference]:
    rows = conn.execute(
        "SELECT * FROM book_references WHERE book_id = ? ORDER BY referrer_kind, referrer_id",
        (strip_ref(book_id),),
    ).fetchall()
    return [_reference(row) for row in rows]


def _clear_referrer(conn: sqlite3.Connection, referrer_kind: str, referrer_id: str) -> None:
    for table in ("book_references", "book_revoked_references"):
        conn.execute(
            f"DELETE FROM {table} WHERE referrer_kind = ? AND referrer_id = ?",  # nosec B608 - fixed tables
            (referrer_kind, referrer_id),
        )


def _replace_references(
        conn: sqlite3.Connection, referrer_kind: str, referrer_id: str,
        book_ids: Sequence[str], *, created_by: str,
) -> None:
    targets = dict.fromkeys(_target(conn, book_id).book_id for book_id in book_ids)
    _clear_referrer(conn, referrer_kind, referrer_id)
    now = _now()
    conn.executemany(
        "INSERT INTO book_references (book_id, referrer_kind, referrer_id, created_by, "
        "created_at) VALUES (?, ?, ?, ?, ?)",
        [(book_id, referrer_kind, referrer_id, created_by, now) for book_id in targets],
    )


def replace_book_references(
        referrer_kind: str, referrer_id: str, book_ids: Sequence[str], *, created_by: str,
        private_intent_book_ids: Sequence[str] = (),
        restore_book_ids: Sequence[str] = (),
        restorable: Callable[[BookRecord], bool] | None = None,
) -> None:
    """Replace book and KB protection together; failure retains the entire old set.

    ``private_intent_book_ids`` records explicit share-by-reference choices for
    writable private targets in the same transaction (see sharing).
    ``restore_book_ids`` / ``restorable`` re-admit released books only after
    a fresh in-transaction check (see sharing).
    """
    from .sharing import replace_references

    replace_references(
        referrer_kind, referrer_id, book_ids, created_by=created_by,
        private_intent_book_ids=private_intent_book_ids,
        restore_book_ids=restore_book_ids, restorable=restorable,
    )


def remove_book_references(referrer_kind: str, referrer_id: str) -> None:
    from .sharing import remove_references

    remove_references(referrer_kind, referrer_id)


def list_book_references(book_id: str) -> list[BookReference]:
    with _read() as conn:
        return [] if conn is None else _references_of(conn, book_id)


def list_referenced_books(referrer_kind: str, referrer_id: str) -> list[BookRecord]:
    with _read() as conn:
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT b.* FROM book_registry b JOIN book_references r ON r.book_id = b.book_id "
            "WHERE r.referrer_kind = ? AND r.referrer_id = ? ORDER BY b.title, b.book_id",
            (referrer_kind, referrer_id),
        ).fetchall()
        return [_record(row) for row in rows]


def _revoke(
        conn: sqlite3.Connection, record: BookRecord, references: Sequence[BookReference],
        *, reason: BookRevocationReason,
) -> list[RevokedBookReference]:
    """Use a freshly read target/references from the caller's write transaction."""
    if reason not in ("made_private", "book_deleted"):
        raise BookReferenceError(f"Invalid book revocation reason: {reason}")
    if any(ref.book_id != record.book_id for ref in references):
        raise BookReferenceError("Revocation references must belong to the target book.")
    now = _now()
    revoked = [
        RevokedBookReference(
            record.book_id, record.title, ref.referrer_kind, ref.referrer_id,
            ref.created_by, reason, now,
        )
        for ref in references
    ]
    for ref in revoked:
        conn.execute(
            "INSERT OR REPLACE INTO book_revoked_references "
            "(book_id, book_title, referrer_kind, referrer_id, created_by, reason, revoked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ref.book_id, ref.book_title, ref.referrer_kind, ref.referrer_id,
             ref.created_by, ref.reason, ref.revoked_at),
        )
        conn.execute(
            "DELETE FROM book_references WHERE book_id = ? AND referrer_kind = ? AND referrer_id = ?",
            (ref.book_id, ref.referrer_kind, ref.referrer_id),
        )
    return revoked


def _revoke_references(
        book_id: str, *, reason: BookRevocationReason, keep_created_by: str | None = None,
) -> list[RevokedBookReference]:
    with _write() as conn:
        record = _get_book(conn, book_id)
        if record is None:
            return []
        refs = [ref for ref in _references_of(conn, book_id) if ref.created_by != keep_created_by]
        return _revoke(conn, record, refs, reason=reason)


def list_unavailable_book_references(
        referrer_kind: str, referrer_id: str,
) -> list[RevokedBookReference]:
    with _read() as conn:
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT * FROM book_revoked_references WHERE referrer_kind = ? AND referrer_id = ? "
            "ORDER BY book_id", (referrer_kind, referrer_id),
        ).fetchall()
        return [_revoked(row) for row in rows]


def list_revoked_book_references(book_id: str) -> list[RevokedBookReference]:
    with _read() as conn:
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT * FROM book_revoked_references WHERE book_id = ? ORDER BY referrer_kind, referrer_id",
            (strip_ref(book_id),),
        ).fetchall()
        return [_revoked(row) for row in rows]


def _book_registry_schema(conn: sqlite3.Connection) -> Literal["main", "books"]:
    """Return the schema name (``main`` or ``books``) that holds ``book_registry``."""
    rows = conn.execute(
        "SELECT name FROM pragma_database_list WHERE name = 'books'"
    ).fetchall()
    return "books" if rows else "main"


def _unregister(conn: sqlite3.Connection, book_id: str) -> None:
    # Revocation, row deletion and intent removal all run in the caller's one
    # write transaction, so they commit (or roll back) atomically together.
    record = _get_book(conn, book_id)
    if record is not None:
        from deeptutor.core.visibility_intent import remove_choice

        _revoke(conn, record, _references_of(conn, book_id), reason="book_deleted")
        conn.execute("DELETE FROM book_registry WHERE book_id = ?", (record.book_id,))
        # Clean up the visibility intent stored beside the book_registry rows.
        # Under shared_write the book DB is attached as "books"; under _write()
        # it is the main connection. Detect which schema holds our table.
        _schema = _book_registry_schema(conn)
        remove_choice(conn, book_id, schema=_schema)


def unregister_book(book_id: str) -> None:
    """Revoke, unregister and release KBs together; services guard physical deletion."""
    from .sharing import unregister

    unregister(book_id)


def _relocate_book(conn: sqlite3.Connection, book_id: str, base_dir: Path | str) -> None:
    """Relocate a checked identity inside the caller's metadata transaction."""
    conn.execute(
        "UPDATE book_registry SET base_dir = ?, updated_at = ? WHERE book_id = ?",
        (_location(base_dir), _now(), book_id),
    )


def relocate_book_dir(old_base_dir: Path | str, new_base_dir: Path | str) -> int:
    """Update metadata after an explicitly requested move; never move book files."""
    try:
        with _write() as conn:
            return conn.execute(
                "UPDATE book_registry SET base_dir = ?, updated_at = ? WHERE base_dir = ?",
                (_location(new_base_dir), _now(), _location(old_base_dir)),
            ).rowcount
    except sqlite3.IntegrityError as exc:
        raise BookRegistryError("The destination already has registered books.") from exc


def transfer_book_ownership(from_user_id: str, to_user_id: str) -> list[BookRecord]:
    """Transfer books together with their existing KB reference ownership."""
    from .sharing import transfer_ownership

    return transfer_ownership(from_user_id, to_user_id)


def list_transferred_books(user_id: str) -> list[BookRecord]:
    with _read() as conn:
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT * FROM book_registry WHERE created_by = ? AND transferred_from != '' "
            "ORDER BY title, book_id", (user_id,),
        ).fetchall()
        return [_record(row) for row in rows]


def register_book_reference_reader(referrer_kind: str, reader: Callable[[str, str], bool]) -> None:
    """Register reader(referrer_id, user_id); explicit registrations take precedence.

    While this thread runs a builtin hook (see :func:`_load_builtin_readers`)
    the registration is staged and later merged with ``setdefault`` semantics.
    """
    pending = getattr(_builtin_reader_state, "pending", None)
    if pending is not None:
        pending.setdefault(referrer_kind, reader)
        return
    with _reader_lock:
        _readers[referrer_kind] = reader


def _load_builtin_readers() -> None:
    """Import cached builtin modules and run their hooks until all succeed once.

    Mirrors plan B's loader contract (D2, ``knowledge.registry``): no
    reader-map lock is held across an import or a hook, so a module whose
    import registers eagerly can never deadlock against a concurrent loader.
    Each hook's registrations are staged on this thread and merged with
    ``setdefault`` only after it succeeds, so a failing hook grants nothing and
    a concurrent explicit registration always keeps precedence. The loaded
    flag is set only when every hook succeeded; a failure is retried on the
    next read. Concurrent loaders may repeat idempotent hooks. Re-entrant
    calls (a hook that reads books) return at once.
    """
    global _builtin_readers_loaded
    if getattr(_builtin_reader_state, "pending", None) is not None:
        return
    with _reader_lock:
        if _builtin_readers_loaded:
            return
    complete = True
    for name in BUILTIN_BOOK_READER_MODULES:
        pending: dict[str, Callable[[str, str], bool]] = {}
        _builtin_reader_state.pending = pending
        try:
            module = importlib.import_module(name)
            register = getattr(module, "register_book_readers", None)
            if callable(register):
                register()
        except Exception:
            complete = False
            logger.exception("Could not load book reference readers from %s", name)
        else:
            with _reader_lock:
                for kind, reader in pending.items():
                    _readers.setdefault(kind, reader)
        finally:
            _builtin_reader_state.pending = None
    if complete:
        with _reader_lock:
            _builtin_readers_loaded = True


def _bound_record(conn: sqlite3.Connection, record: BookRecord) -> BookRecord | None:
    current = _get_book(conn, record.book_id)
    if current is None or (current.base_dir, current.dir_name) != (record.base_dir, record.dir_name):
        return None
    return current


def _read_decision(
    conn: sqlite3.Connection, record: BookRecord, user: CurrentUser,
) -> bool | list[BookReference]:
    """A final decision, or the references still to consult with readers."""
    current = _bound_record(conn, record)
    if current is None:
        return False
    from deeptutor.multi_user.ownership import owns

    if owns(current.created_by, user):
        return True
    if current.visibility == "public":
        from deeptutor.publishing.scope import BOOK, allows

        # Spec 2026-10-29 §7.1: a published book reaches its scope only.
        if allows(BOOK, current.book_id, user.id):
            return True
    return _references_of(conn, current.book_id)


def _readers_grant(references: Sequence[BookReference], user: CurrentUser) -> bool:
    _load_builtin_readers()
    for reference in references:
        with _reader_lock:
            reader = _readers.get(reference.referrer_kind)
        if reader is not None:
            try:
                if reader(reference.referrer_id, user.id):
                    return True
            except Exception:
                logger.warning("Book reference reader for %s failed", reference.referrer_kind, exc_info=True)
    return False


def _settle(decision: bool | list[BookReference], user: CurrentUser) -> bool:
    return decision if isinstance(decision, bool) else _readers_grant(decision, user)


def _can_read(conn: sqlite3.Connection, record: BookRecord, user: CurrentUser) -> bool:
    """For callers already inside a registry transaction (e.g. shared_write)."""
    return _settle(_read_decision(conn, record, user), user)


def can_read_book(record: BookRecord, user: CurrentUser) -> bool:
    # Readers consult other stores (teaching): call them after the book
    # snapshot closes, keeping the global teaching -> KB -> Book lock order.
    with _read() as conn:
        decision = False if conn is None else _read_decision(conn, record, user)
    return _settle(decision, user)


def _can_write(conn: sqlite3.Connection, record: BookRecord, user: CurrentUser) -> bool:
    from deeptutor.multi_user.ownership import owns

    from deeptutor.publishing.admin import retiring

    current = _bound_record(conn, record)
    # D10: administrators only withdraw or delete published books they do not own.
    return current is not None and (
        owns(current.created_by, user)
        or (user.is_admin and current.visibility == "public" and retiring())
    )


def can_write_book(record: BookRecord, user: CurrentUser) -> bool:
    with _read() as conn:
        return conn is not None and _can_write(conn, record, user)


@dataclass(frozen=True)
class BookAccess:
    readable: bool
    writable: bool
    links_initialized: bool


def book_access_batch(records: Sequence[BookRecord], user: CurrentUser) -> list[BookAccess]:
    """Read/write/link-binding decisions for many records in one read snapshot.

    Same rules as :func:`can_read_book`, :func:`can_write_book` and
    :func:`kb_links_initialized`, aligned with ``records``. Reference readers
    (only reached for private, non-owned records) run after the snapshot
    closes, keeping the teaching -> KB -> Book lock order.
    """
    with _read() as conn:
        if conn is None:
            return [BookAccess(False, False, False) for _ in records]
        gathered = [
            (_read_decision(conn, record, user), _can_write(conn, record, user),
             _links_initialized(conn, record.book_id))
            for record in records
        ]
    return [
        BookAccess(_settle(decision, user), writable, initialized)
        for decision, writable, initialized in gathered
    ]


def _set_kb_links(conn: sqlite3.Connection, book_id: str, links: Mapping[str, str]) -> None:
    record = _target(conn, book_id)
    conn.execute("DELETE FROM book_kb_links WHERE book_id = ?", (record.book_id,))
    conn.executemany(
        "INSERT INTO book_kb_links (book_id, position, ref, kb_id) VALUES (?, ?, ?, ?)",
        [(record.book_id, index, ref, kb_id) for index, (ref, kb_id) in enumerate(links.items())],
    )


def set_book_kb_links(book_id: str, links: Mapping[str, str]) -> None:
    """Store fixed KB identities, including explicitly unavailable empty values."""
    from .sharing import shared_write, sync_protection

    with shared_write() as conn:
        _set_kb_links(conn, book_id, links)
        conn.execute("DELETE FROM book_kb_pending WHERE book_id = ?", (strip_ref(book_id),))
        conn.execute(
            "INSERT OR REPLACE INTO book_kb_binding_state VALUES (?, 1)", (strip_ref(book_id),)
        )
        sync_protection(conn, strip_ref(book_id))


def book_kb_links(book_id: str) -> dict[str, str]:
    with _read() as conn:
        if conn is None:
            return {}
        rows = conn.execute(
            "SELECT ref, kb_id FROM book_kb_links WHERE book_id = ? ORDER BY position",
            (strip_ref(book_id),),
        ).fetchall()
        return {row["ref"]: row["kb_id"] for row in rows}


def has_kb_links(book_id: str) -> bool:
    """Whether rows exist, not whether binding/migration has completed."""
    return bool(book_kb_links(book_id))


def kb_refs_for(book_id: str, refs: Sequence[str]) -> list[str]:
    links = book_kb_links(book_id)
    return [f"kb:{links[ref]}" if links.get(ref) else ref for ref in refs]


def get_meta(key: str) -> str | None:
    with _read() as conn:
        if conn is None:
            return None
        row = conn.execute("SELECT value FROM registry_meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else row["value"]


def set_meta(key: str, value: str) -> None:
    with _write() as conn:
        conn.execute(
            "INSERT INTO registry_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value),
        )


def resolve_kb_links(refs: Sequence[str]) -> dict[str, str]:
    """Resolve in the current scope; transient errors never become missing bindings."""
    from deeptutor.knowledge import registry as kb_registry

    links = {}
    for ref in dict.fromkeys(str(value) for value in refs if value):
        try:
            kb = kb_registry.resolve_ref(ref)
        except Exception as exc:
            raise BookKbResolutionError(f"Knowledge base {ref!r} needs resolution retry.") from exc
        links[ref] = kb.kb_id if kb is not None else ""
    return links


def initialize_book_kb_links(
    book_id: str, refs: Sequence[str], *, confirmed_missing: Sequence[str] = (),
) -> bool:
    """Retry pending links in the canonical owner's scope, never rebind fixed IDs.

    An unresolved legacy name may precede KB backfill; only the caller with
    authoritative missing-material evidence may mark it confirmed_missing.
    Returns completion, distinct from has_kb_links (which only checks rows).
    """
    from .sharing import initialize_links

    return initialize_links(book_id, refs, confirmed_missing=confirmed_missing)


def _links_initialized(conn: sqlite3.Connection, book_id: str) -> bool:
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = 'book_kb_binding_state' AND type = 'table'"
    ).fetchone() is None:
        return False
    row = conn.execute(
        "SELECT complete FROM book_kb_binding_state WHERE book_id = ?", (strip_ref(book_id),)
    ).fetchone()
    return row is not None and bool(row["complete"])


def kb_links_initialized(book_id: str) -> bool:
    with _read() as conn:
        return conn is not None and _links_initialized(conn, book_id)


def plan_book_kbs(book_id: str) -> BookKbPlan:
    from .sharing import plan

    return plan(book_id)


def share_book_kbs(
    book_id: str, kb_choices: Mapping[str, str] | None = None, *, force: bool = False,
) -> BookKbPlan:
    """Prepare choices; private unreferenced books never retain KB protection."""
    from .sharing import share

    return share(book_id, kb_choices, force=force)


def set_book_visibility(
    book_id: str, visibility: str, *, force: bool = False,
    kb_choices: Mapping[str, str] | None = None, confirm: bool = False,
    confirmed_referrers: Collection[str] | None = None,
) -> BookRecord:
    from .sharing import set_visibility

    return set_visibility(
        book_id, visibility, force=force, kb_choices=kb_choices, confirm=confirm,
        confirmed_referrers=confirmed_referrers,
    )


active_books: ContextVar[frozenset[str]] = ContextVar("active_books", default=frozenset())
_reading_bindings: ContextVar[tuple[tuple[str, BookRecord], ...]] = ContextVar(
    "book_reading_bindings", default=(),
)


def reading_bindings(*book_ids: str) -> tuple[tuple[str, BookRecord], ...]:
    """The (actor, record) bindings ``reading_book`` would open for *book_ids*.

    Canonical registry identities the current user can read right now, never
    a same-id local storage copy. Storage-bound.
    """
    from deeptutor.multi_user.context import get_current_user

    user = get_current_user()
    bindings = []
    for value in book_ids:
        record = get_book(value)
        if record is not None and can_read_book(record, user):
            bindings.append((user.id, record))
    return tuple(bindings)


@contextmanager
def reading_book(*book_ids: str) -> Iterator[None]:
    """Authorize canonical registry identities, never a same-id local storage copy.

    Consumers must load the registered location, not their current storage.
    Bindings also pin actor and location so inherited or stale contexts cannot
    activate another account's authorization or a relocated/recreated book.
    """
    bindings = reading_bindings(*book_ids)
    token = active_books.set(active_books.get() | frozenset(r.book_id for _, r in bindings))
    bound_token = _reading_bindings.set(_reading_bindings.get() + bindings)
    try:
        yield
    finally:
        _reading_bindings.reset(bound_token)
        active_books.reset(token)


def bind_reading(bindings: tuple[tuple[str, BookRecord], ...]) -> tuple[str, ...]:
    """Open precomputed :func:`reading_bindings` for the rest of the current context.

    For a request-scoped dependency that cannot wrap its endpoint in
    :func:`reading_book` (one HTTP request): the values live in the request
    task's own context and end with it. ``book_kb_reader`` re-checks every
    binding (actor, identity, readability) on each use.
    """
    active_books.set(active_books.get() | frozenset(r.book_id for _, r in bindings))
    _reading_bindings.set(_reading_bindings.get() + tuple(bindings))
    return tuple(record.book_id for _, record in bindings)


class BookReadingScope:
    """A turn's mutable set of open books.

    A mastery turn may switch paths in a task or worker thread running a
    copied context, where re-setting a context variable would not reach the
    rest of the turn. Every task of the turn shares this object instead, and
    a switch re-points it. Nested scopes add to their parent's books.
    """

    def __init__(self, parent: BookReadingScope | None = None) -> None:
        self.parent = parent
        self.bindings: tuple[tuple[str, BookRecord], ...] = ()

    def bind(self, bindings: tuple[tuple[str, BookRecord], ...]) -> None:
        """Replace the open books with precomputed :func:`reading_bindings`."""
        self.bindings = tuple(bindings)

    def clear(self) -> None:
        self.bindings = ()

    def all_bindings(self) -> tuple[tuple[str, BookRecord], ...]:
        scope: BookReadingScope | None = self
        found: tuple[tuple[str, BookRecord], ...] = ()
        while scope is not None:
            found += scope.bindings
            scope = scope.parent
        return found


_turn_reading: ContextVar[BookReadingScope | None] = ContextVar("book_turn_reading", default=None)


@contextmanager
def turn_reading_scope() -> Iterator[BookReadingScope]:
    """Open an (initially empty) mutable reading scope for the rest of a turn."""
    scope = BookReadingScope(parent=_turn_reading.get())
    token = _turn_reading.set(scope)
    try:
        yield scope
    finally:
        _turn_reading.reset(token)


def _open_bindings() -> tuple[tuple[str, BookRecord], ...]:
    scope = _turn_reading.get()
    return _reading_bindings.get() + (scope.all_bindings() if scope is not None else ())


def book_kb_reader(book_id: str, user_id: str) -> bool:
    from deeptutor.multi_user.context import get_current_user

    user = get_current_user()
    if user.id != user_id:
        return False
    bindings = _open_bindings()
    scope = _turn_reading.get()
    turn_books = {r.book_id for _, r in scope.all_bindings()} if scope is not None else set()
    if book_id not in active_books.get() and book_id not in turn_books:
        return False
    current = get_book(book_id)
    if current is None:
        return False
    return any(
        actor == user_id and record.book_id == book_id
        and record.created_at == current.created_at and can_read_book(record, user)
        for actor, record in bindings
    )


def register_kb_reader() -> None:
    from deeptutor.knowledge import registry as kb_registry

    with kb_registry._reader_lock:
        if KB_REFERRER_KIND not in kb_registry._readers:
            kb_registry.register_reference_reader(KB_REFERRER_KIND, book_kb_reader)


register_kb_reader()
