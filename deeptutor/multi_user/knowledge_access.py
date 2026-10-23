"""Knowledge-base visibility and write guards for the multi-user layer."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from deeptutor.knowledge.kb_types import SUBAGENT_KB_TYPE
from deeptutor.knowledge.manager import KnowledgeBaseManager
from deeptutor.knowledge.manifest import (
    MANIFEST_NOTE_LIMIT,
    KbManifest,
    build_manifest,
    document_root,
)

from .context import get_current_user
from .grants import load_grant
from .models import KnowledgeResource
from .paths import get_account_path_service, get_admin_path_service, get_current_path_service

ADMIN_PREFIX = "admin:kb:"
USER_PREFIX = "user:kb:"
DEFAULT_KB_ALIASES = {"", "default", "current", "selected", "默认", "默认知识库", "当前知识库"}
REGISTRY_PREFIX = "kb:"


@lru_cache(maxsize=128)
def _manager_for(base_dir: str) -> KnowledgeBaseManager:
    return KnowledgeBaseManager(base_dir=base_dir)


def current_kb_base_dir() -> Path:
    from deeptutor.services.workspace.knowledge import library_request

    from .paths import get_account_path_service

    paths = get_account_path_service() if library_request.get() else get_current_path_service()
    return paths.get_knowledge_bases_root()


def admin_kb_base_dir() -> Path:
    return get_admin_path_service().get_knowledge_bases_root()


def current_kb_manager() -> KnowledgeBaseManager:
    return _manager_for(str(current_kb_base_dir().resolve()))


def account_kb_manager() -> KnowledgeBaseManager:
    """The account library, even when the request is workspace-scoped."""
    paths = get_account_path_service()
    return _manager_for(str(paths.get_knowledge_bases_root().resolve()))


def admin_kb_manager() -> KnowledgeBaseManager:
    return _manager_for(str(admin_kb_base_dir().resolve()))


def _strip_resource_prefix(value: str) -> tuple[str | None, str]:
    raw = str(value or "").strip()
    if raw.startswith(ADMIN_PREFIX):
        return "admin", raw[len(ADMIN_PREFIX) :]
    if raw.startswith(USER_PREFIX):
        return "user", raw[len(USER_PREFIX) :]
    return None, raw


def resolve_kb(kb_ref: str, *, require_write: bool = False) -> KnowledgeResource:
    if str(kb_ref or "").startswith(REGISTRY_PREFIX):
        return _resolve_registered(str(kb_ref), require_write=require_write)
    from deeptutor.services.workspace.context import current_workspace_id
    from deeptutor.services.workspace.knowledge import (
        canonical_kb_id,
        learning_source_kbs,
        library_request,
        parse_kb_id,
        qualified_kb_id,
        resolve_qualified,
        resolve_selected,
    )
    from deeptutor.services.workspace.resources import current_resources

    if not require_write and kb_ref in learning_source_kbs.get():
        return resolve_qualified(kb_ref)
    selected = None if library_request.get() else current_resources().knowledge_bases
    if selected is not None:
        try:
            return resolve_selected(kb_ref, selected, require_write=require_write)
        except HTTPException:
            if require_write:
                raise
            # D14: public / referenced KBs bypass workspace selections, but the
            # caller's own private KBs stay constrained by the allowlist.
            fallback = _legacy_registry_fallback(kb_ref)
            if fallback is None:
                raise
            return fallback
    if parse_kb_id(kb_ref) is not None or canonical_kb_id(kb_ref) != kb_ref:
        return resolve_qualified(kb_ref, require_write=require_write)
    if kb_ref and not kb_ref.startswith((ADMIN_PREFIX, USER_PREFIX)):
        previous_id = qualified_kb_id(kb_ref, current_workspace_id())
        if canonical_kb_id(previous_id) != previous_id:
            return resolve_qualified(previous_id, require_write=require_write)
    return _resolve_kb(kb_ref, require_write=require_write)


def _resolve_registered(kb_ref: str, *, require_write: bool) -> KnowledgeResource:
    """``kb:<id>`` — served from the record's own location, whoever's scope that is."""
    from deeptutor.knowledge import registry
    from deeptutor.services.workspace.knowledge import learning_source_kbs, library_request
    from deeptutor.services.workspace.resources import current_resources

    record = registry.get_kb(kb_ref)
    user = get_current_user()
    # Unreadable and unknown look the same: a private KB's existence is not public.
    if record is None or not registry.can_read(record, user):
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    writable = registry.can_write(record, user)
    if require_write and not writable:
        raise HTTPException(status_code=403, detail="This knowledge base is read-only for you")
    # D14: workspace selections only narrow the caller's own private KBs.
    own_private = record.visibility == "private" and record.created_by == user.id
    if own_private and not (not require_write and kb_ref in learning_source_kbs.get()) and not library_request.get():
        selected = current_resources().knowledge_bases
        if selected is not None and not _selection_allows(record, selected):
            raise HTTPException(
                status_code=403, detail="Knowledge base is not assigned to this workspace"
            )
    base_dir = Path(record.base_dir)
    if _manager_for(str(base_dir)).get_kb_entry(record.dir_name) is None:
        raise HTTPException(status_code=404, detail="Knowledge base is unavailable")
    return KnowledgeResource(
        id=record.ref,
        name=record.dir_name,
        base_dir=base_dir,
        source="admin" if base_dir == admin_kb_base_dir().resolve() else "user",
        assigned=record.created_by != user.id and not user.is_admin,
        read_only=not writable,
    )


def _selection_allows(record: Any, selected: list[str]) -> bool:
    from deeptutor.services.workspace.knowledge import (
        canonical_kb_id,
        qualified_kb_id,
        workspace_id_for_kb_base_dir,
    )

    chosen = {canonical_kb_id(ref) for ref in selected}
    if record.ref in chosen:
        return True
    workspace_id = workspace_id_for_kb_base_dir(record.base_dir)
    return workspace_id is not None and qualified_kb_id(record.dir_name, workspace_id) in chosen


def _shared_admin_resource(name: str, *, require_write: bool) -> KnowledgeResource | None:
    """A legacy ``admin:kb:<name>`` / bare-name read of an admin KB, decided by the registry."""
    from deeptutor.knowledge import registry

    record = registry.find_by_location(admin_kb_base_dir(), name)
    if record is None or not registry.can_read(record, get_current_user()):
        return None
    if require_write:
        raise HTTPException(status_code=403, detail="Shared knowledge bases are read-only")
    return KnowledgeResource(
        id=f"{ADMIN_PREFIX}{name}",
        name=name,
        base_dir=admin_kb_base_dir(),
        source="admin",
        assigned=True,
        read_only=True,
    )


def _legacy_registry_fallback(kb_ref: str) -> KnowledgeResource | None:
    """Bypass a workspace 403 when the legacy ref points at a public/referenced KB.

    The caller's own private KBs stay constrained by the workspace allowlist;
    only KBs readable through the registry (public or via a registered reader)
    that belong to someone else get the bypass (D14).
    """
    from deeptutor.knowledge import registry

    _, name = _strip_resource_prefix(kb_ref)
    # Try admin library first (covers ``admin:kb:<name>`` and bare names).
    record = registry.find_by_location(admin_kb_base_dir(), name)
    if record is None:
        return None
    user = get_current_user()
    own_private = record.visibility == "private" and record.created_by == user.id
    if own_private or not registry.can_read(record, user):
        return None
    return KnowledgeResource(
        id=f"{ADMIN_PREFIX}{name}",
        name=name,
        base_dir=admin_kb_base_dir(),
        source="admin",
        assigned=True,
        read_only=True,
    )


def _resolve_kb(kb_ref: str, *, require_write: bool = False) -> KnowledgeResource:
    user = get_current_user()
    requested_source, name = _strip_resource_prefix(kb_ref)

    if user.is_admin:
        manager = current_kb_manager()
        resolved = _resolve_default_or_name(manager, name)
        return KnowledgeResource(
            id=f"admin:kb:{resolved}",
            name=resolved,
            base_dir=current_kb_base_dir(),
            source="admin",
            assigned=False,
            read_only=False,
        )

    user_manager = current_kb_manager()

    if requested_source == "admin":
        shared = _shared_admin_resource(name, require_write=require_write)
        if shared is None:
            raise HTTPException(status_code=403, detail="Knowledge base is not shared with you")
        return shared

    if requested_source == "user":
        resolved = _resolve_default_or_name(user_manager, name)
        return KnowledgeResource(
            id=f"user:kb:{resolved}",
            name=resolved,
            base_dir=current_kb_base_dir(),
            source="user",
            assigned=False,
            read_only=False,
        )

    if name.lower() in DEFAULT_KB_ALIASES:
        resolved = _resolve_default_or_name(user_manager, name)
        return KnowledgeResource(
            id=f"user:kb:{resolved}",
            name=resolved,
            base_dir=current_kb_base_dir(),
            source="user",
            assigned=False,
            read_only=False,
        )

    user_names = set(user_manager.list_knowledge_bases())
    if name in user_names:
        return KnowledgeResource(
            id=f"user:kb:{name}",
            name=name,
            base_dir=current_kb_base_dir(),
            source="user",
            assigned=False,
            read_only=False,
        )

    shared = _shared_admin_resource(name, require_write=require_write)
    if shared is not None:
        return shared

    raise HTTPException(status_code=404, detail=f"Knowledge base '{name}' not found")


def _resolve_default_or_name(manager: KnowledgeBaseManager, name: str) -> str:
    requested = str(name or "").strip()
    names = manager.list_knowledge_bases()
    if requested and requested in names:
        return requested
    if requested.lower() in DEFAULT_KB_ALIASES:
        default_kb = manager.get_default()
        if default_kb and default_kb in names:
            return default_kb
        raise HTTPException(status_code=404, detail="No default knowledge base is configured")
    raise HTTPException(status_code=404, detail=f"Knowledge base '{requested}' not found")


def manager_for_resource(resource: KnowledgeResource) -> KnowledgeBaseManager:
    return _manager_for(str(resource.base_dir.resolve()))


def list_visible_knowledge_bases() -> list[dict[str, Any]]:
    from deeptutor.services.workspace.context import current_workspace_id
    from deeptutor.services.workspace.knowledge import (
        canonical_kb_id,
        knowledge_catalog,
        library_request,
        move_aliases,
        parse_kb_id,
    )
    from deeptutor.services.workspace.resources import current_resources

    if library_request.get():
        return knowledge_catalog()
    selected = current_resources().knowledge_bases
    if selected is None:
        own = _list_visible_knowledge_bases()
        origin = current_workspace_id()
        moved_refs = {
            canonical_kb_id(old_id)
            for old_id in move_aliases()
            if (parsed := parse_kb_id(old_id)) is not None and parsed[0] == origin
        }
        if not moved_refs:
            return _with_account_subagents(own, origin=origin)
        catalog = {item["id"]: item for item in knowledge_catalog()}
        existing = {item["id"] for item in own}
        return _with_account_subagents(
            own + [catalog[rid] for rid in sorted(moved_refs - existing) if rid in catalog],
            origin=origin,
        )
    catalog = {item["id"]: item for item in knowledge_catalog()}
    return [
        catalog[rid]
        for rid in dict.fromkeys(canonical_kb_id(ref) for ref in selected)
        if rid in catalog
    ]


def _with_account_subagents(items: list[dict[str, Any]], *, origin: str) -> list[dict[str, Any]]:
    """Add account-level agent connectors while preserving KB isolation.

    Connected agents are live delegates rather than document catalogs. They are
    shared with every workspace in inherit mode, while an explicit workspace KB
    selection remains authoritative.
    """
    if not origin:
        return items
    existing = {item["id"] for item in items}
    result = list(items)
    manager = account_kb_manager()
    for name in manager.list_knowledge_bases():
        meta = manager.get_metadata(name)
        if not isinstance(meta, dict) or meta.get("type") != SUBAGENT_KB_TYPE:
            continue
        rid = f"account:kb:{name}"
        if rid in existing:
            continue
        result.append(
            {
                "id": rid,
                "name": name,
                "source": "admin" if get_current_user().is_admin else "user",
                "assigned": False,
                "read_only": False,
                "provenance_label": "Account library",
            }
        )
    return result


def _list_visible_knowledge_bases() -> list[dict[str, Any]]:
    user = get_current_user()
    manager = current_kb_manager()
    items: list[dict[str, Any]] = []
    for name in manager.list_knowledge_bases():
        items.append(
            {
                "id": f"admin:kb:{name}" if user.is_admin else f"user:kb:{name}",
                "name": name,
                "source": "admin" if user.is_admin else "user",
                "assigned": False,
                "read_only": False,
                "provenance_label": "Created by you" if not user.is_admin else "Admin workspace",
            }
        )

    if user.is_admin:
        return items

    admin_manager = admin_kb_manager()
    admin_names = set(admin_manager.list_knowledge_bases())
    existing_ids = {item["id"] for item in items}
    for item in load_grant(user.id).get("knowledge_bases", []) or []:
        name = str(item.get("name") or item.get("kb_name") or "").strip()
        resource_id = str(item.get("resource_id") or item.get("id") or "")
        if resource_id.startswith(ADMIN_PREFIX):
            name = resource_id[len(ADMIN_PREFIX) :]
        if not name:
            continue
        rid = f"admin:kb:{name}"
        if rid in existing_ids:
            continue
        items.append(
            {
                "id": rid,
                "name": name,
                "source": "admin",
                "assigned": True,
                "read_only": True,
                "available": name in admin_names,
                "provenance_label": "Assigned by admin",
                "needs_admin_reindex": bool(item.get("needs_admin_reindex", False)),
                "embedding_signature": item.get("embedding_signature", ""),
            }
        )
    return items


def assert_writable(kb_ref: str) -> KnowledgeResource:
    return resolve_kb(kb_ref, require_write=True)


def resolve_for_rag(kb_ref: str | None) -> KnowledgeResource | None:
    if not kb_ref:
        return None
    resource = resolve_kb(kb_ref, require_write=False)
    if resource.assigned:
        from .audit import log_usage

        log_usage("knowledge_base", resource.id, "rag_query")
    return resource


def resolve_kb_metadata(kb_ref: str | None) -> dict[str, Any] | None:
    """Access-checked KB metadata (``type`` / ``vault_path`` / …) for ``kb_ref``.

    Returns ``None`` when the reference is empty or not accessible to the
    current user. A pure read with no usage audit (unlike
    :func:`resolve_for_rag`) — safe to call while resolving capability bindings.
    """
    if not kb_ref:
        return None
    try:
        resource = resolve_kb(str(kb_ref), require_write=False)
    except HTTPException:
        return None
    manager = _manager_for(str(resource.base_dir.resolve()))
    return manager.get_metadata(resource.name)


def resolve_kb_manifest(
    kb_ref: str | None,
    *,
    limit: int = MANIFEST_NOTE_LIMIT,
    pattern: str = "",
) -> KbManifest | None:
    """Access-checked document inventory for ``kb_ref`` (``None`` if inaccessible).

    The one seam through which the chat manifest and the ``kb_files`` tool read
    a KB's document set, so neither can bypass the per-user visibility rules
    :func:`resolve_kb` enforces. A pure read with no usage audit, mirroring
    :func:`resolve_kb_metadata`; it touches the filesystem, so async callers
    should hand it to a worker thread.
    """
    if not kb_ref:
        return None
    try:
        resource = resolve_kb(str(kb_ref), require_write=False)
    except HTTPException:
        return None
    manager = _manager_for(str(resource.base_dir.resolve()))
    entry = manager.get_kb_entry(resource.name)
    if entry is None:
        return None
    return build_manifest(
        name=resource.name,
        kb_dir=resource.base_dir / resource.name,
        entry=entry,
        limit=limit,
        pattern=pattern,
    )


def resolve_kb_document_path(kb_ref: str | None, rel_path: str) -> Path | None:
    """Access-checked path to one document inside ``kb_ref``.

    The seam a caller uses to *read a single file* the learner picked out of a
    knowledge base, rather than retrieving passages from the whole thing. Same
    visibility rules as :func:`resolve_kb_manifest`, and ``rel_path`` is
    resolved against the KB's own document root and then re-checked to be
    inside it — the value arrives from a client (and, for a generated outline,
    has passed through a model), so ``..`` and absolute paths must not be able
    to reach a file the KB does not own.

    ``None`` when the KB is inaccessible, has no local documents (a connected
    external resource), or the path is not a file inside it.
    """
    if not kb_ref or not str(rel_path or "").strip():
        return None
    try:
        resource = resolve_kb(str(kb_ref), require_write=False)
    except HTTPException:
        return None
    manager = _manager_for(str(resource.base_dir.resolve()))
    entry = manager.get_kb_entry(resource.name)
    if entry is None:
        return None
    root = document_root(resource.base_dir / resource.name, entry)
    if root is None or not root.is_dir():
        return None
    try:
        resolved_root = root.resolve()
        candidate = (root / str(rel_path).strip()).resolve()
    except OSError:
        return None
    if not candidate.is_relative_to(resolved_root) or not candidate.is_file():
        return None
    return candidate
