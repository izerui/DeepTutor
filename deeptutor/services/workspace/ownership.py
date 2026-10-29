"""Whether a folder may store one account's personal learning data.

Spec 2026-10-29 §4 rule 3: one storage folder belongs to one account. The
deployment content folder (``DEEPTUTOR_WORKSPACE_ROOT``) is every account's
agent-readable folder, so it never becomes anyone's learning workspace.
"""

from __future__ import annotations

import os
from pathlib import Path

SHARED_DEPLOYMENT_FOLDER = (
    "This folder is the deployment's shared content folder and cannot store "
    "personal learning data."
)


def _deployment_root() -> Path | None:
    raw = os.environ.get("DEEPTUTOR_WORKSPACE_ROOT", "").strip()
    return Path(raw).expanduser().resolve() if raw else None


def personal_root_error(root: Path, owner_id: str) -> str:
    """``""`` when *root* may hold *owner_id*'s learning data, else the reason."""
    resolved = Path(root).expanduser().resolve()
    if resolved == _deployment_root():
        return SHARED_DEPLOYMENT_FOLDER
    return ""
