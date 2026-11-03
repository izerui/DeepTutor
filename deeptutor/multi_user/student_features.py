"""Features a student may be denied, enforced on the routes (spec 2026-11-03 batch 3 §7).

Each feature maps to a ``student_policy`` key. A student whose effective
policy denies the key gets a 403 on the feature's routes; everyone else
passes. Creating is what the "own …" switches stop — what was made stays.
"""

from __future__ import annotations

#: Whole features: every route under the prefix.
FEATURE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("/api/partners", "allow_partners"),
    # Group chats run the same Partners.
    ("/api/partner-groups", "allow_partners"),
    ("/api/subagents", "allow_agents"),
    ("/api/documents", "allow_co_writer"),
)
#: Creating one's own: (method, route template).
CREATE_ROUTES: dict[tuple[str, str], str] = {
    ("POST", "/api/courses"): "allow_personal_course",
    ("POST", "/api/task-board/cards"): "allow_personal_task_card",
}

DENIED = {
    "allow_partners": "Partners are not available for your account.",
    "allow_agents": "Agents are not available for your account.",
    "allow_co_writer": "Co-Writer is not available for your account.",
    "allow_personal_course": "Creating your own courses is turned off for your account.",
    "allow_personal_task_card": "Adding your own task cards is turned off for your account.",
}


def feature_for(path: str, method: str, route_path: str | None) -> str:
    normalized = "/" + str(path or "").lstrip("/")
    for prefix, key in FEATURE_PREFIXES:
        if normalized == prefix or normalized.startswith(f"{prefix}/"):
            return key
    return CREATE_ROUTES.get((method.upper(), route_path or normalized), "")


def assert_student_feature(path: str, method: str, route_path: str | None) -> None:
    """Raise ``PermissionError`` when a student's policy denies this route's feature."""
    key = feature_for(path, method, route_path)
    if not key:
        return
    from .roles import student_allows

    if not student_allows(key):
        raise PermissionError(DENIED[key])


__all__ = ["CREATE_ROUTES", "FEATURE_PREFIXES", "assert_student_feature", "feature_for"]
