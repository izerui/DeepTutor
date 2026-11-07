"""Logical resource grants for non-admin users."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sys
import threading
from typing import Any

from deeptutor.services.file_io import atomic_write_text

from .identity import get_user_by_id
from .paths import SYSTEM_ROOT, ensure_system_dirs

GRANTS_DIR = SYSTEM_ROOT / "grants"

_GRANT_THREAD_LOCKS: dict[Path, threading.Lock] = {}
_GRANT_THREAD_LOCKS_GUARD = threading.Lock()


class GrantStorageError(RuntimeError):
    """An existing grant cannot be read safely."""


@contextmanager
def _grant_write_lock(path: Path):
    """Serialize cooperative grant writers within and across processes."""
    with _GRANT_THREAD_LOCKS_GUARD:
        thread_lock = _GRANT_THREAD_LOCKS.setdefault(path, threading.Lock())
    with thread_lock:
        lock_path = path.with_name(f".{path.name}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if sys.platform == "win32":  # pragma: no cover - covered by platform simulation
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                handle.seek(0)
                if sys.platform == "win32":  # pragma: no cover - covered by platform simulation
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def empty_grant(user_id: str) -> dict[str, Any]:
    return {
        "version": 2,
        "user_id": user_id,
        "models": {"llm": []},
        # Retired: KBs are shared by making them public. Keep this field
        # round-trippable so existing grants retain their legacy payload.
        "knowledge_bases": [],
        "skills": [],
        # Partners an admin has lent this user. People build their own partners
        # now, so a grant is only about someone *else's*: it lets the user talk
        # to the named partners — never configure them — and their side of each
        # conversation stays private to their account. Same shape as ``skills``
        # (``[{"partner_id": ...}]``).
        "partners": [],
        # Tool whitelists share the partner-config semantics for built-ins:
        # ``enabled_tools=None`` means "default" (every tool in the pool),
        # ``[]`` means none, a list is an explicit whitelist. MCP tools can
        # proxy host-side capabilities, so non-admin runtime access treats
        # ``mcp_tools=None`` as deny-by-default until an admin grants explicit
        # names. ``cli_apps`` is the same posture for installed CLI apps, keyed
        # by app id: each one is third-party code executing in the sandbox, so
        # an absent grant is no access rather than all of them.
        # ``exec_enabled`` is a tri-state override on top of the
        # deployment exec policy: ``None`` follows the policy, ``False`` always
        # denies, ``True`` is only honored where the sandbox can actually
        # isolate users (SYSTEM isolation).
        "enabled_tools": None,
        "mcp_tools": None,
        "cli_apps": None,
        "exec_enabled": None,
        # ``learning_policy`` (learning restrictions) was retired on
        # 2026-11-06: a stored value is never read, and is dropped the next
        # time an admin saves this grant.
        # The learner's own daily AI tokens (batch 3 §8); ``None`` follows the
        # school default, ``0`` is no limit.
        "ai_daily_tokens": None,
    }


def _normalize_tool_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        return None
    return [str(item).strip() for item in value if str(item).strip()]


def grant_path(user_id: str) -> Path:
    ensure_system_dirs()
    return GRANTS_DIR / f"{user_id}.json"


def normalize_grant(user_id: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    """Coerce any stored/submitted grant payload into the v2 shape.

    v1 grants normalize losslessly for everything that was ever enforced:
    ``models.embedding`` / ``models.search`` / ``spaces`` had no runtime
    consumers and are dropped; absent v2 fields default to unrestricted.
    """
    base = empty_grant(user_id)
    if not isinstance(payload, dict):
        return base
    base["user_id"] = user_id
    models = payload.get("models") if isinstance(payload.get("models"), dict) else {}
    items = models.get("llm") if isinstance(models, dict) else []
    if not isinstance(items, list):
        items = []
    base["models"]["llm"] = [dict(item) for item in items if isinstance(item, dict)]
    for key in ("knowledge_bases", "skills", "partners"):
        # Read once, then narrow. Two separate ``.get`` calls cannot be narrowed
        # together — nothing promises they return the same object — so the
        # inline-conditional form left this iterating a possible ``None``.
        raw = payload.get(key)
        values = raw if isinstance(raw, list) else []
        base[key] = [dict(item) for item in values if isinstance(item, dict)]
    for key in ("enabled_tools", "mcp_tools", "cli_apps"):
        base[key] = _normalize_tool_list(payload.get(key))
    exec_enabled = payload.get("exec_enabled")
    base["exec_enabled"] = bool(exec_enabled) if isinstance(exec_enabled, bool) else None
    from .roles import AI_DAILY_TOKENS, normalize_ai_daily_tokens

    tokens = normalize_ai_daily_tokens(payload.get(AI_DAILY_TOKENS))
    legacy = payload.get("student_policy")
    if tokens is None and isinstance(legacy, dict):
        # Spec 2026-11-05 §3: the retired learner policy is no longer read,
        # except the personal AI limit it used to carry.
        tokens = normalize_ai_daily_tokens(legacy.get(AI_DAILY_TOKENS))
    base[AI_DAILY_TOKENS] = tokens
    return base


def load_grant(user_id: str) -> dict[str, Any]:
    path = grant_path(user_id)
    if not path.exists():
        return empty_grant(user_id)
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GrantStorageError(f"Cannot read grant for user {user_id}") from exc
    if not isinstance(stored, dict):
        raise GrantStorageError(f"Grant for user {user_id} must be a JSON object")
    for key in ("enabled_tools", "mcp_tools", "cli_apps"):
        value = stored.get(key)
        if value is not None and not isinstance(value, list):
            raise GrantStorageError(f"Grant for user {user_id} has invalid {key}")
    if stored.get("exec_enabled") is not None and not isinstance(stored["exec_enabled"], bool):
        raise GrantStorageError(f"Grant for user {user_id} has invalid exec_enabled")
    grant = normalize_grant(user_id, stored)
    try:
        validate_grant(grant)
    except ValueError as exc:
        raise GrantStorageError(f"Grant for user {user_id} is invalid") from exc
    return grant


def _validated_grant(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    user_record = get_user_by_id(user_id)
    if user_record is None:
        raise ValueError(f"Unknown user id: {user_id}")
    _username, record = user_record
    if str(record.get("role") or "user") == "admin":
        raise ValueError("Admin users use the main workspace and cannot receive assignments.")
    grant = normalize_grant(user_id, payload)
    validate_grant(grant)
    return grant


def save_grant(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    grant = _validated_grant(user_id, payload)
    path = grant_path(user_id)
    with _grant_write_lock(path):
        atomic_write_text(path, json.dumps(grant, indent=2, ensure_ascii=False))
    return grant


def validate_grant(grant: dict[str, Any]) -> None:
    """Reject accidental secret/path material in grants.

    Grants carry logical ids only. Runtime resolution happens server-side.
    """
    forbidden = {"api_key", "secret", "password", "token", "path", "base_url"}

    def walk(value: Any, trail: str = "grant") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                lowered = str(key).lower()
                if lowered in forbidden or lowered.endswith("_key"):
                    raise ValueError(f"Grants must not contain secret/path field: {trail}.{key}")
                walk(child, f"{trail}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{trail}[{index}]")

    walk(grant)


def public_grant(user_id: str) -> dict[str, Any]:
    return deepcopy(load_grant(user_id))
