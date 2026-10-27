"""System-wide knowledge-base registry (spec section 7.2).

Knowledge bases stay where they were created — a ``kb_config.json`` key plus a
folder under some ``base_dir`` (the admin library, an account library, a custom
workspace, a partner workspace). The registry gives each one a fixed id, an
owner and a visibility, and records what references it. New references are
written ``kb:<kb_id>``; legacy ones (``admin:kb:``, ``user:kb:``,
``account:kb:``, ``workspace:<id>:kb:``, bare names) keep resolving through
:mod:`deeptutor.multi_user.knowledge_access` and map onto a record by location.

Reads: admins, public KBs, the creator, and anyone a registered reference
reader vouches for. Writes: admins and the creator. Personal connectors are
always private and only the owner's partners may reference them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib
import json
import logging
from pathlib import Path
import sqlite3
import threading
from typing import TYPE_CHECKING, Any, Literal
import uuid

from deeptutor.knowledge.file_snapshot import FileSnapshotCache
from deeptutor.knowledge.kb_types import (
    CONNECTED_KB_TYPES, CONNECTOR_CONFIG_FIELDS, CONNECTOR_PROVIDERS,
)

if TYPE_CHECKING:
    from deeptutor.multi_user.models import CurrentUser

logger = logging.getLogger(__name__)

Visibility = Literal["public", "private"]
VISIBILITIES: frozenset[str] = frozenset({"public", "private"})
REF_PREFIX = "kb:"
UPLOADED_KIND = "uploaded"
REGISTRY_FILENAME = "kb_registry.sqlite3"

#: Pointers into a person's devices, folders or credentials (spec 7.3).
CONNECTOR_KINDS: frozenset[str] = frozenset(CONNECTED_KB_TYPES)
#: The only referrers that may point at a connector: the owner's own partners.
CONNECTOR_REFERRER_KINDS: frozenset[str] = frozenset({"partner"})
#: KB kinds that need the deployment's embedding model.
INDEXED_KINDS: frozenset[str] = frozenset({UPLOADED_KIND, "linked"})
#: Synthetic user id prefix of partner runtimes (``services/partners/scope.py``).
PARTNER_USER_PREFIX = "partner_"

#: Tests point this at a temporary file; ``None`` means
#: ``<SYSTEM_ROOT>/kb_registry.sqlite3`` (read per call, so a monkeypatched
#: ``SYSTEM_ROOT`` is honored).
DB_PATH_OVERRIDE: Path | None = None

#: Modules that call :func:`register_reference_reader` on import. Imported the
#: first time a reference is checked, so the registry never imports a feature
#: package eagerly. Plan C adds its mastery-template module here.
BUILTIN_READER_MODULES: tuple[str, ...] = ("deeptutor.book.registry", "deeptutor.teaching.access")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_registry (
    kb_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    base_dir TEXT NOT NULL,
    dir_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility IN ('public', 'private')),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (base_dir, dir_name)
);
CREATE TABLE IF NOT EXISTS kb_references (
    kb_id TEXT NOT NULL REFERENCES kb_registry(kb_id) ON DELETE CASCADE,
    referrer_kind TEXT NOT NULL,
    referrer_id TEXT NOT NULL,
    doc_ids TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (kb_id, referrer_kind, referrer_id)
);
CREATE INDEX IF NOT EXISTS kb_references_by_referrer
    ON kb_references (referrer_kind, referrer_id);
CREATE TABLE IF NOT EXISTS registry_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kb_documents (
    kb_id TEXT NOT NULL REFERENCES kb_registry(kb_id) ON DELETE CASCADE,
    doc_id TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (kb_id, doc_id),
    UNIQUE (kb_id, rel_path)
);
CREATE TABLE IF NOT EXISTS kb_document_history (
    kb_id TEXT NOT NULL REFERENCES kb_registry(kb_id) ON DELETE CASCADE,
    doc_id TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    deleted INTEGER NOT NULL CHECK (deleted = 1),
    PRIMARY KEY (kb_id, doc_id)
);
CREATE TABLE IF NOT EXISTS kb_admin_legacy_names (
    name TEXT PRIMARY KEY,
    kb_id TEXT NOT NULL REFERENCES kb_registry(kb_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS kb_revoked_references (
    kb_id TEXT NOT NULL,
    kb_name TEXT NOT NULL,
    referrer_kind TEXT NOT NULL,
    referrer_id TEXT NOT NULL,
    doc_ids TEXT,
    created_by TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (reason IN ('made_private', 'kb_deleted')),
    revoked_at TEXT NOT NULL,
    PRIMARY KEY (kb_id, referrer_kind, referrer_id)
);
CREATE INDEX IF NOT EXISTS kb_revoked_by_referrer
    ON kb_revoked_references (referrer_kind, referrer_id);
CREATE TABLE IF NOT EXISTS migration_items (
    kind TEXT NOT NULL,
    item_key TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (kind, item_key)
);
"""

_write_lock = threading.RLock()
_builtin_readers_loaded = False
_reader_lock = threading.RLock()
_builtin_reader_state = threading.local()


class KbRegistryError(ValueError):
    """A registry operation that is not allowed or not possible."""


class KbVisibilityError(KbRegistryError):
    """Visibility change refused (connector made public, or foreign references)."""


class KbReferenceError(KbRegistryError):
    """Reference refused (unknown KB, or a connector shared outside its owner)."""


@dataclass(frozen=True)
class KbRecord:
    kb_id: str
    name: str
    base_dir: str
    dir_name: str
    kind: str
    visibility: Visibility
    created_by: str
    created_at: str
    updated_at: str

    @property
    def ref(self) -> str:
        return f"{REF_PREFIX}{self.kb_id}"


@dataclass(frozen=True)
class KbReference:
    kb_id: str
    referrer_kind: str
    referrer_id: str
    doc_ids: list[str] | None
    created_by: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kb_id": self.kb_id,
            "referrer_kind": self.referrer_kind,
            "referrer_id": self.referrer_id,
            "doc_ids": self.doc_ids,
            "created_by": self.created_by,
        }


RevocationReason = Literal["made_private", "kb_deleted"]


@dataclass(frozen=True)
class RevokedReference:
    """A reference an admin's force withdrew (spec 7.4 "资料不可用").

    Kept outside ``kb_references`` (no foreign key, so it survives the KB's
    deletion) and never consulted by :func:`can_read`: the referrer lost
    access. Cleared when the referrer re-states its references, except for
    unavailable identities explicitly retained by the referrer.
    """

    kb_id: str
    kb_name: str
    referrer_kind: str
    referrer_id: str
    doc_ids: list[str] | None
    created_by: str
    reason: RevocationReason
    revoked_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kb_id": self.kb_id,
            "kb_name": self.kb_name,
            "referrer_kind": self.referrer_kind,
            "referrer_id": self.referrer_id,
            "doc_ids": self.doc_ids,
            "created_by": self.created_by,
            "reason": self.reason,
            "revoked_at": self.revoked_at,
        }


def _partner_reader(referrer_id: str, user_id: str) -> bool:
    # A partner turn runs as the synthetic user ``partner_<id>``.
    return user_id == f"{PARTNER_USER_PREFIX}{referrer_id}"


_readers: dict[str, Callable[[str, str], bool]] = {"partner": _partner_reader}


# ── storage ─────────────────────────────────────────────────────────


def _db_path() -> Path:
    if DB_PATH_OVERRIDE is not None:
        return DB_PATH_OVERRIDE
    from deeptutor.multi_user import paths

    return paths.SYSTEM_ROOT / REGISTRY_FILENAME


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    return conn


@contextmanager
def _read() -> Iterator[sqlite3.Connection | None]:
    """A connection, or ``None`` before anything was ever registered."""
    path = _db_path()
    if not path.exists():
        yield None
        return
    conn = _open(path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def _write() -> Iterator[sqlite3.Connection]:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock:
        conn = _open(path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _location(base_dir: Path | str) -> str:
    return str(Path(base_dir).expanduser().resolve())


def _strip_ref(kb_id: str) -> str:
    value = str(kb_id or "").strip()
    return value[len(REF_PREFIX) :] if value.startswith(REF_PREFIX) else value


def _record(row: sqlite3.Row) -> KbRecord:
    return KbRecord(
        kb_id=row["kb_id"],
        name=row["name"],
        base_dir=row["base_dir"],
        dir_name=row["dir_name"],
        kind=row["kind"],
        visibility=row["visibility"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _reference(row: sqlite3.Row) -> KbReference:
    raw = row["doc_ids"]
    return KbReference(
        kb_id=row["kb_id"],
        referrer_kind=row["referrer_kind"],
        referrer_id=row["referrer_id"],
        doc_ids=None if raw is None else [str(item) for item in json.loads(raw)],
        created_by=row["created_by"],
    )


def _doc_ids_json(doc_ids: Sequence[str] | None) -> str | None:
    if doc_ids is None:
        return None
    return json.dumps(list(dict.fromkeys(str(item) for item in doc_ids)), ensure_ascii=False)


# ── records ─────────────────────────────────────────────────────────


def kind_of(entry: Any) -> str:
    """Registry kind of a ``kb_config.json`` entry: a connector type or ``uploaded``."""
    kind = entry.get("type") if isinstance(entry, dict) else None
    return str(kind) if kind in CONNECTOR_KINDS else UPLOADED_KIND


# Parsed ``kb_config.json`` payloads, re-validated against the file on every
# use; treat cached payloads as read-only.
_CONFIG_PAYLOADS = FileSnapshotCache()


def _parse_config(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def config_kind(record: KbRecord) -> str | None:
    """Read the on-disk kind used by direct consumers of a registry record."""
    path = Path(record.base_dir) / "kb_config.json"
    try:
        payload = _CONFIG_PAYLOADS.load(path, _parse_config)
    except FileNotFoundError:
        # Registry-only records have no readable resource yet. Keep their
        # declared restrictions; resolution separately requires a live entry.
        return record.kind
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    entries = payload.get("knowledge_bases")
    if not isinstance(entries, dict):
        return None
    if record.dir_name not in entries:
        # Preserve unavailable registry rows in lists without inventing a new
        # classification; resource resolution still requires an actual entry.
        return record.kind
    entry = entries.get(record.dir_name)
    if not isinstance(entry, dict):
        return None
    if entry.get("type") is not None and not isinstance(entry["type"], str):
        return None
    if entry.get("rag_provider") is not None and not isinstance(entry["rag_provider"], str):
        return None
    if kind_of(entry) == UPLOADED_KIND and (
        any(entry.get(key) for key in CONNECTOR_CONFIG_FIELDS)
        or (entry.get("rag_provider") or "").strip().lower() in CONNECTOR_PROVIDERS
    ):
        return None
    return kind_of(entry)


def classification_is_consistent(record: KbRecord) -> bool:
    return record.kind == config_kind(record)


def is_connector(record: KbRecord) -> bool:
    return record.kind in CONNECTOR_KINDS or config_kind(record) != UPLOADED_KIND


def _check_visibility(visibility: str, kind: str) -> None:
    if visibility not in VISIBILITIES:
        raise KbVisibilityError("Visibility must be 'public' or 'private'.")
    if visibility == "public" and kind in CONNECTOR_KINDS:
        raise KbVisibilityError(
            "Personal connectors are always private and cannot be made public."
        )


def register_kb(
    *,
    base_dir: Path | str,
    name: str,
    kind: str,
    created_by: str,
    visibility: str = "private",
) -> KbRecord:
    """Register ``name`` under ``base_dir``; an existing registration is returned unchanged."""
    kind = kind or UPLOADED_KIND
    _check_visibility(visibility, kind)
    location = _location(base_dir)
    now = _now()
    with _write() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO kb_registry (kb_id, name, base_dir, dir_name, kind, "
            "visibility, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, name, location, name, kind, visibility, created_by, now, now),
        )
        row = conn.execute(
            "SELECT * FROM kb_registry WHERE base_dir = ? AND dir_name = ?", (location, name)
        ).fetchone()
    return _record(row)


def register_new_kb(
    *,
    base_dir: Path | str,
    name: str,
    created_by: str,
    visibility: str,
    check_available: Callable[[], None],
) -> KbRecord:
    """Replace a stale identity before publishing a new catalog entry.

    The caller holds per-KB write ownership through catalog publication.
    Commit identity replacement before publishing, so concurrent readers cannot
    associate the new catalog entry with the old uncommitted identity.
    """
    _check_visibility(visibility, UPLOADED_KIND)
    location = _location(base_dir)
    with _write() as conn:
        check_available()
        row = conn.execute(
            "SELECT * FROM kb_registry WHERE base_dir = ? AND dir_name = ?", (location, name)
        ).fetchone()
        if row is not None:
            stale = _record(row)
            _revoke(conn, stale, _references_of(conn, stale.kb_id), reason="kb_deleted")
            conn.execute("DELETE FROM kb_registry WHERE kb_id = ?", (stale.kb_id,))
        kb_id, now = uuid.uuid4().hex, _now()
        conn.execute(
            "INSERT INTO kb_registry (kb_id, name, base_dir, dir_name, kind, "
            "visibility, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kb_id, name, location, name, UPLOADED_KIND, visibility, created_by, now, now),
        )
        from deeptutor.core.visibility_intent import record_choice

        record_choice(conn, kb_id, visibility, schema="main")
        row = conn.execute("SELECT * FROM kb_registry WHERE kb_id = ?", (kb_id,)).fetchone()
    return _record(row)


def _prepare_initialized_kb(base_dir: Path, name: str, *, created_by: str) -> None:
    """Reserve CLI creation authority before the directory/catalog is visible.

    Existing registrations (including API reservations) keep their owner,
    visibility and intent. Repairing a missing directory is not new consent.
    """
    from deeptutor.core.visibility_intent import record_choice

    with _write() as conn:
        if (base_dir / name).exists():
            return
        location = _location(base_dir)
        row = conn.execute(
            "SELECT * FROM kb_registry WHERE base_dir = ? AND dir_name = ?", (location, name),
        ).fetchone()
        if row is None:
            kb_id, now = uuid.uuid4().hex, _now()
            conn.execute(
                "INSERT INTO kb_registry (kb_id, name, base_dir, dir_name, kind, "
                "visibility, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'private', ?, ?, ?)",
                (kb_id, name, location, name, UPLOADED_KIND, created_by, now, now),
            )
            record_choice(conn, kb_id, "private", schema="main")


def get_kb(kb_id: str) -> KbRecord | None:
    with _read() as conn:
        if conn is None:
            return None
        row = conn.execute(
            "SELECT * FROM kb_registry WHERE kb_id = ?", (_strip_ref(kb_id),)
        ).fetchone()
    return _record(row) if row is not None else None


def find_by_location(base_dir: Path | str, name: str) -> KbRecord | None:
    with _read() as conn:
        if conn is None:
            return None
        row = conn.execute(
            "SELECT * FROM kb_registry WHERE base_dir = ? AND dir_name = ?",
            (_location(base_dir), name),
        ).fetchone()
    return _record(row) if row is not None else None


def find_admin_legacy_name(name: str) -> KbRecord | None:
    """Resolve a historical admin name to identity only, never to permission."""
    with _read() as conn:
        if conn is None:
            return None
        row = conn.execute(
            "SELECT r.* FROM kb_registry r JOIN kb_admin_legacy_names a "
            "ON r.kb_id = a.kb_id WHERE a.name = ?", (name,),
        ).fetchone()
    return _record(row) if row is not None else None


def admin_legacy_refs(kb_id: str) -> list[str]:
    """Global admin aliases only; callers must authorize the KB before exposing these."""
    from deeptutor.multi_user.paths import get_admin_path_service

    root = _location(get_admin_path_service().get_knowledge_bases_root())
    with _read() as conn:
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT name FROM kb_admin_legacy_names WHERE kb_id = ? "
            "UNION SELECT dir_name AS name FROM kb_registry "
            "WHERE kb_id = ? AND base_dir = ? AND NOT EXISTS "
            "(SELECT 1 FROM kb_admin_legacy_names a WHERE a.name = dir_name) "
            "ORDER BY name",
            (_strip_ref(kb_id), _strip_ref(kb_id), root),
        ).fetchall()
    return [f"admin:kb:{row['name']}" for row in rows]


def _retain_admin_legacy_names(conn: sqlite3.Connection, *, kb_id: str | None = None) -> None:
    from deeptutor.multi_user.paths import get_admin_path_service

    root = _location(get_admin_path_service().get_knowledge_bases_root())
    # Only the shared admin-default namespace is global. Account/workspace
    # aliases remain in their existing account-private runtime files.
    conn.execute(
        "INSERT OR IGNORE INTO kb_admin_legacy_names (name, kb_id) "
        "SELECT dir_name, kb_id FROM kb_registry WHERE base_dir = ? "
        "AND (? IS NULL OR kb_id = ?)", (root, kb_id, kb_id),
    )


def list_records(
    *,
    visibility: str | None = None,
    created_by: str | None = None,
    base_dir: Path | str | None = None,
) -> list[KbRecord]:
    clauses: list[str] = []
    params: list[str] = []
    if visibility is not None:
        clauses.append("visibility = ?")
        params.append(visibility)
    if created_by is not None:
        clauses.append("created_by = ?")
        params.append(created_by)
    if base_dir is not None:
        clauses.append("base_dir = ?")
        params.append(_location(base_dir))
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with _read() as conn:
        if conn is None:
            return []
        rows = conn.execute(
            f"SELECT * FROM kb_registry{where} ORDER BY name, kb_id",  # nosec B608 - fixed fragments
            params,
        ).fetchall()
    return [_record(row) for row in rows]


def sync_base_dir(
    base_dir: Path | str, entries: Mapping[str, Any], *, created_by: str
) -> dict[str, KbRecord]:
    """Register every ``name -> kb_config entry`` not registered yet; return all of them.

    Read-mostly: the write only happens when something is missing, so calling
    this from a list endpoint costs one query once everything is registered.
    """
    if not entries:
        return {}
    known = {record.dir_name: record for record in list_records(base_dir=base_dir)}
    missing = [name for name in entries if name not in known]
    if missing:
        location = _location(base_dir)
        now = _now()
        with _write() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO kb_registry (kb_id, name, base_dir, dir_name, kind, "
                "visibility, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'private', ?, ?, ?)",
                [
                    (uuid.uuid4().hex, name, location, name, kind_of(entries[name]), created_by, now, now)
                    for name in missing
                ],
            )
        known = {record.dir_name: record for record in list_records(base_dir=base_dir)}
    return {name: known[name] for name in entries if name in known}


def ensure_registered(base_dir: Path | str, name: str, *, created_by: str) -> KbRecord:
    """The record at this location, registering it (kind from its config entry) if needed."""
    existing = find_by_location(base_dir, name)
    if existing is not None:
        return existing
    from deeptutor.knowledge.manager import KnowledgeBaseManager

    entry = KnowledgeBaseManager(base_dir=str(base_dir)).get_kb_entry(name)
    return register_kb(base_dir=base_dir, name=name, kind=kind_of(entry), created_by=created_by)


def _owner_for_resource(resource: Any, caller_id: str) -> str | None:
    """Decide who should own an auto-registered record.

    Admin-library KBs belong to ``LOCAL_ADMIN_ID`` regardless of who resolves
    them first. User-library KBs belong to the caller only when they have write
    access (``not resource.read_only``); a read-only foreign resource that isn't
    registered yet returns ``None`` to signal "don't auto-register".
    """
    from deeptutor.multi_user.knowledge_access import admin_kb_base_dir
    from deeptutor.multi_user.models import LOCAL_ADMIN_ID

    if _location(resource.base_dir) == _location(admin_kb_base_dir()):
        return LOCAL_ADMIN_ID
    if resource.read_only:
        return None
    return caller_id


def resolve_ref(ref: str) -> KbRecord | None:
    """The record behind any KB reference.

    ``kb:<id>`` is a plain lookup (pair it with :func:`can_read`). Legacy forms
    are resolved — with access checks — relative to the current user's scope
    by :func:`deeptutor.multi_user.knowledge_access.resolve_kb`, and registered
    on first sight under the current user.
    """
    value = str(ref or "").strip()
    if not value:
        return None
    if value.startswith(REF_PREFIX):
        return get_kb(value)
    from fastapi import HTTPException

    from deeptutor.multi_user.context import get_current_user
    from deeptutor.multi_user.knowledge_access import resolve_kb

    try:
        resource = resolve_kb(value)
    except HTTPException as exc:
        if exc.status_code in {400, 403, 404}:
            return None
        raise
    owner = _owner_for_resource(resource, get_current_user().id)
    if owner is None:
        return find_by_location(resource.base_dir, resource.name)
    return ensure_registered(resource.base_dir, resource.name, created_by=owner)


def unregister_kb(kb_id: str) -> None:
    """Forget a KB. References still pointing at it (only possible after an
    admin's force delete) are kept as revoked with reason ``kb_deleted``."""
    with _write() as conn:
        row = conn.execute(
            "SELECT * FROM kb_registry WHERE kb_id = ?", (_strip_ref(kb_id),)
        ).fetchone()
        if row is None:
            return
        record = _record(row)
        _revoke(conn, record, _references_of(conn, record.kb_id), reason="kb_deleted")
        conn.execute("DELETE FROM kb_registry WHERE kb_id = ?", (record.kb_id,))


@contextmanager
def guarded_deletion(
    base_dir: Path | str, name: str
) -> Iterator[tuple[KbRecord | None, list[KbReference]]]:
    """Hold a write transaction through authorization and detaching the KB.

    The caller checks the yielded record/references and raises if deletion is
    refused or fails. Only successful exit revokes references and unregisters.
    Keep the body short (rename, config update): slow file removal belongs
    after the commit, since every registry writer in every process waits here.
    BEGIN IMMEDIATE also excludes reference writers in other processes, even
    when this location has not been registered yet.
    """
    with _write() as conn:
        row = conn.execute(
            "SELECT * FROM kb_registry WHERE base_dir = ? AND dir_name = ?",
            (_location(base_dir), name),
        ).fetchone()
        record = _record(row) if row is not None else None
        references = _references_of(conn, record.kb_id) if record is not None else []
        yield record, references
        if record is not None:
            _revoke(conn, record, references, reason="kb_deleted")
            conn.execute("DELETE FROM kb_registry WHERE kb_id = ?", (record.kb_id,))


def adopt_partner_copy(kb_id: str, *, partner_id: str, owner_id: str) -> KbRecord:
    """Claim one synthetic legacy copy without replacing its ID or consumers."""
    with _write() as conn:
        row = conn.execute(
            "SELECT * FROM kb_registry WHERE kb_id = ?", (_strip_ref(kb_id),)
        ).fetchone()
        if row is None or row["created_by"] not in {f"partner_{partner_id}", owner_id}:
            raise KbRegistryError("Legacy copy ownership changed during migration.")
        if row["visibility"] != "private":
            raise KbRegistryError("Legacy copy must be private before adoption.")
        conn.execute(
            "UPDATE kb_registry SET created_by = ?, updated_at = ? WHERE kb_id = ?",
            (owner_id, _now(), row["kb_id"]),
        )
        return _record(conn.execute(
            "SELECT * FROM kb_registry WHERE kb_id = ?", (row["kb_id"],)
        ).fetchone())


def _relocate_kb(conn: sqlite3.Connection, kb_id: str, base_dir: Path | str) -> int:
    """Relocate within an existing writer, preserving legacy canonical aliases."""
    _retain_admin_legacy_names(conn, kb_id=kb_id)
    return conn.execute(
        "UPDATE kb_registry SET base_dir = ?, updated_at = ? WHERE kb_id = ?",
        (_location(base_dir), _now(), kb_id),
    ).rowcount


def relocate_kb(kb_id: str, base_dir: Path | str) -> KbRecord:
    """Point a record at a new ``base_dir`` (the KB folder was moved); the id is kept."""
    try:
        with _write() as conn:
            updated = _relocate_kb(conn, _strip_ref(kb_id), base_dir)
    except sqlite3.IntegrityError as exc:
        raise KbRegistryError("Another knowledge base is registered at that location.") from exc
    record = get_kb(kb_id)
    if not updated or record is None:
        raise KbRegistryError(f"Knowledge base {kb_id} is not registered.")
    return record


def relocate_base_dir(old_base_dir: Path | str, new_base_dir: Path | str) -> int:
    """Follow a whole catalog moved to another root; returns the number of records moved."""
    try:
        with _write() as conn:
            from deeptutor.multi_user.paths import get_admin_path_service

            if _location(old_base_dir) == _location(
                get_admin_path_service().get_knowledge_bases_root()
            ):
                _retain_admin_legacy_names(conn)
            return conn.execute(
                "UPDATE kb_registry SET base_dir = ?, updated_at = ? WHERE base_dir = ?",
                (_location(new_base_dir), _now(), _location(old_base_dir)),
            ).rowcount
    except sqlite3.IntegrityError as exc:
        raise KbRegistryError("The destination already has registered knowledge bases.") from exc


def set_visibility(kb_id: str, visibility: str, *, force: bool = False) -> KbRecord:
    """Change visibility. Who may call this (D3, D12) is the API layer's decision.

    Making a public KB private is refused while another account references it
    (spec 7.4) unless ``force`` — reserved for admins by the caller. A forced
    withdrawal moves those other accounts' references to the revoked table
    (reason ``made_private``, see :func:`list_unavailable_references`).
    References by the owner alone stay and simply turn the KB into "readable
    through a reference".

    The record lookup, reference listing and decision all happen inside a single
    ``BEGIN IMMEDIATE`` transaction so a concurrent ``add_reference`` cannot
    slip between the check and the update.
    """
    with _write() as conn:
        row = conn.execute(
            "SELECT * FROM kb_registry WHERE kb_id = ?", (_strip_ref(kb_id),)
        ).fetchone()
        if row is None:
            raise KbRegistryError(f"Knowledge base {kb_id} is not registered.")
        record = _record(row)
        _check_visibility(visibility, record.kind)
        if visibility == "public" and is_connector(record):
            raise KbVisibilityError("Inconsistent or connected knowledge bases cannot be made public.")
        foreign: list[KbReference] = []
        if visibility == "private" and record.visibility == "public":
            foreign = [
                ref for ref in _references_of(conn, record.kb_id)
                if ref.created_by != record.created_by
            ]
            if foreign and not force:
                names = ", ".join(f"{ref.referrer_kind}:{ref.referrer_id}" for ref in foreign)
                raise KbVisibilityError(
                    f"Still referenced by other accounts ({names}); "
                    "it cannot be made private until those references are removed."
                )
        # Spec 7.4: an admin's forced withdrawal cuts the other accounts off;
        # their references become "unavailable" instead of silently reading on.
        _revoke(conn, record, foreign, reason="made_private")
        conn.execute(
            "UPDATE kb_registry SET visibility = ?, updated_at = ? WHERE kb_id = ?",
            (visibility, _now(), record.kb_id),
        )
        from deeptutor.core.visibility_intent import record_choice

        record_choice(conn, record.kb_id, visibility, schema="main")
    updated = get_kb(record.kb_id)
    if updated is None:
        raise KbRegistryError(f"Knowledge base {kb_id} disappeared after visibility update.")
    return updated


def list_referenced(referrer_kind: str, referrer_id: str) -> list[KbRecord]:
    with _read() as conn:
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT r.* FROM kb_registry r JOIN kb_references x ON x.kb_id = r.kb_id "
            "WHERE x.referrer_kind = ? AND x.referrer_id = ? ORDER BY r.name, r.kb_id",
            (referrer_kind, referrer_id),
        ).fetchall()
    return [_record(row) for row in rows]


def transfer_ownership(from_user_id: str, to_user_id: str) -> list[KbRecord]:
    """Hand an account's public and referenced KBs to another owner (account deletion)."""
    with _write() as conn:
        rows = conn.execute(
            "SELECT * FROM kb_registry WHERE created_by = ? AND (visibility = 'public' "
            "OR kb_id IN (SELECT kb_id FROM kb_references))",
            (from_user_id,),
        ).fetchall()
        now = _now()
        conn.executemany(
            "UPDATE kb_registry SET created_by = ?, updated_at = ? WHERE kb_id = ?",
            [(to_user_id, now, row["kb_id"]) for row in rows],
        )
        # Keep the old owner's own consumers same-owner after hand-over.
        # Foreign consumers retain their identity and visibility safeguards.
        # Book refs follow actual book transfers in book.sharing's joint writer,
        # and teacher-path ("mastery_template") refs follow the template in the
        # teaching store's joint writer; updating them here would survive a
        # failed hand-over of the book or the path.
        conn.executemany(
            "UPDATE kb_references SET created_by = ? WHERE kb_id = ? AND created_by = ? "
            "AND referrer_kind NOT IN ('book', 'mastery_template')",
            [(to_user_id, row["kb_id"], from_user_id) for row in rows],
        )
    return [record for row in rows if (record := get_kb(row["kb_id"])) is not None]


def has_indexed_kbs() -> bool:
    with _read() as conn:
        if conn is None:
            return False
        placeholders = ", ".join("?" for _ in INDEXED_KINDS)
        row = conn.execute(
            f"SELECT 1 FROM kb_registry WHERE kind IN ({placeholders}) LIMIT 1",
            tuple(sorted(INDEXED_KINDS)),
        ).fetchone()
    return row is not None


def get_meta(key: str) -> str | None:
    with _read() as conn:
        if conn is None:
            return None
        row = conn.execute("SELECT value FROM registry_meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def set_meta(key: str, value: str) -> None:
    with _write() as conn:
        conn.execute(
            "INSERT INTO registry_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


# ── migration results (R1) ──────────────────────────────────────────


@dataclass(frozen=True)
class MigrationItem:
    """One line of the admin "migration results" notice.

    ``kind`` decides how the UI renders ``data``. Plan B writes
    ``kb_made_public`` ({kb_id, name, users}), ``connector_grant_disabled``
    ({name, kind, users}) and ``partner_copy_adopted`` ({partner_id, name,
    kb_id, owner_id}); later plans add their own kinds.
    """

    kind: str
    key: str
    data: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "key": self.key, "data": self.data, "created_at": self.created_at}


def add_migration_item(kind: str, key: str, data: Mapping[str, Any]) -> None:
    """Record (or refresh) one migration result; the first timestamp is kept."""
    with _write() as conn:
        conn.execute(
            "INSERT INTO migration_items (kind, item_key, data, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(kind, item_key) DO UPDATE SET data = excluded.data",
            (kind, key, json.dumps(dict(data), ensure_ascii=False), _now()),
        )


def list_migration_items() -> list[MigrationItem]:
    with _read() as conn:
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT * FROM migration_items ORDER BY created_at, rowid"
        ).fetchall()
    return [
        MigrationItem(
            kind=row["kind"],
            key=row["item_key"],
            data=json.loads(row["data"]),
            created_at=row["created_at"],
        )
        for row in rows
    ]


# ── references ──────────────────────────────────────────────────────


def _reference_target(conn: sqlite3.Connection, kb_id: str, referrer_kind: str) -> KbRecord:
    row = conn.execute(
        "SELECT * FROM kb_registry WHERE kb_id = ?", (_strip_ref(kb_id),)
    ).fetchone()
    if row is None:
        raise KbReferenceError(f"Knowledge base {kb_id} is not registered.")
    record = _record(row)
    if not classification_is_consistent(record):
        raise KbReferenceError("Knowledge-base classification is inconsistent; repair it before sharing.")
    if is_connector(record) and referrer_kind not in CONNECTOR_REFERRER_KINDS:
        raise KbReferenceError(
            "Personal connectors cannot be shared. Upload the material to a knowledge "
            "base and reference that instead."
        )
    return record


def add_reference(
    kb_id: str,
    *,
    referrer_kind: str,
    referrer_id: str,
    doc_ids: list[str] | None = None,
    created_by: str,
    validate_target: Callable[[KbRecord], None] | None = None,
) -> None:
    """Add a reference, optionally validating its fresh target before any write.

    The validator runs under BEGIN IMMEDIATE, excluding concurrent visibility
    and reference changes. It must raise to reject and must not write registry
    state. Omitting it preserves the existing trusted-caller contract.
    """
    with _write() as conn:
        record = _reference_target(conn, kb_id, referrer_kind)
        if validate_target is not None:
            validate_target(record)
        conn.execute(
            "INSERT OR REPLACE INTO kb_references (kb_id, referrer_kind, referrer_id, doc_ids, "
            "created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (record.kb_id, referrer_kind, referrer_id, _doc_ids_json(doc_ids), created_by, _now()),
        )
        conn.execute(
            "DELETE FROM kb_revoked_references WHERE kb_id = ? AND referrer_kind = ? "
            "AND referrer_id = ?",
            (record.kb_id, referrer_kind, referrer_id),
        )


def replace_references(
    referrer_kind: str,
    referrer_id: str,
    refs: list[tuple[str, list[str] | None]],
    *,
    created_by: str,
    readable_by: CurrentUser | None = None,
    retain_revoked_kb_ids: Sequence[str] = (),
    private_intent_kb_ids: Sequence[str] = (),
) -> None:
    """Atomically replace live references and settle earlier revocations.

    ``readable_by`` rechecks access using fresh targets while visibility and
    reference writers are excluded by BEGIN IMMEDIATE. Failure changes nothing.
    ``retain_revoked_kb_ids`` preserves unavailable resources still held by the
    referrer, unless they are successfully restored in ``refs``. Defaults keep
    the existing whole-set restatement contract.

    ``private_intent_kb_ids`` are the caller's explicit "share by reference
    only" choices. In this same transaction, each one that is also a target,
    still private, not a connector and writable by the actor (``readable_by``,
    else the current user) records durable private intent, so a pending legacy
    migration cannot publish it later. Other keys are ignored; nothing is
    withdrawn. Defaults and generic restatements record nothing.
    """
    with _write() as conn:
        targets = [(_reference_target(conn, kb_id, referrer_kind), doc_ids) for kb_id, doc_ids in refs]
        if readable_by is not None:
            for record, _docs in targets:
                if not can_read(record, readable_by):
                    raise KbReferenceError(f"Knowledge base {record.kb_id} is no longer readable.")
        retained = {_strip_ref(kb_id) for kb_id in retain_revoked_kb_ids}
        retained.difference_update(record.kb_id for record, _docs in targets)
        conn.execute(
            "DELETE FROM kb_references WHERE referrer_kind = ? AND referrer_id = ?",
            (referrer_kind, referrer_id),
        )
        revoked = conn.execute(
            "SELECT kb_id FROM kb_revoked_references WHERE referrer_kind = ? AND referrer_id = ?",
            (referrer_kind, referrer_id),
        ).fetchall()
        conn.executemany(
            "DELETE FROM kb_revoked_references WHERE referrer_kind = ? AND referrer_id = ? "
            "AND kb_id = ?",
            [
                (referrer_kind, referrer_id, row["kb_id"])
                for row in revoked if row["kb_id"] not in retained
            ],
        )
        now = _now()
        conn.executemany(
            "INSERT OR REPLACE INTO kb_references (kb_id, referrer_kind, referrer_id, doc_ids, "
            "created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (record.kb_id, referrer_kind, referrer_id, _doc_ids_json(doc_ids), created_by, now)
                for record, doc_ids in targets
            ],
        )
        explicit = {_strip_ref(kb_id) for kb_id in private_intent_kb_ids}
        if explicit:
            from deeptutor.core.visibility_intent import record_choice
            from deeptutor.multi_user.context import get_current_user

            actor = readable_by or get_current_user()
            for record, _docs in targets:
                if (
                    record.kb_id in explicit
                    and record.visibility == "private"
                    and not is_connector(record)
                    and can_write(record, actor)
                ):
                    record_choice(conn, record.kb_id, "private", schema="main")


def remove_references(referrer_kind: str, referrer_id: str) -> None:
    """The referrer is gone (or let go of everything): drop live and revoked rows."""
    with _write() as conn:
        conn.execute(
            "DELETE FROM kb_references WHERE referrer_kind = ? AND referrer_id = ?",
            (referrer_kind, referrer_id),
        )
        conn.execute(
            "DELETE FROM kb_revoked_references WHERE referrer_kind = ? AND referrer_id = ?",
            (referrer_kind, referrer_id),
        )


def _references_of(conn: sqlite3.Connection, kb_id: str) -> list[KbReference]:
    rows = conn.execute("SELECT * FROM kb_references WHERE kb_id = ?", (kb_id,)).fetchall()
    return [_reference(row) for row in rows]


def _revoke(
    conn: sqlite3.Connection,
    record: KbRecord,
    references: Sequence[KbReference],
    *,
    reason: RevocationReason,
) -> None:
    now = _now()
    for ref in references:
        conn.execute(
            "INSERT OR REPLACE INTO kb_revoked_references (kb_id, kb_name, referrer_kind, "
            "referrer_id, doc_ids, created_by, reason, revoked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.kb_id,
                record.name,
                ref.referrer_kind,
                ref.referrer_id,
                _doc_ids_json(ref.doc_ids),
                ref.created_by,
                reason,
                now,
            ),
        )
        conn.execute(
            "DELETE FROM kb_references WHERE kb_id = ? AND referrer_kind = ? AND referrer_id = ?",
            (record.kb_id, ref.referrer_kind, ref.referrer_id),
        )


def _revoked(row: sqlite3.Row) -> RevokedReference:
    raw = row["doc_ids"]
    return RevokedReference(
        kb_id=row["kb_id"],
        kb_name=row["kb_name"],
        referrer_kind=row["referrer_kind"],
        referrer_id=row["referrer_id"],
        doc_ids=None if raw is None else [str(item) for item in json.loads(raw)],
        created_by=row["created_by"],
        reason=row["reason"],
        revoked_at=row["revoked_at"],
    )


def list_unavailable_references(referrer_kind: str, referrer_id: str) -> list[RevokedReference]:
    """What an admin's force took away from this referrer (spec 7.4).

    Plan C shows "资料不可用" on a teacher path for every row returned here.
    """
    with _read() as conn:
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT * FROM kb_revoked_references WHERE referrer_kind = ? AND referrer_id = ? "
            "ORDER BY revoked_at, kb_name",
            (referrer_kind, referrer_id),
        ).fetchall()
    return [_revoked(row) for row in rows]


def list_revoked_references(kb_id: str) -> list[RevokedReference]:
    with _read() as conn:
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT * FROM kb_revoked_references WHERE kb_id = ? ORDER BY referrer_kind, referrer_id",
            (_strip_ref(kb_id),),
        ).fetchall()
    return [_revoked(row) for row in rows]


def remove_reference(kb_id: str, referrer_kind: str, referrer_id: str) -> None:
    with _write() as conn:
        conn.execute(
            "DELETE FROM kb_references WHERE kb_id = ? AND referrer_kind = ? AND referrer_id = ?",
            (_strip_ref(kb_id), referrer_kind, referrer_id),
        )


def list_references(kb_id: str) -> list[KbReference]:
    with _read() as conn:
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT * FROM kb_references WHERE kb_id = ? ORDER BY referrer_kind, referrer_id",
            (_strip_ref(kb_id),),
        ).fetchall()
    return [_reference(row) for row in rows]


# ── access ──────────────────────────────────────────────────────────


def register_reference_reader(referrer_kind: str, reader: Callable[[str, str], bool]) -> None:
    """``reader(referrer_id, user_id)`` is True when that user may read what the referrer cites."""
    pending = getattr(_builtin_reader_state, "pending", None)
    if pending is not None:
        pending.setdefault(referrer_kind, reader)
        return
    with _reader_lock:
        _readers[referrer_kind] = reader


def _load_builtin_readers() -> None:
    """Import and invoke cached hooks without holding the reader-map lock.

    Stage each module's registrations on this thread until its hook succeeds.
    Concurrent explicit registrations retain precedence, even if a hook fails.
    Concurrent loaders may repeat idempotent hooks; none waits for a module
    while holding a lock needed by that module's eager registrar.
    """
    global _builtin_readers_loaded
    if getattr(_builtin_reader_state, "pending", None) is not None:
        return
    with _reader_lock:
        if _builtin_readers_loaded:
            return
    complete = True
    for name in BUILTIN_READER_MODULES:
        pending: dict[str, Callable[[str, str], bool]] = {}
        _builtin_reader_state.pending = pending
        try:
            module = importlib.import_module(name)
            register = getattr(module, "register_kb_reader", None)
            if callable(register):
                register()
        except Exception:
            complete = False
            logger.exception("Could not load knowledge reference readers from %s", name)
        else:
            with _reader_lock:
                for kind, reader in pending.items():
                    _readers.setdefault(kind, reader)
        finally:
            _builtin_reader_state.pending = None
    if complete:
        with _reader_lock:
            _builtin_readers_loaded = True


def can_read(record: KbRecord, user: CurrentUser) -> bool:
    if user.is_admin or record.created_by == user.id:
        return True
    if not classification_is_consistent(record):
        return False
    connector = is_connector(record)
    if record.visibility == "public" and not connector:
        return True
    _load_builtin_readers()
    for reference in list_references(record.kb_id):
        if connector and (
            reference.referrer_kind not in CONNECTOR_REFERRER_KINDS
            or reference.created_by != record.created_by
        ):
            continue
        reader = _readers.get(reference.referrer_kind)
        if reader is None:
            continue
        try:
            if reader(reference.referrer_id, user.id):
                return True
        except Exception:
            # A broken reader never grants access.
            logger.warning(
                "Reference reader for %s failed", reference.referrer_kind, exc_info=True
            )
    return False


def can_write(record: KbRecord, user: CurrentUser) -> bool:
    return user.is_admin or record.created_by == user.id


@dataclass(frozen=True)
class KbDocument:
    kb_id: str
    doc_id: str
    rel_path: str
    content_hash: str
    version: int
    updated_at: str
    deleted: bool


def _document(row: sqlite3.Row) -> KbDocument:
    return KbDocument(
        kb_id=row["kb_id"], doc_id=row["doc_id"], rel_path=row["rel_path"],
        content_hash=row["content_hash"], version=int(row["version"]),
        updated_at=row["updated_at"], deleted=bool(row["deleted"]),
    )


def _document_path(value: str) -> str:
    # Reject aliases rather than letting two spellings acquire different IDs.
    from pathlib import PurePosixPath

    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or path.as_posix() != value or value == ".":
        raise KbRegistryError("Document paths must be canonical relative POSIX paths.")
    return value


def _upsert_document(
    conn: sqlite3.Connection, kb_id: str, rel_path: str, content_hash: str, now: str
) -> KbDocument:
    _document_path(rel_path)
    conn.execute(
        "INSERT INTO kb_documents "
        "(kb_id, doc_id, rel_path, content_hash, version, updated_at, deleted) "
        "VALUES (?, ?, ?, ?, 1, ?, 0) "
        "ON CONFLICT(kb_id, rel_path) DO UPDATE SET "
        "content_hash = excluded.content_hash, version = kb_documents.version + 1, "
        "updated_at = excluded.updated_at, deleted = 0 "
        "WHERE kb_documents.content_hash != excluded.content_hash OR kb_documents.deleted = 1",
        (kb_id, uuid.uuid4().hex, rel_path, content_hash, now),
    )
    return _document(conn.execute(
        "SELECT * FROM kb_documents WHERE kb_id = ? AND rel_path = ?", (kb_id, rel_path)
    ).fetchone())


def upsert_documents(kb_id: str, hashes: Mapping[str, str]) -> list[KbDocument]:
    """Record only the supplied paths, returning a transaction-consistent snapshot."""
    with _write() as conn:
        now = _now()
        return [
            _upsert_document(conn, _strip_ref(kb_id), path, digest, now)
            for path, digest in hashes.items()
        ]


def sync_documents(kb_id: str, hashes: Mapping[str, str]) -> list[KbDocument]:
    """Replace the live set; deletion keeps identities for later revival."""
    kb_id = _strip_ref(kb_id)
    with _write() as conn:
        now = _now()
        documents = [
            _upsert_document(conn, kb_id, path, digest, now)
            for path, digest in hashes.items()
        ]
        rows = conn.execute(
            "SELECT doc_id, rel_path FROM kb_documents WHERE kb_id = ? AND deleted = 0", (kb_id,)
        ).fetchall()
        conn.executemany(
            "UPDATE kb_documents SET deleted = 1, updated_at = ? WHERE kb_id = ? AND doc_id = ?",
            [(now, kb_id, row["doc_id"]) for row in rows if row["rel_path"] not in hashes],
        )
        return sorted(documents, key=lambda doc: doc.rel_path)


def _mark_document_deleted(conn: sqlite3.Connection, kb_id: str, rel_path: str) -> None:
    conn.execute(
        "UPDATE kb_documents SET deleted = 1, updated_at = ? "
        "WHERE kb_id = ? AND rel_path = ? AND deleted = 0",
        (_now(), kb_id, rel_path),
    )


def mark_document_deleted(kb_id: str, rel_path: str) -> KbDocument | None:
    kb_id = _strip_ref(kb_id)
    with _write() as conn:
        _mark_document_deleted(conn, kb_id, rel_path)
        row = conn.execute(
            "SELECT * FROM kb_documents WHERE kb_id = ? AND rel_path = ?", (kb_id, rel_path)
        ).fetchone()
        return _document(row) if row else None


def _move_document_paths(
    conn: sqlite3.Connection, kb_id: str, source_rel: str, dest_rel: str
) -> None:
    _document_path(source_rel)
    _document_path(dest_rel)
    if source_rel == dest_rel:
        return
    if dest_rel.startswith(source_rel + "/"):
        raise KbRegistryError("Cannot move a folder into itself.")
    rows = conn.execute(
        "SELECT * FROM kb_documents WHERE kb_id = ?", (kb_id,)
    ).fetchall()
    # Literal prefix matching avoids SQL LIKE wildcard/backslash ambiguity.
    moved = [
        (dest_rel + row["rel_path"][len(source_rel):], row["doc_id"])
        for row in rows
        if not row["deleted"]
        and (row["rel_path"] == source_rel or row["rel_path"].startswith(source_rel + "/"))
    ]
    occupied = {row["rel_path"] for row in rows if not row["deleted"]}
    if any(path in occupied for path, _ in moved):
        raise KbRegistryError("The destination already has a document identity.")
    # Keep displaced tombstones intact, including pinned IDs and original paths.
    # The existing table remains the unique path owner and revival authority.
    for path, _ in moved:
        conn.execute(
            "INSERT INTO kb_document_history SELECT * FROM kb_documents "
            "WHERE kb_id = ? AND rel_path = ? AND deleted = 1",
            (kb_id, path),
        )
        conn.execute(
            "DELETE FROM kb_documents WHERE kb_id = ? AND rel_path = ? AND deleted = 1",
            (kb_id, path),
        )
    conn.executemany(
        "UPDATE kb_documents SET rel_path = ?, updated_at = ? WHERE kb_id = ? AND doc_id = ?",
        [(path, _now(), kb_id, doc_id) for path, doc_id in moved],
    )


def move_document_paths(kb_id: str, source_rel: str, dest_rel: str) -> None:
    """Follow an explicit file/folder move without changing identities or versions."""
    with _write() as conn:
        _move_document_paths(conn, _strip_ref(kb_id), source_rel, dest_rel)


@contextmanager
def guarded_document_change(
    base_dir: Path | str, name: str, source_rel: str, *, dest_rel: str | None = None
) -> Iterator[tuple[KbRecord | None, KbDocument | None, list[KbReference]]]:
    """Exclude reference writers through the filesystem change and registry commit.

    Callers must restore their filesystem changes if this context raises,
    including a commit failure. The yielded snapshot precedes the mutation.
    """
    with _write() as conn:
        row = conn.execute(
            "SELECT * FROM kb_registry WHERE base_dir = ? AND dir_name = ?",
            (_location(base_dir), name),
        ).fetchone()
        record = _record(row) if row else None
        document = None
        references = []
        if record is not None:
            row = conn.execute(
                "SELECT * FROM kb_documents WHERE kb_id = ? AND rel_path = ?",
                (record.kb_id, source_rel),
            ).fetchone()
            document = _document(row) if row else None
            references = _references_of(conn, record.kb_id)
            if dest_rel is None:
                _mark_document_deleted(conn, record.kb_id, source_rel)
            else:
                _move_document_paths(conn, record.kb_id, source_rel, dest_rel)
        yield record, document, references


def find_document(kb_id: str, rel_path: str) -> KbDocument | None:
    """Return the current path owner, including its tombstone, not displaced history."""
    with _read() as conn:
        if conn is None:
            return None
        row = conn.execute(
            "SELECT * FROM kb_documents WHERE kb_id = ? AND rel_path = ?",
            (_strip_ref(kb_id), rel_path),
        ).fetchone()
        return _document(row) if row else None


def list_documents(kb_id: str, *, include_deleted: bool = False) -> list[KbDocument]:
    """List current path owners, plus displaced tombstones when requested."""
    with _read() as conn:
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT * FROM kb_documents WHERE kb_id = ? AND (? OR deleted = 0) "
            "UNION ALL SELECT * FROM kb_document_history WHERE kb_id = ? AND ? "
            "ORDER BY rel_path, doc_id",
            (_strip_ref(kb_id), include_deleted, _strip_ref(kb_id), include_deleted),
        ).fetchall()
        return [_document(row) for row in rows]


def document_referrers(kb_id: str, doc_id: str) -> list[KbReference]:
    return [ref for ref in list_references(kb_id) if ref.doc_ids is not None and doc_id in ref.doc_ids]


def document_hashes(raw_dir: Path) -> dict[str, str]:
    """Hash raw files with the same POSIX keys as book document fingerprints."""
    root = raw_dir.resolve()
    hashes = {}
    for path in sorted(raw_dir.rglob("*")):
        if not path.is_file() or not path.resolve().is_relative_to(root):
            continue
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256")
        hashes[path.relative_to(raw_dir).as_posix()] = f"sha256:{digest.hexdigest()}"
    return hashes


def refresh_documents(base_dir: Path | str, name: str) -> list[KbDocument]:
    from deeptutor.knowledge.document_mutation import DocumentRecordingError, document_mutation

    try:
        with document_mutation(base_dir, name):
            record = find_by_location(base_dir, name)
            if record is None or is_connector(record):
                return []
            raw_dir = Path(record.base_dir) / record.dir_name / "raw"
            if not raw_dir.is_dir():
                return []
            return sync_documents(record.kb_id, document_hashes(raw_dir))
    except Exception as exc:
        raise DocumentRecordingError(f"Document recording failed for '{name}': {exc}") from exc
