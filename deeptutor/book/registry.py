"""Deployment-wide book identities, references and access.

Books stay in their original directories; the identity is the existing Book.id.
Mutation functions are trusted storage primitives: callers authorize the owner
and canonical location. Connection-taking helpers compose within one immediate
transaction. They do not coordinate the separate KB database; callers must
coordinate/recover that work without opening nested registry writers.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import importlib
import logging
from pathlib import Path
import sqlite3
import threading
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from deeptutor.multi_user.models import CurrentUser

logger = logging.getLogger(__name__)

Visibility = Literal["public", "private"]
BookRevocationReason = Literal["made_private", "book_deleted"]
VISIBILITIES: frozenset[str] = frozenset({"public", "private"})
REF_PREFIX = "book:"
DIR_PREFIX = "book_"
REGISTRY_FILENAME = "book_registry.sqlite3"
DB_PATH_OVERRIDE: Path | None = None

# Cached modules may expose register_book_readers() to restore a reset reader
# map. Hooks run with setdefault semantics, preserving explicit custom readers.
BUILTIN_BOOK_READER_MODULES: tuple[str, ...] = ()

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
"""

_write_lock = threading.RLock()
_writer_state = threading.local()
_reader_lock = threading.RLock()
_loading_builtin = False
_readers: dict[str, Callable[[str, str], bool]] = {}


class BookRegistryError(ValueError):
    """Invalid or conflicting book registration."""


class BookVisibilityError(BookRegistryError):
    """Invalid or refused visibility change."""


class BookReferenceError(BookRegistryError):
    """Invalid or unavailable reference target."""


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
    """Lookup only; callers must authorize the returned canonical record."""
    return get_book(ref)


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
) -> None:
    """Trusted whole-set replacement; unknown targets leave all prior evidence intact."""
    with _write() as conn:
        _replace_references(conn, referrer_kind, referrer_id, book_ids, created_by=created_by)


def remove_book_references(referrer_kind: str, referrer_id: str) -> None:
    with _write() as conn:
        _clear_referrer(conn, referrer_kind, referrer_id)


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


def _unregister(conn: sqlite3.Connection, book_id: str) -> None:
    record = _get_book(conn, book_id)
    if record is not None:
        _revoke(conn, record, _references_of(conn, book_id), reason="book_deleted")
        conn.execute("DELETE FROM book_registry WHERE book_id = ?", (record.book_id,))


def unregister_book(book_id: str) -> None:
    """Revoke and delete atomically; physical deletion guards belong to the service."""
    with _write() as conn:
        _unregister(conn, book_id)


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
    with _write() as conn:
        rows = conn.execute(
            "SELECT * FROM book_registry WHERE created_by = ? ORDER BY title, book_id",
            (from_user_id,),
        ).fetchall()
        if from_user_id != to_user_id:
            conn.execute(
                "UPDATE book_registry SET created_by = ?, transferred_from = ?, updated_at = ? "
                "WHERE created_by = ?", (to_user_id, from_user_id, _now(), from_user_id),
            )
        return [_target(conn, row["book_id"]) for row in rows]


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
    """Register reader(referrer_id, user_id); explicit registrations take precedence."""
    with _reader_lock:
        if _loading_builtin:
            _readers.setdefault(referrer_kind, reader)
        else:
            _readers[referrer_kind] = reader


def _load_builtin_readers() -> None:
    global _loading_builtin
    with _reader_lock:
        if _loading_builtin:
            return
        _loading_builtin = True
        try:
            for name in BUILTIN_BOOK_READER_MODULES:
                before = dict(_readers)
                try:
                    module = importlib.import_module(name)
                    register = getattr(module, "register_book_readers", None)
                    if callable(register):
                        register()
                except Exception:
                    _readers.clear()
                    _readers.update(before)
                    logger.exception("Could not load book reference readers from %s", name)
        finally:
            _loading_builtin = False


def _bound_record(conn: sqlite3.Connection, record: BookRecord) -> BookRecord | None:
    current = _get_book(conn, record.book_id)
    if current is None or (current.base_dir, current.dir_name) != (record.base_dir, record.dir_name):
        return None
    return current


def _can_read(conn: sqlite3.Connection, record: BookRecord, user: CurrentUser) -> bool:
    current = _bound_record(conn, record)
    if current is None:
        return False
    if user.is_admin or current.visibility == "public" or current.created_by == user.id:
        return True
    _load_builtin_readers()
    for reference in _references_of(conn, current.book_id):
        with _reader_lock:
            reader = _readers.get(reference.referrer_kind)
        if reader is not None:
            try:
                if reader(reference.referrer_id, user.id):
                    return True
            except Exception:
                logger.warning("Book reference reader for %s failed", reference.referrer_kind, exc_info=True)
    return False


def can_read_book(record: BookRecord, user: CurrentUser) -> bool:
    with _read() as conn:
        return conn is not None and _can_read(conn, record, user)


def _can_write(conn: sqlite3.Connection, record: BookRecord, user: CurrentUser) -> bool:
    current = _bound_record(conn, record)
    return current is not None and (user.is_admin or current.created_by == user.id)


def can_write_book(record: BookRecord, user: CurrentUser) -> bool:
    with _read() as conn:
        return conn is not None and _can_write(conn, record, user)


def _set_kb_links(conn: sqlite3.Connection, book_id: str, links: Mapping[str, str]) -> None:
    record = _target(conn, book_id)
    conn.execute("DELETE FROM book_kb_links WHERE book_id = ?", (record.book_id,))
    conn.executemany(
        "INSERT INTO book_kb_links (book_id, position, ref, kb_id) VALUES (?, ?, ?, ?)",
        [(record.book_id, index, ref, kb_id) for index, (ref, kb_id) in enumerate(links.items())],
    )


def set_book_kb_links(book_id: str, links: Mapping[str, str]) -> None:
    """Store fixed KB identities, including explicitly unavailable empty values."""
    with _write() as conn:
        _set_kb_links(conn, book_id, links)


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
