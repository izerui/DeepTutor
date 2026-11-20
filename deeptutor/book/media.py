"""Files a block plays, published with the book (spec 2026-11-20 §6).

A generated video lesson or animation used to be linked from the book by its
``/files/outputs/...`` URL. That URL resolves inside the *requesting* account's
workspace, so a learner who joined a shared or listed book could see the block
but never play it: the file lived in the author's private outputs.

Blocks now carry their media inside the book itself::

    book_{id}/assets/blocks/{block_id}/{name}

and the reader fetches it through the book's own read check
(``GET /api/books/{book}/pages/{page}/blocks/{block}/media/{name}``). Only the
names a block lists in ``payload["media"]`` are served, and only while that
block is on that page. Older animation blocks that still point at the
generating account's outputs are served from the book owner's workspace, but
only for the artifact folder the book generator itself writes
(``workspace/chat/math_animator/book-{book}-{block}/``).
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .models import Block, BlockType, Page

if TYPE_CHECKING:
    from .storage import BookStorage

#: ``payload`` key listing the file names published for a block.
MEDIA_KEY = "media"
#: A lesson page carries its own player, script and narration: a few MB.
MAX_MEDIA_BYTES = 200 * 1024 * 1024
#: The video lesson page name, as the chat pipeline writes it.
LESSON_PAGE = "lesson.html"

OUTPUTS_PREFIX = "/files/outputs/"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class BookMediaError(ValueError):
    """A media name, id or source that may not be published or served."""


def safe_name(name: str) -> str:
    name = str(name or "")
    if not _SAFE_NAME.match(name) or ".." in name:
        raise BookMediaError("Invalid media name.")
    return name


def _safe_id(value: str) -> str:
    value = str(value or "")
    if not _SAFE_ID.match(value):
        raise BookMediaError("Invalid id.")
    return value


def block_media_dir(storage: BookStorage, book_id: str, block_id: str) -> Path:
    assets = storage.path_service.get_book_assets_dir(_safe_id(book_id))
    return assets / "blocks" / _safe_id(block_id)


def media_url(book_id: str, page_id: str, block_id: str, name: str) -> str:
    """The reader URL of one published file (relative to the API base)."""
    parts = (book_id, page_id, block_id, safe_name(name))
    book, page, block, file_name = (quote(str(part), safe="") for part in parts)
    return f"/api/books/{book}/pages/{page}/blocks/{block}/media/{file_name}"


def is_video_lesson_page(path: Path) -> bool:
    return path.name == LESSON_PAGE


def publish_file(
    storage: BookStorage, book_id: str, block_id: str, source: Path, name: str,
) -> Path:
    """Copy ``source`` into the book as ``name`` of ``block_id`` (atomic replace)."""
    name = safe_name(name)
    source = Path(source)
    if not source.is_file() or source.is_symlink():
        raise BookMediaError("The file to publish is missing.")
    if source.stat().st_size > MAX_MEDIA_BYTES:
        raise BookMediaError("The file is too large to publish with the book.")
    target_dir = block_media_dir(storage, book_id, block_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{name}.", dir=target_dir)
    os.close(fd)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target_dir / name)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return target_dir / name


def published_names(block: Block) -> list[str]:
    raw = (block.payload or {}).get(MEDIA_KEY)
    if not isinstance(raw, list):
        return []
    names = []
    for value in raw:
        try:
            names.append(safe_name(str(value)))
        except BookMediaError:
            continue
    return names


def _legacy_animation_source(
    storage: BookStorage, book_id: str, block: Block, name: str,
) -> Path | None:
    """An older animation block's artifact, from its book owner's outputs only."""
    payload = block.payload or {}
    urls = [str(payload.get("video_url") or "")]
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, list):
        urls.extend(str(item.get("url") or "") for item in artifacts if isinstance(item, dict))
    turn = f"book-{book_id}-{block.id}"
    for url in urls:
        if not url.startswith(OUTPUTS_PREFIX):
            continue
        relative = PurePosixPath(url[len(OUTPUTS_PREFIX):])
        parts = relative.parts
        if (
            len(parts) == 6
            and parts[:4] == ("workspace", "chat", "math_animator", turn)
            and parts[4] == "artifacts"
            and parts[5] == name
        ):
            return storage.path_service.resolve_public_output_path(str(relative))
    return None


def resolve_block_media(
    storage: BookStorage, book_id: str, page: Page | None, block_id: str, name: str,
) -> Path | None:
    """The file ``name`` of block ``block_id`` on ``page``, or ``None`` (serve 404)."""
    if page is None:
        return None
    try:
        name = safe_name(name)
        directory = block_media_dir(storage, book_id, block_id)
    except BookMediaError:
        return None
    block = page.block_by_id(block_id)
    if block is None:
        return None
    if name in published_names(block):
        candidate = directory / name
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(directory.resolve(strict=True))
        except (OSError, ValueError):
            return None
        return resolved if resolved.is_file() else None
    if block.type == BlockType.ANIMATION:
        return _legacy_animation_source(storage, book_id, block, name)
    return None


def publish_animation_artifacts(
    storage: BookStorage, book_id: str, page_id: str, block_id: str,
    artifacts: list[dict[str, Any]], generator_root: Any,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Copy a generated animation's artifacts into the book and point at them.

    ``generator_root`` is the path service the generator wrote with. An artifact
    that cannot be found there keeps its old URL (still playable by its author).
    """
    published: list[str] = []
    rewritten: list[dict[str, Any]] = []
    for artifact in artifacts:
        item = dict(artifact)
        url = str(item.get("url") or "")
        source = None
        if url.startswith(OUTPUTS_PREFIX):
            source = generator_root.resolve_public_output_path(url[len(OUTPUTS_PREFIX):])
        if source is not None:
            try:
                name = safe_name(Path(source).name)
                publish_file(storage, book_id, block_id, Path(source), name)
            except BookMediaError:
                pass
            else:
                published.append(name)
                item["url"] = media_url(book_id, page_id, block_id, name)
        rewritten.append(item)
    return rewritten, published


__all__ = [
    "BookMediaError",
    "LESSON_PAGE",
    "MEDIA_KEY",
    "OUTPUTS_PREFIX",
    "block_media_dir",
    "is_video_lesson_page",
    "media_url",
    "publish_animation_artifacts",
    "publish_file",
    "published_names",
    "resolve_block_media",
    "safe_name",
]
