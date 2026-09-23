"""Learning-account policy resolution and turn enforcement."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .context import get_current_user
from .grants import load_grant


class LearningPersonaMissingError(RuntimeError):
    """Raised when the admin persona directory lacks the locked persona file."""

    error_code = "learning_persona_missing"
    retryable = False


def learning_policy_for_user(user_id: str, *, is_admin: bool = False) -> dict[str, Any] | None:
    """Return the sanitized public policy for an account."""
    if is_admin:
        return None
    loaded = load_grant(user_id)
    policy = loaded.get("learning_policy")
    if policy is None:
        from .identity import get_user_by_id

        user = get_user_by_id(user_id)
        if user is not None and str(user[1].get("preset") or "standard") == "learner":
            from .grants import learner_grant

            policy = learner_grant(user_id).get("learning_policy")
    return deepcopy(policy) if isinstance(policy, dict) else None


def current_learning_policy() -> dict[str, Any] | None:
    user = get_current_user()
    return learning_policy_for_user(user.id, is_admin=user.is_admin)


def apply_learning_policy(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply account policy before a turn is validated or persisted."""
    policy = current_learning_policy()
    if policy is None:
        return payload

    capability = str(payload.get("capability") or "chat")
    allowed = set(policy.get("allowed_capabilities") or [])
    if capability not in allowed:
        raise PermissionError(
            "This learning account cannot use this mode. Please choose an allowed learning mode."
        )

    from fastapi import HTTPException

    from .knowledge_access import resolve_kb
    from .partner_access import can_use_partner

    requested_partner = str(payload.get("partner_id") or "").strip()
    partner_allowed = bool(requested_partner) and can_use_partner(requested_partner)

    raw_kbs = payload.get("knowledge_bases")
    validated_kbs: list[str] = []
    if isinstance(raw_kbs, (list, tuple)):
        for entry in raw_kbs:
            if not isinstance(entry, str) or not entry.strip():
                continue
            try:
                resolve_kb(entry.strip(), require_write=False)
                validated_kbs.append(entry.strip())
            except HTTPException:
                pass

    raw_kb_name = str(payload.get("kb_name") or "").strip()
    kb_name = ""
    if raw_kb_name:
        try:
            resolve_kb(raw_kb_name, require_write=False)
            kb_name = raw_kb_name
        except HTTPException:
            pass

    return {
        **payload,
        "persona": str(policy.get("locked_persona") or ""),
        "tools": [],
        "enabled_tools": [],
        "enable_web_search": False,
        "partner_id": requested_partner if partner_allowed else None,
        "bot_id": None,
        "knowledge_bases": validated_kbs,
        "kb_name": kb_name,
        "enable_rag": bool(validated_kbs or kb_name) and payload.get("enable_rag", False),
    }


def assert_learning_surface(surface: str) -> None:
    """Deny a server surface not explicitly exposed to a learning account."""
    policy = current_learning_policy()
    if policy is None:
        return
    if surface not in set(policy.get("allowed_surfaces") or ["chat", "reading"]):
        target = f"{surface} surface" if surface else "requested server surface"
        raise PermissionError(f"This learning account cannot use the {target}.")


def assert_learning_material(material_id: str, *, upload: bool = False) -> None:
    """Enforce upload and assigned-material policy in the authenticated scope."""
    policy = current_learning_policy()
    if policy is None:
        return
    has_reading = isinstance(policy.get("reading"), dict)
    reading = policy.get("reading") if has_reading else {}
    if upload:
        if has_reading and not bool(reading.get("allow_upload")):
            raise PermissionError("This learning account cannot upload reading materials.")
        return
    assigned = set(reading.get("material_ids") or (["*"] if not has_reading else []))
    if "*" not in assigned and str(material_id or "") not in assigned:
        raise PermissionError("This reading material is not assigned to this learning account.")


def assert_learning_material_mutation(material_id: str) -> None:
    """Protect administrator-assigned material from learner-side deletion."""
    policy = current_learning_policy()
    if policy is None:
        return
    reading = policy.get("reading") if isinstance(policy.get("reading"), dict) else {}
    if not bool(reading.get("allow_upload")):
        raise PermissionError("This learning account cannot modify assigned reading materials.")
    assert_learning_material(material_id)


def allowed_reading_extensions() -> set[str] | None:
    """None means unrestricted; a set is the learner extension allowlist."""
    policy = current_learning_policy()
    if policy is None:
        return None
    if not isinstance(policy.get("reading"), dict):
        return {"read_aloud", "guided_learning", "vocabulary", "quiz", "translation"}
    return set(policy["reading"].get("extensions") or [])


def assert_workspace_isolated() -> None:
    """Reject writes when a learning account's path service fell back to shared.

    A no-op for admins and standard accounts (no learning policy). For learner
    accounts, verifies that ``get_path_service()`` resolved to a user-scoped
    instance rather than the global default.
    """
    if current_learning_policy() is None:
        return
    from deeptutor.services.path_service import PathService, get_path_service

    if get_path_service() is PathService.get_instance():
        raise PermissionError("Workspace isolation failed; write refused.")


def resolve_persona_context(
    requested_persona: str,
    *,
    is_admin: bool,
) -> tuple[str, str]:
    """Resolve persona content respecting learning-policy protection.

    Learning-policy accounts always load the ``locked_persona`` from the admin
    persona directory regardless of what the client requested — even if the
    request omits persona entirely.  If the locked persona file is missing from
    the admin directory, the turn is aborted (fail closed).

    Other non-admin users try own workspace first, then fall back to admin
    presets.

    Returns ``(persona_context, effective_persona_name)``.
    """
    from deeptutor.multi_user.paths import get_admin_path_service
    from deeptutor.services.persona import PersonaService, get_persona_service

    policy = current_learning_policy()
    if policy:
        locked = str(policy.get("locked_persona") or "teacher")
        context = PersonaService(
            root=get_admin_path_service().get_workspace_dir() / "personas"
        ).load_for_context(locked)
        if not context:
            raise LearningPersonaMissingError(
                f"Learning policy requires persona '{locked}' but it is missing "
                f"from the admin persona directory. The turn cannot proceed safely."
            )
        return context, locked

    if not requested_persona:
        return "", ""

    context = get_persona_service().load_for_context(requested_persona)
    if not context and not is_admin:
        context = PersonaService(
            root=get_admin_path_service().get_workspace_dir() / "personas"
        ).load_for_context(requested_persona)
    return context, requested_persona if context else ""


__all__ = [
    "allowed_reading_extensions",
    "apply_learning_policy",
    "assert_learning_material",
    "assert_learning_material_mutation",
    "assert_learning_surface",
    "assert_workspace_isolated",
    "current_learning_policy",
    "learning_policy_for_user",
    "resolve_persona_context",
]
