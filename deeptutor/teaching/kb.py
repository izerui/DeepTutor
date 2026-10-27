"""Teaching's only door into the knowledge-base registry (plan B).

Every function reads ``registry.<name>`` at call time, so the registry stays
the single source of truth and tests can stand it in with ``monkeypatch``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
import hashlib
from pathlib import Path, PurePosixPath
from typing import Any

from deeptutor.knowledge import registry
from deeptutor.learning.models import TopicSource, TopicSourceKind
from deeptutor.teaching.models import TemplateDocRef, TemplateKbRef

REFERRER_KIND = "mastery_template"
KbVisibilityError = registry.KbVisibilityError
KbRegistryError = registry.KbRegistryError


def source_kb_ref(source: TopicSource) -> str:
    """The knowledge-base reference a topic source reads from, or ``""``."""
    if source.kind == TopicSourceKind.KNOWLEDGE_BASE:
        return str(source.source_id or "").strip()
    if source.kind == TopicSourceKind.FILE:
        metadata = source.metadata or {}
        return str(metadata.get("kb_name") or metadata.get("knowledge_base") or "").strip()
    return ""


def source_rel_path(source: TopicSource) -> str:
    """A FILE source's document as plan B keys it: POSIX, relative to ``raw/``.

    The wizard stores it in ``metadata["path"]`` or ``source_id`` (see
    ``learning/topic_generation._ground_file_source``). ``""`` for anything that
    tries to leave the knowledge base.
    """
    raw = str((source.metadata or {}).get("path") or source.source_id or "").strip()
    parts = [part for part in PurePosixPath(raw.replace("\\", "/")).parts if part not in ("/", ".")]
    if not parts or ".." in parts:
        return ""
    return "/".join(parts)


def source_doc_id(source: TopicSource) -> str:
    """The stable document id a FILE source already carries, or ``""``."""
    if source.kind != TopicSourceKind.FILE:
        return ""
    return str((source.metadata or {}).get("doc_id") or "").strip()


def resolve(ref: str) -> Any | None:
    return registry.resolve_ref(ref) if ref else None


def resolve_id(kb_id: str) -> Any | None:
    return registry.get_kb(kb_id)


def is_connector(record: Any) -> bool:
    return bool(registry.is_connector(record))


def can_write(record: Any, user: Any) -> bool:
    return bool(registry.can_write(record, user))


def can_read(record: Any, user: Any) -> bool:
    return bool(registry.can_read(record, user))


def _raw_dir(record: Any) -> Path:
    return Path(record.base_dir) / record.dir_name / "raw"


def kb_ready(record: Any) -> bool:
    """Whether the library finished indexing.

    Libraries created before the status field existed report ``unknown``; they
    count as ready when their ``raw/`` folder holds files (cross-check 6.5).
    """
    base_dir = Path(record.base_dir)
    raw = _raw_dir(record)
    if not base_dir.is_dir():
        # Never let a status probe create a vanished catalog folder.
        return False
    from deeptutor.knowledge.manager import KnowledgeBaseManager

    status = KnowledgeBaseManager(base_dir=str(base_dir)).get_kb_status(record.dir_name)
    state = str((status or {}).get("status") or "unknown")
    if state == "ready":
        return True
    if state == "unknown":
        return raw.is_dir() and any(path.is_file() for path in raw.rglob("*"))
    return False


def raw_file(record: Any, rel_path: str) -> Path | None:
    """The document's file under the KB's ``raw/``, or ``None`` when it is gone."""
    if not rel_path:
        return None
    raw = _raw_dir(record).resolve()
    target = (raw / rel_path).resolve()
    if raw not in target.parents or not target.is_file():
        return None
    return target


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def find_document(kb_id: str, rel_path: str) -> Any | None:
    return registry.find_document(kb_id, rel_path)


def live_document(kb_id: str, doc_id: str) -> Any | None:
    """The live (not deleted) document with this fixed id, wherever it now lives.

    Moves keep the id (plan B ``move_document_paths``), so this is how every
    consumer finds a published document: never by the path captured in a
    snapshot, which may since hold an unrelated file.
    """
    if not doc_id:
        return None
    return next(
        (doc for doc in registry.list_documents(kb_id) if doc.doc_id == doc_id and not doc.deleted),
        None,
    )


def document_file(record: Any, doc_id: str) -> Path | None:
    """The current file of document *doc_id* in *record*, or ``None`` when unavailable."""
    doc = live_document(record.kb_id, doc_id)
    return raw_file(record, doc.rel_path) if doc is not None else None


def missing_documents(kb_ref: TemplateKbRef) -> list[TemplateDocRef]:
    """The documents a published KB reference binds that can no longer be read.

    A document is missing when its KB is gone, its id has no live entry, or its
    current file is absent. The captured :class:`TemplateDocRef` (path and
    version as published) is returned unchanged, for provenance.
    """
    record = resolve_id(kb_ref.kb_id)
    if record is None:
        return list(kb_ref.documents)
    return [doc for doc in kb_ref.documents if document_file(record, doc.doc_id) is None]


def record_document(kb_id: str, rel_path: str, path: Path) -> Any:
    """Give a document its fixed id now (plan B normally does it at indexing)."""
    return registry.upsert_documents(kb_id, {rel_path: file_hash(path)})[0]


def make_public(kb_id: str) -> None:
    """Publish a KB; plan B records the explicit public intent in the same writer."""
    registry.set_visibility(kb_id, "public")


def _merged(kb_refs: Iterable[TemplateKbRef]) -> list[tuple[str, list[str] | None]]:
    """One reference per KB: the whole base wins, otherwise the union of documents.

    Callers keeping older revisions usable (pending questions) pass the refs
    of every revision still applied; duplicates collapse here.
    """
    merged: dict[str, list[str] | None] = {}
    for ref in kb_refs:
        if ref.kb_id in merged and merged[ref.kb_id] is None:
            continue
        if ref.doc_ids is None:
            merged[ref.kb_id] = None
            continue
        merged[ref.kb_id] = list(dict.fromkeys([*(merged.get(ref.kb_id) or []), *ref.doc_ids]))
    return list(merged.items())


def replace_template_references(
    template_id: str,
    kb_refs: Iterable[TemplateKbRef],
    *,
    created_by: str,
    explicit_reference_only: Sequence[str] = (),
    readable_by: Any | None = None,
    retain_revoked_kb_ids: Sequence[str] = (),
    restore_kb_ids: Sequence[str] = (),
    restorable: Callable[[Any], bool] | None = None,
) -> None:
    """Declare what the template cites (plan B references), in one transaction.

    ``restore_kb_ids`` / ``restorable``: released KBs asked back, each kept
    only if ``restorable`` accepts its fresh record inside plan B's writer.

    ``explicit_reference_only`` must hold only the KB ids the publisher
    explicitly mapped to ``reference_only`` in its request: for those that are
    still private and writable, plan B records durable private intent in the
    same writer as the reference. Defaults and earlier choices are not consent.
    """
    options: dict[str, Any] = {}
    if explicit_reference_only:
        options["private_intent_kb_ids"] = list(explicit_reference_only)
    if readable_by is not None:
        options["readable_by"] = readable_by
    if retain_revoked_kb_ids:
        options["retain_revoked_kb_ids"] = list(retain_revoked_kb_ids)
    if restore_kb_ids:
        options["restore_kb_ids"] = list(restore_kb_ids)
        options["restorable"] = restorable
    registry.replace_references(
        REFERRER_KIND, template_id, _merged(kb_refs), created_by=created_by, **options
    )


def remove_template_references(template_id: str) -> None:
    registry.remove_references(REFERRER_KIND, template_id)


def referenced_kb_ids(template_id: str) -> set[str]:
    """The KBs the template's live references hold now (revoked ones excluded)."""
    return {record.kb_id for record in registry.list_referenced(REFERRER_KIND, template_id)}


def unavailable_references(template_id: str) -> list[Any]:
    """References an admin revoked (forced private or deleted the KB); spec 7.4.

    Revoked rows no longer grant access; republishing with the KB re-declares
    the reference and clears them (plan B).
    """
    return list(registry.list_unavailable_references(REFERRER_KIND, template_id))
