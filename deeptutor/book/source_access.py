"""Archive admission for a Book's physical storage, independent of its owner."""

from __future__ import annotations

from functools import wraps
from inspect import iscoroutinefunction
from typing import TYPE_CHECKING

from deeptutor.knowledge.registry_scope import source_is_archived
from deeptutor.services.workspace.models import WorkspaceError

if TYPE_CHECKING:
    from .storage import BookStorage


def require_content_writable(storage: BookStorage) -> None:
    paths = storage.path_service
    # The shared helper expects the corresponding KB root, never the Book dir.
    # Managed content stores must still have a catalog entry. Standalone SDK
    # roots retain the KB helper's legacy non-workspace behavior.
    managed = paths.workspace_root.parent.name == ".deeptutor"
    if source_is_archived(paths.get_knowledge_bases_root(), require_resolved=managed):
        raise WorkspaceError("Restore the source workspace before changing this book.")


def content_writable(storage: BookStorage) -> bool:
    try:
        require_content_writable(storage)
    except (WorkspaceError, OSError):
        # Archive uncertainty narrows writes without withdrawing safe reads.
        return False
    return True


def content_writer(function):
    """Check an engine's bound target at admission, including direct callers."""
    if iscoroutinefunction(function):
        @wraps(function)
        async def async_guarded(self, *args, **kwargs):
            require_content_writable(self.storage)
            return await function(self, *args, **kwargs)

        return async_guarded

    @wraps(function)
    def guarded(self, *args, **kwargs):
        require_content_writable(self.storage)
        return function(self, *args, **kwargs)

    return guarded
