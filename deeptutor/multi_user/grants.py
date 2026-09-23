"""Logical resource grants for non-admin users."""

from __future__ import annotations

from contextlib import suppress
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any

from .identity import get_user_by_id
from .paths import SYSTEM_ROOT, ensure_system_dirs

GRANTS_DIR = SYSTEM_ROOT / "grants"

_grant_locks: dict[str, threading.Lock] = {}
_grant_locks_guard = threading.Lock()


def _grant_lock(user_id: str) -> threading.Lock:
    """Per-user in-process lock serializing save_grant and migrate_learner_grant.

    Only serializes threads within a single Python process. Multi-worker or
    multi-process deployments sharing the same grant directory require a
    filesystem lock (e.g. fcntl.flock) for cross-process safety.
    """
    with _grant_locks_guard:
        lock = _grant_locks.get(user_id)
        if lock is None:
            lock = threading.Lock()
            _grant_locks[user_id] = lock
        return lock


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.stem}.", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(path))
    except Exception:
        with suppress(OSError):
            os.unlink(tmp_path)
        raise

LEARNING_CAPABILITIES = {"chat", "immersive_reading", "mastery_path", "immersive_watching"}
LEARNING_AGE_BANDS = {"6-8", "9-12", "13-15"}
LEARNING_PERSONAS = {"teacher", "peer", "research-assistant"}
LEARNING_SURFACES = {
    "chat", "reading", "mastery", "books", "watching",
    "partners", "agents", "writing", "notebook", "dashboard",
    "personas", "settings", "voice", "knowledge", "memory",
    "skills", "tools", "system", "imports", "files",
}
_EXTENSION_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def empty_grant(user_id: str) -> dict[str, Any]:
    return {
        "version": 2,
        "user_id": user_id,
        "models": {"llm": []},
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
        "learning_policy": None,
    }


def _normalize_tool_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        return None
    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_learning_policy(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return {}

    capabilities = _normalize_tool_list(value.get("allowed_capabilities")) or []
    has_reading = isinstance(value.get("reading"), dict)
    raw_reading = value.get("reading") if has_reading else {}
    material_ids = _normalize_tool_list(raw_reading.get("material_ids")) or (
        [] if has_reading else ["*"]
    )
    extensions = _normalize_tool_list(raw_reading.get("extensions")) or (
        []
        if has_reading
        else ["read_aloud", "guided_learning", "vocabulary", "quiz", "translation"]
    )
    # A policy without explicit surfaces/reading keeps the original learner
    # behavior while persisting the complete contract for API clients.
    surfaces = _normalize_tool_list(value.get("allowed_surfaces"))
    normalized: dict[str, Any] = {
        "age_band": str(value.get("age_band") or "").strip(),
        "locked_persona": str(value.get("locked_persona") or "").strip(),
        "allowed_capabilities": capabilities,
        "default_capability": str(value.get("default_capability") or "chat").strip(),
    }
    if surfaces is not None:
        normalized["allowed_surfaces"] = surfaces
    pv = value.get("policy_version")
    if isinstance(pv, int) and pv >= 1:
        normalized["policy_version"] = pv
    normalized["reading"] = {
        "allow_upload": bool(raw_reading.get("allow_upload", True)),
        "material_ids": material_ids,
        "extensions": extensions,
    }
    return normalized


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
    raw_lp = payload.get("learning_policy")
    raw_surfaces = raw_lp.get("allowed_surfaces") if isinstance(raw_lp, dict) else None
    base["learning_policy"] = _normalize_learning_policy(raw_lp)
    _migrate_learner_v1_to_v2(user_id, base["learning_policy"])
    _migrate_learner_v2_add_settings(user_id, base["learning_policy"], raw_surfaces)
    return base


_LEARNER_V1_POLICY = {
    "age_band": "9-12",
    "locked_persona": "teacher",
    "allowed_capabilities": ["chat", "immersive_reading"],
    "default_capability": "immersive_reading",
    "allowed_surfaces": ["chat", "reading"],
    "reading": {
        "allow_upload": False,
        "material_ids": [],
        "extensions": [],
    },
}

_LEARNER_V2_CAPABILITIES = ["chat", "immersive_reading", "mastery_path", "immersive_watching"]
_LEARNER_V2_SURFACES = [
    "chat", "reading", "mastery", "books", "watching",
    "partners", "agents", "writing", "notebook", "dashboard",
    "voice", "knowledge", "memory", "files", "settings",
]


def _migrate_learner_v1_to_v2(user_id: str, policy: dict[str, Any] | None) -> None:
    """Upgrade learner-preset accounts from v1 (chat/reading only) to v2 (all learning surfaces).

    Only fires when the account is preset=learner AND the normalized policy
    exactly matches the original v1 defaults. Admin-customized policies and
    non-learner accounts are never touched.
    """
    if not isinstance(policy, dict):
        return
    user = get_user_by_id(user_id)
    if user is None or str(user[1].get("preset") or "standard") != "learner":
        return
    if policy.get("policy_version", 1) >= 2:
        return
    if policy == _LEARNER_V1_POLICY:
        policy["allowed_capabilities"] = list(_LEARNER_V2_CAPABILITIES)
        policy["allowed_surfaces"] = list(_LEARNER_V2_SURFACES)
        policy["policy_version"] = 2


_LEARNER_V2_SURFACES_BEFORE_SETTINGS = [
    "chat", "reading", "mastery", "books", "watching",
    "partners", "agents", "writing", "notebook", "dashboard",
    "voice", "knowledge", "memory", "files",
]


def _migrate_learner_v2_add_settings(
    user_id: str,
    policy: dict[str, Any] | None,
    raw_surfaces: Any = None,
) -> None:
    """Back-fill "settings" into v2 learner accounts whose raw on-disk surfaces
    exactly match the old default (before "settings" was added).

    The check uses the pre-normalization ``raw_surfaces`` so that anomalous
    data (empty strings, non-string values) is never silently cleaned into the
    old default and mistaken for an unmodified policy. Admin-customized
    policies are never touched.  Pinned to ``policy_version == 2`` so future
    schema versions are not accidentally matched.
    """
    if not isinstance(policy, dict):
        return
    if policy.get("policy_version", 1) != 2:
        return
    user = get_user_by_id(user_id)
    if user is None or str(user[1].get("preset") or "standard") != "learner":
        return
    if not isinstance(raw_surfaces, list):
        return
    if raw_surfaces != _LEARNER_V2_SURFACES_BEFORE_SETTINGS:
        return
    surfaces = policy.get("allowed_surfaces")
    if isinstance(surfaces, list) and "settings" not in surfaces:
        surfaces.append("settings")


def learner_grant(user_id: str) -> dict[str, Any]:
    """Return the conservative server-enforced expansion of the learner preset."""
    return normalize_grant(
        user_id,
        {
            "enabled_tools": [],
            "mcp_tools": [],
            "cli_apps": [],
            "exec_enabled": False,
            "learning_policy": {
                "policy_version": 2,
                "age_band": "9-12",
                "locked_persona": "teacher",
                "allowed_capabilities": ["chat", "immersive_reading", "mastery_path", "immersive_watching"],
                "default_capability": "immersive_reading",
                "allowed_surfaces": [
                    "chat", "reading", "mastery", "books", "watching",
                    "partners", "agents", "writing", "notebook", "dashboard",
                    "voice", "knowledge", "memory", "files", "settings",
                ],
                "reading": {
                    "allow_upload": False,
                    "material_ids": [],
                    "extensions": [],
                },
            },
        },
    )


def migrate_learner_grant(user_id: str) -> bool:
    """Persist learner policy upgrades atomically (v1→v2 and surface back-fills).

    Returns True if the file was rewritten, False if no migration was needed
    or the account is not a learner. Leaves the original file intact on any
    failure. Serialized against ``save_grant`` via a shared per-user lock.
    """
    path = grant_path(user_id)
    if not path.exists():
        return False
    with _grant_lock(user_id):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return False
        raw_policy = raw.get("learning_policy") if isinstance(raw, dict) else None
        if not isinstance(raw_policy, dict):
            return False
        try:
            migrated = normalize_grant(user_id, raw)
            migrated_policy = migrated.get("learning_policy")
            if not isinstance(migrated_policy, dict):
                return False
            raw_version = raw_policy.get("policy_version", 1)
            migrated_version = migrated_policy.get("policy_version", 1)
            v1_to_v2 = raw_version < 2 and migrated_version >= 2
            raw_surfaces = raw_policy.get("allowed_surfaces")
            settings_backfill = (
                raw_version == 2
                and isinstance(raw_surfaces, list)
                and raw_surfaces == _LEARNER_V2_SURFACES_BEFORE_SETTINGS
            )
            changed = v1_to_v2 or settings_backfill
            if not changed:
                return False
            validate_grant(migrated)
            _atomic_write_json(path, migrated)
            return True
        except Exception:
            return False


def load_grant(user_id: str) -> dict[str, Any]:
    path = grant_path(user_id)
    if not path.exists():
        return empty_grant(user_id)
    try:
        return normalize_grant(user_id, json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return empty_grant(user_id)


def save_grant(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    user_record = get_user_by_id(user_id)
    if user_record is None:
        raise ValueError(f"Unknown user id: {user_id}")
    _username, record = user_record
    if str(record.get("role") or "user") == "admin":
        raise ValueError("Admin users use the main workspace and cannot receive assignments.")
    grant = normalize_grant(user_id, payload)
    validate_grant(grant)
    with _grant_lock(user_id):
        _atomic_write_json(grant_path(user_id), grant)
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
    policy = grant.get("learning_policy")
    if policy is None:
        return
    if not isinstance(policy, dict):
        raise ValueError("learning_policy must be an object or null")
    if policy.get("age_band") not in LEARNING_AGE_BANDS:
        raise ValueError(
            f"learning_policy.age_band must be one of: {', '.join(sorted(LEARNING_AGE_BANDS))}"
        )
    if policy.get("locked_persona") not in LEARNING_PERSONAS:
        raise ValueError(
            f"learning_policy.locked_persona must be one of: {', '.join(sorted(LEARNING_PERSONAS))}"
        )
    capabilities = policy.get("allowed_capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise ValueError("learning_policy.allowed_capabilities cannot be empty")
    capability_set = set(capabilities)
    unknown = capability_set - LEARNING_CAPABILITIES
    if unknown:
        raise ValueError(
            "learning_policy.allowed_capabilities contains unsupported values: "
            f"{', '.join(sorted(unknown))}"
        )
    if policy.get("default_capability") not in capability_set:
        raise ValueError("learning_policy.default_capability must be allowed")
    surfaces = policy.get("allowed_surfaces", ["chat", "reading"])
    if not isinstance(surfaces, list) or not surfaces:
        raise ValueError("learning_policy.allowed_surfaces cannot be empty")
    unknown_surfaces = set(surfaces) - LEARNING_SURFACES
    if unknown_surfaces:
        raise ValueError(
            "learning_policy.allowed_surfaces contains unsupported values: "
            f"{', '.join(sorted(unknown_surfaces))}"
        )
    # allowed_capabilities and allowed_surfaces stay independent on purpose.
    # A capability enabled without its surface is not a security hole: the
    # surface guard (``require_learning_surface``) runs as a request-level
    # dependency, before the route handler and before the capability check,
    # so the page is already unreachable. Coupling them here would break the
    # guardian endpoint, which may only edit surfaces — never capabilities.
    # The admin UI still links them via CAP_TO_SURFACE for a clearer form.
    reading = policy.get("reading", {})
    if not isinstance(reading, dict):
        raise ValueError("learning_policy.reading must be an object")
    material_ids = reading.get("material_ids") or []
    if not isinstance(material_ids, list):
        raise ValueError("learning_policy.reading.material_ids must be an array")
    invalid_extensions = sorted(
        extension
        for extension in set(reading.get("extensions") or [])
        if not _EXTENSION_ID_RE.fullmatch(str(extension))
    )
    if invalid_extensions:
        raise ValueError(
            "learning_policy.reading.extensions contains invalid ids: "
            f"{', '.join(invalid_extensions)}"
        )


def public_grant(user_id: str) -> dict[str, Any]:
    return deepcopy(load_grant(user_id))
