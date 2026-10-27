"""Teaching's only door into the book registry (plan D).

A teacher path cites books like it cites knowledge bases (spec 7.8): public
books pass, the teacher's own private books are made public or shared by
reference, other accounts' private books cannot be cited.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from deeptutor.book import registry as book_registry

REFERRER_KIND = "mastery_template"
BookRegistryError = book_registry.BookRegistryError
BookVisibilityError = book_registry.BookVisibilityError
BookReferenceError = book_registry.BookReferenceError


class BookCollisionError(BookRegistryError):
    """A bare id names a local-only book that collides with another registered book."""


DependencyReason = Literal["missing", "revoked", "not_shared", "connector", "pending"]


@dataclass(frozen=True)
class BookDependency:
    """A knowledge base a book is bound to that its readers cannot currently use."""

    book_id: str
    ref: str
    kb_id: str
    name: str
    reason: DependencyReason


def resolve(ref: str) -> Any | None:
    """The registered book a source names, in the current (owner's) scope.

    ``book:<id>`` always binds the registered identity, even when a local
    import uses the same id. A bare id is a legacy personal source: it names
    the book in the current local storage when one exists there, and is
    registered on first sight under the current account (plan D). A local
    book whose id belongs to a different registered book cannot be published
    (:class:`BookCollisionError`); it is never silently swapped for that book.
    """
    value = str(ref or "").strip()
    if not value:
        return None
    if value.startswith(book_registry.REF_PREFIX):
        return book_registry.resolve_book_ref(value)
    from deeptutor.book.storage import get_book_storage
    from deeptutor.multi_user.book_access import register_current_scope_book

    book_id = book_registry.strip_ref(value)
    storage = get_book_storage()
    if not storage.book_exists(book_id):
        return book_registry.resolve_book_ref(value)
    record = register_current_scope_book(book_id)
    location = str(storage.path_service.get_book_dir().resolve())
    if record is None or record.base_dir != location:
        raise BookCollisionError(
            f"Book {book_id} in this workspace shares its id with another registered book."
        )
    return record


def get(book_id: str) -> Any | None:
    return book_registry.get_book(book_id)


def can_read(record: Any, user: Any) -> bool:
    return bool(book_registry.can_read_book(record, user))


def can_write(record: Any, user: Any) -> bool:
    return bool(book_registry.can_write_book(record, user))


def plan_kbs(book_id: str) -> Any:
    return book_registry.plan_book_kbs(book_id)


def share_kbs(book_id: str, kb_choices: Mapping[str, str]) -> Any:
    """Make the book's own KBs readable by its readers, following ``kb_choices``.

    Plan D records explicit ``reference_only`` / ``make_public`` KB intent in
    the same transaction; pass only the publisher's explicit choices.
    """
    return book_registry.share_book_kbs(book_id, dict(kb_choices))


def make_public(book_id: str, kb_choices: Mapping[str, str]) -> Any:
    return book_registry.set_book_visibility(book_id, "public", kb_choices=dict(kb_choices))


def book_kb_ids(book_id: str) -> list[str]:
    """Every knowledge base this book is bound to (plan D's fixed links).

    Read from the book's bindings rather than its live ``"book"`` references,
    so a reference an admin revoked is still listed (and reported unavailable
    by :func:`unavailable_dependencies`) instead of silently disappearing.
    Links that never resolved to a KB have no id and are reported there too.
    """
    return list(
        dict.fromkeys(kb_id for kb_id in book_registry.book_kb_links(book_id).values() if kb_id)
    )


def unavailable_dependencies(book_id: str) -> list[BookDependency]:
    """The book's bound KBs its readers cannot use right now (spec 7.4).

    A bound KB is usable when it is public (and not a connector) or protected
    by the book's own reference. Everything else — a missing or unresolved
    link, an admin-revoked reference, a private KB the book never shared, a
    connector, or bindings still pending — is returned. Material checks stop
    on any of these instead of tutoring with a reduced material set.
    """
    from deeptutor.knowledge import registry as kb_registry

    try:
        plan = plan_kbs(book_id)
    except book_registry.BookKbResolutionError:
        return [BookDependency(book_id, "", "", "", "pending")]
    links = book_registry.book_kb_links(book_id)
    revoked = {
        row.kb_id
        for row in kb_registry.list_unavailable_references(book_registry.KB_REFERRER_KIND, book_id)
    }
    issues: list[BookDependency] = [
        BookDependency(book_id, ref, links.get(ref, ""), "", "missing") for ref in plan.missing
    ]
    for issue in plan.issues:
        if issue.code == "connector":
            issues.append(BookDependency(book_id, issue.ref, issue.kb_id, issue.name, "connector"))
        elif issue.kb_id not in plan.referenced_kb_ids:
            reason: DependencyReason = "revoked" if issue.kb_id in revoked else "not_shared"
            issues.append(BookDependency(book_id, issue.ref, issue.kb_id, issue.name, reason))
    refs = {kb_id: ref for ref, kb_id in links.items() if kb_id}
    for kb in plan.private_kbs:
        if kb.kb_id not in plan.referenced_kb_ids:
            reason = "revoked" if kb.kb_id in revoked else "not_shared"
            issues.append(BookDependency(book_id, refs.get(kb.kb_id, ""), kb.kb_id, kb.name, reason))
    return issues


def replace_template_references(
    template_id: str,
    book_ids: Sequence[str],
    *,
    created_by: str,
    explicit_reference_only: Sequence[str] = (),
    restore_book_ids: Sequence[str] = (),
    restorable: Callable[[Any], bool] | None = None,
) -> None:
    """Declare the books the template cites (plan D references and KB protection).

    ``explicit_reference_only`` holds only the book ids the publisher
    explicitly mapped to ``reference_only``: plan D records their durable
    private intent in the same transaction when they are still private and
    writable. Defaults and earlier choices are not consent.
    ``restore_book_ids`` / ``restorable``: released books asked back, each kept
    only if ``restorable`` accepts its fresh record inside plan D's writer.
    """
    options: dict[str, Any] = {}
    if explicit_reference_only:
        options["private_intent_book_ids"] = list(explicit_reference_only)
    if restore_book_ids:
        options["restore_book_ids"] = list(restore_book_ids)
        options["restorable"] = restorable
    book_registry.replace_book_references(
        REFERRER_KIND, template_id, list(dict.fromkeys(book_ids)), created_by=created_by, **options
    )


def remove_template_references(template_id: str) -> None:
    book_registry.remove_book_references(REFERRER_KIND, template_id)


def referenced_book_ids(template_id: str) -> set[str]:
    """The books the template's live references hold now (revoked ones excluded)."""
    return {
        record.book_id
        for record in book_registry.list_referenced_books(REFERRER_KIND, template_id)
    }


def unavailable_references(template_id: str) -> list[Any]:
    """Book references an admin revoked (forced private or deleted the book)."""
    return list(book_registry.list_unavailable_book_references(REFERRER_KIND, template_id))
