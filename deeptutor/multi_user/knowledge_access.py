"""Knowledge-base visibility and write guards for the multi-user layer."""

from __future__ import annotations

from dataclasses import replace
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
            # Match legacy refs by registry identity, preserving the private
            # allowlist while public / referenced KBs retain their read access.
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
    from deeptutor.knowledge.registry_scope import source_is_archived
    from deeptutor.services.workspace.models import WorkspaceError
    from deeptutor.services.workspace.knowledge import learning_source_kbs, library_request
    from deeptutor.services.workspace.resources import current_resources

    record = registry.get_kb(kb_ref)
    user = get_current_user()
    # Unreadable and unknown look the same: a private KB's existence is not public.
    if record is None or not registry.can_read(record, user):
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    writable = registry.can_write(record, user)
    if writable:
        try:
            writable = not source_is_archived(record.base_dir)
        except WorkspaceError:
            # Archive uncertainty narrows writes, never previously granted reads.
            writable = False
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

    record = registry.find_admin_legacy_name(name) or registry.find_by_location(
        admin_kb_base_dir(), name
    )
    if record is None or not registry.can_read(record, get_current_user()):
        return None
    if require_write:
        raise HTTPException(status_code=403, detail="Shared knowledge bases are read-only")
    resource = _resolve_registered(record.ref, require_write=False)
    return replace(
        resource,
        id=f"{ADMIN_PREFIX}{name}",
        assigned=True,
        read_only=True,
    )


def _legacy_registry_fallback(kb_ref: str) -> KnowledgeResource | None:
    """Resolve legacy reads by identity without bypassing the private allowlist."""
    from deeptutor.knowledge import registry
    from deeptutor.services.workspace.knowledge import (
        canonical_kb_id,
        parse_kb_id,
        resolve_qualified,
    )

    if parse_kb_id(canonical_kb_id(kb_ref)) is not None:
        # Resolve the owned source scope first, then check its registry identity
        # against the consumer workspace after resolve_qualified restores it.
        resource = resolve_qualified(kb_ref)
        record = registry.find_by_location(resource.base_dir, resource.name)
        if record is None:
            return None
        checked = _resolve_registered(record.ref, require_write=False)
        return replace(checked, id=resource.id)

    prefix, name = _strip_resource_prefix(kb_ref)
    if prefix == "user":
        return None
    # Try admin library first (covers ``admin:kb:<name>`` and bare names).
    record = registry.find_admin_legacy_name(name) or registry.find_by_location(
        admin_kb_base_dir(), name
    )
    if record is None:
        return None
    user = get_current_user()
    own_private = record.visibility == "private" and record.created_by == user.id
    if own_private or not registry.can_read(record, user):
        return None
    return _shared_admin_resource(name, require_write=False)


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

    if requested_source == "user" or name.lower() in DEFAULT_KB_ALIASES:
        resolved = _resolve_default_or_name(user_manager, name)
    elif name in user_manager.list_knowledge_bases():
        resolved = name
    else:
        shared = _shared_admin_resource(name, require_write=require_write)
        if shared is not None:
            return shared
        raise HTTPException(status_code=404, detail=f"Knowledge base '{name}' not found")

    from deeptutor.services.partners.scope import is_partner_user_id

    if is_partner_user_id(user.id):
        from deeptutor.knowledge import registry

        # Adopted human-owned KBs stay in this directory after detachment.
        # Their location is not authority, even when reached by a legacy alias.
        record = registry.find_by_location(current_kb_base_dir(), resolved)
        if record is not None:
            resource = _resolve_registered(record.ref, require_write=require_write)
            return replace(resource, id=f"user:kb:{resolved}")

    return KnowledgeResource(
        id=f"user:kb:{resolved}",
        name=resolved,
        base_dir=current_kb_base_dir(),
        source="user",
        assigned=False,
        read_only=False,
    )


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
        return _with_public(knowledge_catalog())
    selected = current_resources().knowledge_bases
    if selected is None:
        own = _with_external_private(_list_visible_knowledge_bases())
        origin = current_workspace_id()
        moved_refs = {
            canonical_kb_id(old_id)
            for old_id in move_aliases()
            if (parsed := parse_kb_id(old_id)) is not None and parsed[0] == origin
        }
        if not moved_refs:
            return _with_public(_with_account_subagents(own, origin=origin))
        catalog = {item["id"]: item for item in knowledge_catalog()}
        existing = {item["id"] for item in own}
        return _with_public(
            _with_account_subagents(
                own + [catalog[rid] for rid in sorted(moved_refs - existing) if rid in catalog],
                origin=origin,
            )
        )
    catalog = {item["id"]: item for item in knowledge_catalog()}
    # R3: a selection may name KBs by ``kb:<id>`` as well as by legacy id.
    by_registry_ref = {item["ref"]: item for item in catalog.values() if item.get("ref")}
    chosen = []
    for rid in dict.fromkeys(canonical_kb_id(ref) for ref in selected):
        item = by_registry_ref.get(rid) if rid.startswith(REGISTRY_PREFIX) else catalog.get(rid)
        if item is not None and item not in chosen:
            chosen.append(item)
    # D14: the selection narrows private KBs only; public ones are always there.
    return _with_public(chosen)


def _with_account_subagents(items: list[dict[str, Any]], *, origin: str) -> list[dict[str, Any]]:
    """Add account-level agent connectors while preserving KB isolation.

    Connected agents are live delegates rather than document catalogs. They are
    shared with every workspace in inherit mode, while an explicit workspace KB
    selection remains authoritative.
    """
    if not origin:
        return items
    from deeptutor.knowledge import registry

    existing = {item["id"] for item in items}
    result = list(items)
    manager = account_kb_manager()
    subagents = {
        name: meta
        for name in manager.list_knowledge_bases()
        if isinstance(meta := manager.get_metadata(name), dict)
        and meta.get("type") == SUBAGENT_KB_TYPE
    }
    records = registry.sync_base_dir(
        manager.base_dir, subagents, created_by=get_current_user().id
    )
    for name in subagents:
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
                **_registry_fields(records.get(name), owned=True),
            }
        )
    return result


def _list_visible_knowledge_bases() -> list[dict[str, Any]]:
    """The current scope's own KBs, registered on first sight (connect routes, manual folders)."""
    from deeptutor.knowledge import registry

    user = get_current_user()
    manager = current_kb_manager()
    names = manager.list_knowledge_bases()
    records = registry.sync_base_dir(
        current_kb_base_dir(),
        {name: manager.get_kb_entry(name) for name in names},
        created_by=user.id,
    )
    return [
        {
            "id": f"admin:kb:{name}" if user.is_admin else f"user:kb:{name}",
            "name": name,
            "source": "admin" if user.is_admin else "user",
            "assigned": False,
            "read_only": False,
            "provenance_label": "Created by you" if not user.is_admin else "Admin workspace",
            **_registry_fields(records.get(name), owned=True),
        }
        for name in names
    ]


def _registry_fields(record: Any, *, owned: bool) -> dict[str, Any]:
    if record is None:
        return {"owned": owned}
    from deeptutor.knowledge import registry
    from deeptutor.services.workspace.knowledge import (
        canonical_kb_id,
        qualified_kb_id,
        workspace_id_for_kb_base_dir,
    )

    legacy_refs = registry.admin_legacy_refs(record.kb_id)
    # Storage classification is relative to this viewer's catalogs, not the
    # creator or the unqualified list ID (which also occurs in workspaces).
    storage_scope = workspace_id_for_kb_base_dir(record.base_dir)
    if storage_scope is not None:
        storage_ref = qualified_kb_id(record.dir_name, storage_scope)
        # A historical move may reserve this spelling for another identity.
        if canonical_kb_id(storage_ref) == storage_ref:
            legacy_refs.append(storage_ref)

    return {
        "kb_id": record.kb_id,
        "ref": record.ref,
        "legacy_refs": legacy_refs,
        "visibility": record.visibility,
        "created_by": record.created_by,
        "owned": owned,
        "kind": record.kind,
        "connector": registry.is_connector(record),
    }


def _with_external_private(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Include independently owned KBs outside the account's storage scopes."""
    from deeptutor.knowledge import registry
    from deeptutor.knowledge.registry_scope import is_retired_source
    from deeptutor.services.workspace.knowledge import workspace_id_for_kb_base_dir

    user = get_current_user()
    present = {item.get("kb_id") for item in items}
    result = list(items)
    for record in registry.list_records(visibility="private", created_by=user.id):
        if record.kb_id in present or workspace_id_for_kb_base_dir(record.base_dir) is not None:
            continue
        if is_retired_source(record.base_dir):
            # Stable IDs still serve consumers, but are not active libraries.
            continue
        result.append(
            {
                "id": record.ref,
                "name": record.name,
                "source": "user",
                "assigned": False,
                "read_only": False,
                "provenance_label": "Created by you",
                **_registry_fields(record, owned=True),
            }
        )
    return result


def _account_names() -> dict[str, str]:
    from .identity import load_users
    from .models import LOCAL_ADMIN_ID

    names = {LOCAL_ADMIN_ID: "admin"}
    for username, record in load_users().items():
        if record.get("id"):
            names[str(record["id"])] = username
    return names


def _with_public(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Public KBs are in every list and every workspace (spec 7.3, D14)."""
    from deeptutor.knowledge import registry

    present = {item.get("kb_id") for item in items if item.get("kb_id")}
    extra = [r for r in registry.list_records(visibility="public") if r.kb_id not in present]
    if not extra:
        return items
    user = get_current_user()
    names = _account_names()
    admin_root = str(admin_kb_base_dir().resolve())
    return items + [
        {
            "id": record.ref,
            "name": record.name,
            "source": "admin" if record.base_dir == admin_root else "user",
            "assigned": True,
            "read_only": not registry.can_write(record, user),
            "provenance_label": "Public knowledge base",
            **_registry_fields(record, owned=record.created_by == user.id),
            "created_by_name": names.get(record.created_by, ""),
        }
        for record in extra if registry.can_read(record, user)
    ]


def chat_readable_kb_refs(refs: Any) -> list[str]:
    """Keep public and readable own-private KBs for learner chat, in input order.

    Reference-only access does not extend to chat, and connectors are excluded.
    Private KBs still obey the current workspace's selection.
    """
    from deeptutor.knowledge import registry

    if not isinstance(refs, (list, tuple)):
        return []
    user = get_current_user()
    kept: list[str] = []
    for raw in refs:
        if not isinstance(raw, str):
            continue
        ref = raw.strip()
        if not ref or ref in kept:
            continue
        try:
            record = registry.resolve_ref(ref)
            if record is None or registry.is_connector(record):
                continue
            if record.visibility != "public" and record.created_by != user.id:
                continue
            # Registry IDs are unchecked lookups. Use the read guard as well
            # so they cannot bypass private workspace selections or availability.
            resolve_kb(ref)
        except (HTTPException, ValueError):
            continue
        kept.append(ref)
    return kept


def assert_writable(kb_ref: str) -> KnowledgeResource:
    return resolve_kb(kb_ref, require_write=True)


def resolve_for_rag(kb_ref: str | None) -> KnowledgeResource | None:
    """The library a retrieval reads, after access *and* the turn's allowlist.

    Every textual and visual retrieval resolves its library here, so this is
    where a teacher path's R8 restriction is enforced (403 outside it). The
    allowlist is checked after the ordinary rules and therefore only narrows.
    """
    if not kb_ref:
        return None
    from .retrieval_scope import enforce_retrieval_scope

    resource = resolve_kb(kb_ref, require_write=False)
    enforce_retrieval_scope(resource)
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
    from .retrieval_scope import enforce_retrieval_scope

    try:
        resource = resolve_kb(str(kb_ref), require_write=False)
        enforce_retrieval_scope(resource)
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


def live_document_path(kb_ref: str | None, doc_id: str) -> str | None:
    """Where document *doc_id* of the library *kb_ref* lives now, or ``None``.

    Documents keep a fixed id when they move (plan B), so a material that
    captured a path at some point — a teacher path's published snapshot —
    follows the id instead: the captured path may since hold an unrelated
    file. ``None`` when the library or the live document is gone. A lookup,
    not an access check: callers still read through the access-checked seams.
    """
    if not kb_ref or not doc_id:
        return None
    from deeptutor.knowledge import registry

    record = registry.resolve_ref(str(kb_ref))
    if record is None:
        return None
    for document in registry.list_documents(record.kb_id):
        if document.doc_id == doc_id and not document.deleted:
            return document.rel_path
    return None


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
    from .retrieval_scope import enforce_retrieval_scope

    try:
        resource = resolve_kb(str(kb_ref), require_write=False)
        enforce_retrieval_scope(resource)
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
