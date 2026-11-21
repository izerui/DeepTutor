"""Inspect and clean reproducible Manim render files without touching published media.

Ported from upstream 38cf3979d. A Math Animator turn writes its final video or
images to ``outputs/math_animator/<session>/<turn>/artifacts`` and keeps the
Manim segments, caches and copies it rendered on the way in the sibling
``media`` folder. That folder can be rebuilt from the source and is often far
larger than the result, so the author may clear it.

Develop decides every storage location itself (spec 2026-10-29, P1c), so unlike
upstream nothing here reports a server path or offers to reveal one.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil

from .models import WorkspaceError
from .service import ContentWorkspaceService


def _render_paths(
    service: ContentWorkspaceService, workspace_id: str, item_id: str
) -> tuple[Path, Path]:
    _, item = service.resolve_published_item(workspace_id, item_id)
    parts = PurePosixPath(item.relative_path).parts
    if (
        not item.generated
        or len(parts) != 6
        or parts[:2] != ("outputs", "math_animator")
        or parts[4] != "artifacts"
        or any(part in {".", ".."} for part in parts)
    ):
        raise WorkspaceError("Only published Math Animator artifacts support render cleanup.")
    binding = service.binding_by_id(workspace_id)
    cursor = binding.root
    for part in parts:
        cursor /= part
        if cursor.is_symlink():
            raise WorkspaceError("Render storage cannot contain symbolic links.")
    artifact = service.resolve(binding, item.relative_path, write=True)
    if not artifact.is_file():
        raise WorkspaceError("The original artifact is unavailable; render files were preserved.")
    return artifact, artifact.parent.parent / "media"


def _inventory(media: Path) -> dict:
    if media.is_symlink():
        raise WorkspaceError("Render storage cannot contain symbolic links.")
    digest = hashlib.sha256()
    size = count = 0
    if media.exists():
        if not media.is_dir():
            raise WorkspaceError("Render storage is not a directory.")
        for parent, directories, files in os.walk(media, followlinks=False):
            for name in sorted(directories + files):
                path = Path(parent) / name
                if path.is_symlink():
                    raise WorkspaceError("Render storage cannot contain symbolic links.")
                stat = path.stat()
                digest.update(
                    f"{path.relative_to(media)}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode()
                )
                if path.is_file():
                    size += stat.st_size
                    count += 1
            directories.sort()
    return {"cache_bytes": size, "cache_files": count, "fingerprint": digest.hexdigest()}


def inspect_render_storage(
    service: ContentWorkspaceService, workspace_id: str, item_id: str
) -> dict:
    """Describe one published artifact and its rebuildable render directory."""
    artifact, media = _render_paths(service, workspace_id, item_id)
    return {"file_bytes": artifact.stat().st_size, **_inventory(media)}


def clean_render_storage(
    service: ContentWorkspaceService, workspace_id: str, item_id: str, fingerprint: str
) -> dict:
    """Delete only the unchanged media directory from an approved preview."""
    artifact, media = _render_paths(service, workspace_id, item_id)
    preview = _inventory(media)
    if preview["fingerprint"] != fingerprint:
        raise WorkspaceError("Render files changed. Review the storage preview again.")
    # media holds segments, font caches and render copies; the final output
    # lives in the sibling artifacts folder and the source next to it.
    if media.exists():
        shutil.rmtree(media)
    return {
        "file_bytes": artifact.stat().st_size,
        "freed_bytes": preview["cache_bytes"],
        "removed_files": preview["cache_files"],
        **_inventory(media),
    }


__all__ = ["clean_render_storage", "inspect_render_storage"]
