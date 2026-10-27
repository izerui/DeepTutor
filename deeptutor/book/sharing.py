"""Book/KB transitions in one SQLite transaction.

The KB database is main and the book database is attached. Both must use
on-disk DELETE journals and FULL synchronous writes: SQLite's super-journal
then recovers both databases after process death, without compensation that
could undo a different worker's commit. WAL/OFF/MEMORY modes fail closed.

Lock order is KB then book, including local mutexes. Only synchronous local
metadata work belongs inside this boundary; resolve legacy resources before
entry. Never call independent registry writers from the yielded connection.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from pathlib import Path
import sqlite3

from deeptutor.book import registry as books
from deeptutor.core.visibility_intent import record_choice
from deeptutor.knowledge import registry as kbs
from deeptutor.multi_user.context import get_current_user
from deeptutor.multi_user.roles import is_teacher_or_admin


@contextmanager
def shared_write(
    *,
    recovery: AbstractContextManager | None = None,
    attach: Mapping[str, Path] | None = None,
) -> Iterator[sqlite3.Connection]:
    """A bounded, process-safe, crash-atomic transaction over both registries.

    ``attach`` adds further existing databases (schema name -> file) to the
    same transaction, after the two registries in lock order. They are held to
    the same DELETE-journal / FULL-synchronous contract so one COMMIT covers
    every file.
    """
    if getattr(books._writer_state, "active", False):
        raise books.BookRegistryError("Cannot enter sharing from an active book writer.")
    book_path, kb_path = books._db_path().resolve(), kbs._db_path().resolve()
    extra = {name: Path(path).resolve() for name, path in (attach or {}).items()}
    if len({book_path, kb_path, *extra.values()}) != 2 + len(extra):
        raise books.BookRegistryError("Book and KB registries must be distinct files.")
    if set(extra) & {"main", "temp", "books"} or not all(
        name.isidentifier() for name in extra
    ):
        raise books.BookRegistryError("Invalid attached database name.")
    schemas = ("main", "books", *extra)
    with kbs._write_lock, books._write_lock:
        # Initialize schemas sequentially, before either database has a writer.
        kb_path.parent.mkdir(parents=True, exist_ok=True)
        book_path.parent.mkdir(parents=True, exist_ok=True)
        kbs._open(kb_path).close()
        books._open(book_path).close()
        conn = sqlite3.connect(kb_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        books._writer_state.active = True
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("ATTACH DATABASE ? AS books", (str(book_path),))
            for name, path in extra.items():
                conn.execute(f"ATTACH DATABASE ? AS {name}", (str(path),))
            for schema in schemas:
                conn.execute(f"PRAGMA {schema}.synchronous = FULL")
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Restore staged files before ROLLBACK releases the SQLite
                # writers; another process must not see an unrestored live row.
                with recovery if recovery is not None else nullcontext():
                    # Check after locking: another connection cannot change
                    # journal mode between validation and the writes.
                    for schema in schemas:
                        mode = conn.execute(f"PRAGMA {schema}.journal_mode").fetchone()[0]
                        if mode.lower() != "delete":
                            raise books.BookRegistryError(
                                f"Atomic book sharing requires DELETE journals ({schema}: {mode})."
                            )
                    yield conn
                    conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        finally:
            books._writer_state.active = False
            conn.close()


def transfer_ownership(from_user_id: str, to_user_id: str) -> list[books.BookRecord]:
    """Hand over books and active protection without renewing withdrawn access."""
    with shared_write() as conn:
        rows = conn.execute(
            "SELECT * FROM books.book_registry WHERE created_by = ? ORDER BY title, book_id",
            (from_user_id,),
        ).fetchall()
        if from_user_id != to_user_id:
            conn.execute(
                "UPDATE books.book_registry SET created_by = ?, transferred_from = ?, "
                "updated_at = ? WHERE created_by = ?",
                (to_user_id, from_user_id, books._now(), from_user_id),
            )
            # Do not rebuild protection: fixed targets, document limits and
            # historical revocations must survive account deletion unchanged.
            conn.executemany(
                "UPDATE main.kb_references SET created_by = ? "
                "WHERE referrer_kind = 'book' AND referrer_id = ?",
                [(to_user_id, row["book_id"]) for row in rows],
            )
        return [books._target(conn, row["book_id"]) for row in rows]


def _links(conn: sqlite3.Connection, book_id: str) -> dict[str, str]:
    return dict(conn.execute(
        "SELECT ref, kb_id FROM book_kb_links WHERE book_id = ? ORDER BY position", (book_id,),
    ).fetchall())


def _pending_refs(conn: sqlite3.Connection, book_id: str) -> set[str]:
    try:
        return {row[0] for row in conn.execute(
            "SELECT ref FROM book_kb_pending WHERE book_id = ?", (book_id,),
        )}
    except sqlite3.OperationalError as exc:
        # D1 databases have no pending-state table until their first D2 write.
        # Advisory reads must not migrate/create files.
        if str(exc) != "no such table: book_kb_pending":
            raise
        return set()


def _pending(conn: sqlite3.Connection, book_id: str) -> None:
    if _pending_refs(conn, book_id):
        raise books.BookKbResolutionError("Book KB bindings are pending; retry in the owner's scope.")


def _assemble_plan(
    record: books.BookRecord, links: Mapping[str, str], targets: Mapping[str, kbs.KbRecord],
    referenced: frozenset[str],
) -> books.BookKbPlan:
    public, private, issues, missing = [], [], [], []
    seen = set()
    user = get_current_user()
    for ref, kb_id in links.items():
        kb = targets.get(kb_id)
        if kb is None:
            missing.append(ref)
            continue
        if kb_id in seen:
            continue
        seen.add(kb_id)
        if kbs.is_connector(kb):
            issues.append(books.BookKbIssue(
                ref, kb_id, kb.name, "connector",
                "Personal connectors cannot be shared. Upload the material instead.",
            ))
        elif kb.visibility == "public":
            public.append(kb)
        elif kbs.can_write(kb, user):
            private.append(kb)
        else:
            issues.append(books.BookKbIssue(
                ref, kb_id, kb.name, "foreign_private",
                "This knowledge base is another account's private library.",
            ))
    return books.BookKbPlan(
        record.book_id, tuple(public), tuple(private), referenced, tuple(issues), tuple(missing),
    )


def _plan(conn: sqlite3.Connection, record: books.BookRecord) -> books.BookKbPlan:
    _pending(conn, record.book_id)
    links = _links(conn, record.book_id)
    targets = {}
    for kb_id in set(links.values()) - {""}:
        row = conn.execute("SELECT * FROM kb_registry WHERE kb_id = ?", (kb_id,)).fetchone()
        if row is not None:
            targets[kb_id] = kbs._record(row)
    referenced = frozenset(row[0] for row in conn.execute(
        "SELECT kb_id FROM kb_references WHERE referrer_kind = ? AND referrer_id = ?",
        (books.KB_REFERRER_KIND, record.book_id),
    ))
    return _assemble_plan(record, links, targets, referenced)


def plan(book_id: str) -> books.BookKbPlan:
    """Advisory read; mutations recompute the plan under the joint transaction."""
    with books._read() as conn:
        if conn is None:
            raise books.BookRegistryError(f"Book {book_id} is not registered.")
        record = books._target(conn, book_id)
        _pending(conn, record.book_id)
        links = _links(conn, record.book_id)
    targets = {}
    for kb_id in set(links.values()) - {""}:
        kb = kbs.get_kb(kb_id)
        if kb is not None:
            targets[kb_id] = kb
    referenced = frozenset(kb.kb_id for kb in kbs.list_referenced("book", record.book_id))
    return _assemble_plan(record, links, targets, referenced)


def _authorize(conn: sqlite3.Connection, record: books.BookRecord, *, force: bool) -> None:
    user = get_current_user()
    if not books._can_write(conn, record, user):
        raise books.BookVisibilityError("Only the book owner or an admin may change sharing.")
    if force and not user.is_admin:
        raise books.BookVisibilityError("Only admins may force book sharing.")


def _choices(kb_choices: Mapping[str, str] | None) -> dict[str, str]:
    choices = dict(kb_choices or {})
    if any(value not in books.KB_CHOICES for value in choices.values()):
        raise books.BookVisibilityError("Unknown knowledge-base choice.")
    return choices


def _apply_choices(
    conn: sqlite3.Connection, record: books.BookRecord, choices: Mapping[str, str], *, force: bool,
) -> None:
    planned = _plan(conn, record)
    if planned.issues and not force:
        raise books.BookVisibilityError(
            "; ".join(f"{issue.name}: {issue.message}" for issue in planned.issues)
        )
    publish = [kb for kb in planned.private_kbs if choices.get(kb.kb_id) == "make_public"]
    if publish and not is_teacher_or_admin():
        raise books.BookVisibilityError("Only teachers and admins can make knowledge bases public.")
    for kb in planned.private_kbs:
        if choices.get(kb.kb_id) == "reference_only":
            # Only an explicit canonical choice supersedes pending legacy grants.
            record_choice(conn, kb.kb_id, "private", schema="main")
    for kb in publish:
        # The plan was built from this locked connection and checked actor/kind.
        conn.execute(
            "UPDATE kb_registry SET visibility = 'public', updated_at = ? WHERE kb_id = ?",
            (kbs._now(), kb.kb_id),
        )
        record_choice(conn, kb.kb_id, "public", schema="main")


def sync_protection(
    conn: sqlite3.Connection, book_id: str, *, force: bool = False, refresh: bool = True,
) -> None:
    """Derive protection from fresh shared state; all cleanup rolls back on error."""
    record = books._get_book(conn, book_id)
    shared = record is not None and (
        record.visibility == "public" or books._references_of(conn, book_id)
    )
    if shared and not refresh:
        # Removing/reusing a consumer is not new consent to restore a KB that
        # its owner has withdrawn, or to share a previously skipped connector.
        return
    targets = []
    if shared:
        planned = _plan(conn, record)
        # Already protected private sources remain usable by authorized readers
        # adding a second book reference. New private sources still need consent.
        blocked = [
            issue for issue in planned.issues
            if issue.code == "connector" or issue.kb_id not in planned.referenced_kb_ids
        ]
        if blocked and not force:
            raise books.BookReferenceError(
                "; ".join(f"{issue.name}: {issue.message}" for issue in blocked)
            )
        allowed = {kb.kb_id for kb in (*planned.public_kbs, *planned.private_kbs)}
        allowed.update(
            issue.kb_id for issue in planned.issues
            if issue.code == "foreign_private" and issue.kb_id in planned.referenced_kb_ids
        )
        targets = [kbs._reference_target(conn, kb_id, "book") for kb_id in sorted(allowed)]
    conn.execute(
        "DELETE FROM kb_references WHERE referrer_kind = 'book' AND referrer_id = ?", (book_id,),
    )
    # Keep unavailable evidence for still-bound sources; an unrelated publication
    # must not erase a previous admin withdrawal/deletion notice.
    restored = {kb.kb_id for kb in targets}
    revoked = conn.execute(
        "SELECT kb_id FROM kb_revoked_references WHERE referrer_kind = 'book' AND referrer_id = ?",
        (book_id,),
    ).fetchall()
    retained = set(_links(conn, book_id).values()) if shared else set()
    for row in revoked:
        if row["kb_id"] in restored or row["kb_id"] not in retained:
            conn.execute(
                "DELETE FROM kb_revoked_references WHERE referrer_kind = 'book' "
                "AND referrer_id = ? AND kb_id = ?", (book_id, row["kb_id"]),
            )
    if record is not None:
        conn.executemany(
            "INSERT INTO kb_references "
            "(kb_id, referrer_kind, referrer_id, doc_ids, created_by, created_at) "
            "VALUES (?, 'book', ?, NULL, ?, ?)",
            [(kb.kb_id, book_id, record.created_by, kbs._now()) for kb in targets],
        )


def share(
    book_id: str, kb_choices: Mapping[str, str] | None, *, force: bool,
) -> books.BookKbPlan:
    choices = _choices(kb_choices)
    with shared_write() as conn:
        record = books._target(conn, book_id)
        _authorize(conn, record, force=force)
        _apply_choices(conn, record, choices, force=force)
        sync_protection(conn, record.book_id, force=force)
        return _plan(conn, record)


def set_visibility(
    book_id: str, visibility: str, *, force: bool, kb_choices: Mapping[str, str] | None,
) -> books.BookRecord:
    with shared_write() as conn:
        return change_visibility(conn, books._target(conn, book_id), visibility,
                                 force=force, kb_choices=kb_choices)


def change_visibility(
    conn: sqlite3.Connection, record: books.BookRecord, visibility: str, *,
    force: bool, kb_choices: Mapping[str, str] | None,
) -> books.BookRecord:
    """Connection-taking transition for callers retaining a bound target."""
    if visibility not in books.VISIBILITIES:
        raise books.BookVisibilityError("Visibility must be 'public' or 'private'.")
    choices = _choices(kb_choices)
    _authorize(conn, record, force=force)
    record = books._target(conn, record.book_id)
    if visibility == "public":
        if not is_teacher_or_admin():
            raise books.BookVisibilityError("Only teachers and admins can publish books.")
        _apply_choices(conn, record, choices, force=force)
    else:
        foreign = [
            ref for ref in books._references_of(conn, record.book_id)
            if ref.created_by != record.created_by
        ]
        if foreign and not force:
            raise books.BookVisibilityError("Still referenced by other accounts.")
        books._revoke(conn, record, foreign, reason="made_private")
    conn.execute(
        "UPDATE book_registry SET visibility = ?, updated_at = ? WHERE book_id = ?",
        (visibility, books._now(), record.book_id),
    )
    record_choice(conn, record.book_id, visibility, schema="books")
    sync_protection(conn, record.book_id, force=force, refresh=visibility == "public")
    return books._target(conn, record.book_id)


def _previous(conn: sqlite3.Connection, kind: str, referrer: str) -> set[str]:
    return {row[0] for row in conn.execute(
        "SELECT book_id FROM book_references WHERE referrer_kind = ? AND referrer_id = ?",
        (kind, referrer),
    )}


def replace_references(
    kind: str, referrer: str, book_ids: Sequence[str], *, created_by: str,
    private_intent_book_ids: Sequence[str] = (),
) -> None:
    """``private_intent_book_ids``: explicit share-by-reference choices.

    Each one that is also a target, still private and writable by the actor
    records durable Book private intent in this same transaction. Other keys,
    public books and defaults record nothing.
    """
    explicit = {books.strip_ref(value) for value in private_intent_book_ids}
    with shared_write() as conn:
        targets = {books._target(conn, value).book_id for value in book_ids}
        user = get_current_user()
        newly_shared = set()
        for book_id in targets:
            record = books._target(conn, book_id)
            if not books._can_read(conn, record, user):
                raise books.BookReferenceError("The current actor cannot read this book.")
            if record.visibility != "public" and not books._references_of(conn, book_id):
                newly_shared.add(book_id)
        previous = _previous(conn, kind, referrer)
        books._replace_references(conn, kind, referrer, sorted(targets), created_by=created_by)
        for book_id in sorted(targets & explicit):
            record = books._target(conn, book_id)
            if record.visibility == "private" and books._can_write(conn, record, user):
                record_choice(conn, book_id, "private", schema="books")
        for book_id in sorted(previous | targets):
            sync_protection(conn, book_id, refresh=book_id in newly_shared)


def remove_references(kind: str, referrer: str) -> None:
    with shared_write() as conn:
        previous = _previous(conn, kind, referrer)
        books._clear_referrer(conn, kind, referrer)
        for book_id in sorted(previous):
            sync_protection(conn, book_id, refresh=False)


def unregister(book_id: str) -> None:
    with shared_write() as conn:
        books._unregister(conn, book_id)
        sync_protection(conn, books.strip_ref(book_id))


def initialize_links(
    book_id: str, refs: Sequence[str], *, confirmed_missing: Sequence[str],
) -> bool:
    from deeptutor.services.path_service import get_path_service

    record = books.get_book(book_id)
    user = get_current_user()
    if record is None:
        raise books.BookRegistryError(f"Book {book_id} is not registered.")
    if user.id != record.created_by or books._location(get_path_service().get_book_dir()) != record.base_dir:
        raise books.BookKbResolutionError("Resolve KB links in the canonical owner's book scope.")
    # Resolution may touch local catalogs. Never retain global writers here.
    with books._read() as conn:
        if conn is None:
            raise books.BookKbResolutionError("Book disappeared during binding.")
        old = _links(conn, record.book_id)
        prior_pending = _pending_refs(conn, record.book_id)
    links = dict(old)
    pending = set()
    confirmed = set(confirmed_missing)
    for ref in dict.fromkeys([*refs, *sorted(prior_pending)]):
        if ref in old:
            continue
        try:
            kb_id = books.resolve_kb_links([ref])[ref]
        except books.BookKbResolutionError:
            pending.add(ref)
            continue
        if kb_id or ref.startswith(kbs.REF_PREFIX) or ref in confirmed:
            links[ref] = kb_id
        else:
            pending.add(ref)
    with shared_write() as conn:
        current = books._target(conn, book_id)
        current_pending = _pending_refs(conn, record.book_id)
        if current != record or _links(conn, record.book_id) != old or current_pending != prior_pending:
            raise books.BookKbResolutionError("Book changed during binding; retry in its owner scope.")
        books._set_kb_links(conn, record.book_id, links)
        conn.execute("DELETE FROM book_kb_pending WHERE book_id = ?", (record.book_id,))
        conn.executemany(
            "INSERT INTO book_kb_pending VALUES (?, ?)", [(record.book_id, ref) for ref in sorted(pending)],
        )
        conn.execute(
            "INSERT OR REPLACE INTO book_kb_binding_state VALUES (?, ?)",
            (record.book_id, int(not pending)),
        )
        if not pending:
            sync_protection(conn, record.book_id)
    return not pending
