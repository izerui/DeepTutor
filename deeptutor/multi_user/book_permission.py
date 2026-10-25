"""Account creation policy and inert shared-shelf evidence for D22 migration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .models import CurrentUser


@dataclass(frozen=True, slots=True)
class BookPermission:
    """Whether an account may create books; reading uses the registry."""

    create: bool = True


# Missing and malformed records preserve the ability to create a personal book.
DEFAULT_BOOK_PERMISSION = BookPermission()
ADMIN_BOOK_PERMISSION = BookPermission(create=True)


def normalize_book_permission(raw: Any) -> BookPermission:
    """Read only create, including the old actions shape."""
    if not isinstance(raw, dict):
        return DEFAULT_BOOK_PERMISSION
    if any(key in raw for key in ("create", "default", "books")):
        return BookPermission(create=raw.get("create", True) is True)
    actions = raw.get("actions") if isinstance(raw.get("actions"), dict) else {}
    return BookPermission(create=actions.get("create", True) is True)


def public_permission_dict(permission: BookPermission) -> dict[str, Any]:
    return {"create": permission.create}


def canonical_book_permission(raw: Any) -> dict[str, Any]:
    return public_permission_dict(normalize_book_permission(raw))


def permission_for_user(user: CurrentUser) -> BookPermission:
    if user.is_admin:
        return ADMIN_BOOK_PERMISSION
    from .identity import get_user

    record = get_user(user.username) or {}
    return normalize_book_permission(record.get("book_permission"))


@dataclass(frozen=True, slots=True)
class LegacyBookShares:
    """Stored shared-shelf levels, consumed only by the D22 migration."""

    default: Literal["none", "read"] = "none"
    books: tuple[tuple[str, Literal["none", "read", "edit"]], ...] = ()

    def level_for(self, book_id: str) -> Literal["none", "read", "edit"]:
        return dict(self.books).get(book_id, self.default)


def legacy_book_shares(raw: Any) -> LegacyBookShares:
    """Read the retired formats independently of the live create permission."""
    if not isinstance(raw, dict):
        return LegacyBookShares()
    # A create-only save may add create to stored mode/actions evidence.
    if any(key in raw for key in ("default", "books")):
        default = raw.get("default")
        default = default if isinstance(default, str) and default in {"none", "read"} else "none"
        entries = raw.get("books")
        books = []
        if isinstance(entries, dict):
            for key, level in entries.items():
                book_id = str(key or "").strip()
                level = "edit" if level == "delete" else level
                if book_id and isinstance(level, str) and level in {"none", "read", "edit"}:
                    books.append((book_id, level))
        return LegacyBookShares(default=default, books=tuple(books))
    if raw.get("mode") == "all":
        return LegacyBookShares(default="read")
    ids = raw.get("book_ids")
    if raw.get("mode") == "list" and isinstance(ids, (list, tuple, set)):
        actions = raw.get("actions")
        actions = actions if isinstance(actions, dict) else {}
        level = "edit" if actions.get("edit") is True or actions.get("delete") is True else "read"
        return LegacyBookShares(books=tuple(
            (book_id, level) for book_id in dict.fromkeys(str(item or "").strip() for item in ids)
            if book_id
        ))
    return LegacyBookShares()


__all__ = [
    "ADMIN_BOOK_PERMISSION",
    "DEFAULT_BOOK_PERMISSION",
    "BookPermission",
    "LegacyBookShares",
    "legacy_book_shares",
    "canonical_book_permission",
    "normalize_book_permission",
    "permission_for_user",
    "public_permission_dict",
]
