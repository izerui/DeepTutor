"""Whether anyone may sign up (spec 2026-10-29 D19).

Public registration is open by default and creates student accounts. An
administrator can close it; while closed, only a valid class invite link
admits a new account. Stored in ``<SYSTEM_ROOT>/auth/registration.json``.
"""

from __future__ import annotations

import json
from pathlib import Path


def _settings_file() -> Path:
    from . import paths

    return paths.SYSTEM_ROOT / "auth" / "registration.json"


def public_registration_open() -> bool:
    try:
        value = json.loads(_settings_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        # An unreadable setting fails closed: no anonymous account creation.
        return False
    return bool(value.get("public", True)) if isinstance(value, dict) else False


def set_public_registration(open_: bool) -> bool:
    from deeptutor.utils.secret_files import ensure_private_directory

    path = _settings_file()
    ensure_private_directory(path.parent)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"public": bool(open_)}), encoding="utf-8")
    temporary.replace(path)
    return bool(open_)


__all__ = ["public_registration_open", "set_public_registration"]
