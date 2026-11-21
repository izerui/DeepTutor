"""Remove the files a conversation generated when the conversation is deleted.

A chat turn writes into its content workspace at
``outputs/<capability>/<session>/<turn>/`` (exec and CLI results, generated
media, Math Animator renders, visualisations, audio overviews) and publishes
the files it shows as private snapshots (``workspace_presentations``). A few
pipelines still use the older per-turn folders under
``workspace/chat/<feature>/<turn>/`` and link them as ``/files/outputs/...``;
video lessons always do.

Deleting a conversation deletes all of these, so its generated files go with
it instead of piling up in the account. Book media is unaffected: a block that
plays a generated file carries its own copy inside the book.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path, PurePosixPath
import re
import shutil
from typing import Any, Iterable
from urllib.parse import unquote

from .models import WorkspaceBinding, WorkspaceError
from .service import ContentWorkspaceService, session_output_component

logger = logging.getLogger(__name__)

_OUTPUT_URL = re.compile(r"/files/outputs/([^\s\"'<>\\)\]?#]+)")
# Legacy per-turn folders that hold a conversation's generated files.
_LEGACY_FEATURES = frozenset(
    {"chat", "deep_solve", "deep_question", "deep_research", "math_animator", "video_lesson"}
)
_SAFE_DIR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_INTERNAL_DIR = ".deeptutor"


def legacy_output_dirs(messages: Iterable[Any]) -> set[tuple[str, str]]:
    """``(feature, turn folder)`` pairs the messages link under ``/files/outputs``.

    Only folders a message of this conversation points at are returned, so a
    folder another conversation or a book wrote is never a candidate.
    """
    text = json.dumps(list(messages), ensure_ascii=False, default=str)
    found: set[tuple[str, str]] = set()
    for match in _OUTPUT_URL.findall(text):
        parts = PurePosixPath(unquote(match)).parts
        if (
            len(parts) >= 5
            and parts[:2] == ("workspace", "chat")
            and parts[2] in _LEGACY_FEATURES
            and _SAFE_DIR.match(parts[3])
            and ".." not in parts[3]
            # The book generator renders its animations under book-<book>-<block>.
            and not parts[3].startswith("book-")
        ):
            found.add((parts[2], parts[3]))
    return found


def _candidate_bindings(service: ContentWorkspaceService) -> list[WorkspaceBinding]:
    bindings: dict[str, WorkspaceBinding] = {}
    for resolve in (
        service.general_binding,
        service.current_binding,
        service.default_binding,
    ):
        try:
            binding = resolve()
        except (WorkspaceError, OSError):
            continue
        bindings.setdefault(binding.workspace_id, binding)
    try:
        for binding in service.registered_bindings():
            bindings.setdefault(binding.workspace_id, binding)
    except (WorkspaceError, OSError):
        pass
    return list(bindings.values())


def _remove_tree(path: Path) -> bool:
    if path.is_symlink():
        # Never follow a link out of the workspace; drop only the link.
        path.unlink(missing_ok=True)
        return True
    if path.is_dir():
        shutil.rmtree(path)
        return True
    return False


def _remove_workspace_outputs(binding: WorkspaceBinding, component: str) -> int:
    outputs = binding.root / "outputs"
    if outputs.is_symlink() or not outputs.is_dir():
        return 0
    removed = 0
    for capability in outputs.iterdir():
        if (
            capability.name == _INTERNAL_DIR
            or capability.is_symlink()
            or not capability.is_dir()
        ):
            continue
        if _remove_tree(capability / component):
            removed += 1
    return removed


def _remove_presentations(
    service: ContentWorkspaceService, binding: WorkspaceBinding, component: str
) -> int:
    root = service._presentation_root(binding)
    items = root / "items"
    blobs = root / "blobs"
    if root.is_symlink() or items.is_symlink() or not items.is_dir():
        return 0
    released: set[str] = set()
    kept: set[str] = set()
    removed = 0
    for manifest in items.glob("*.json"):
        if manifest.is_symlink():
            continue
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sha = str(payload.get("sha256") or "")
        parts = PurePosixPath(str(payload.get("relative_path") or "")).parts
        if len(parts) >= 4 and parts[0] == "outputs" and parts[2] == component:
            manifest.unlink(missing_ok=True)
            released.add(sha)
            removed += 1
        else:
            kept.add(sha)
    if not blobs.is_symlink():
        # Snapshots are stored once per content; keep any another file still uses.
        for sha in released - kept:
            if _SHA256.match(sha):
                (blobs / sha).unlink(missing_ok=True)
    return removed


def remove_session_outputs(
    session_id: str, legacy_dirs: Iterable[tuple[str, str]] = ()
) -> dict[str, int]:
    """Delete the generated files of one (already deleted) conversation."""
    from deeptutor.services.path_service import get_path_service
    from deeptutor.services.workspace import get_content_workspace_service

    service = get_content_workspace_service()
    component = session_output_component(session_id)
    folders = snapshots = 0
    for binding in _candidate_bindings(service):
        try:
            folders += _remove_workspace_outputs(binding, component)
            snapshots += _remove_presentations(service, binding, component)
        except (WorkspaceError, OSError):
            logger.exception(
                "could not remove outputs of session %s in workspace %s",
                session_id,
                binding.workspace_id,
            )
    chat_root = get_path_service().get_chat_workspace_root()
    targets = set(legacy_dirs)
    if _SAFE_DIR.match(session_id) and ".." not in session_id:
        # Session-keyed state (e.g. tools a chat loaded) lives beside the turns.
        targets.add(("chat", session_id))
    if not chat_root.is_symlink():
        for feature, name in targets:
            if feature not in _LEGACY_FEATURES or not _SAFE_DIR.match(name) or ".." in name:
                continue
            feature_root = chat_root / feature
            if feature_root.is_symlink():
                continue
            try:
                if _remove_tree(feature_root / name):
                    folders += 1
            except OSError:
                logger.exception("could not remove %s/%s of session %s", feature, name, session_id)
    return {"folders": folders, "snapshots": snapshots}


__all__ = ["legacy_output_dirs", "remove_session_outputs"]
