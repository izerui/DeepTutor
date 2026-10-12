"""Async adapter around the synchronous PageIndex SDK clients."""

from __future__ import annotations

import asyncio
from functools import lru_cache
import logging
import os
from pathlib import Path
import re
import shutil
from typing import Any

from deeptutor.services.provider_registry import find_by_name, strip_provider_prefix

from .config import PageIndexConfig

logger = logging.getLogger(__name__)

# Local indexing runs LLM calls with no deadline of their own. Each submit runs
# in a killable child process so a stalled file cannot hold a thread, the SDK
# store, or the files queued behind it. Cloud keeps the SDK's 30-minute wait.
DEFAULT_LOCAL_SUBMIT_TIMEOUT_SECONDS = 1800.0
_LOCAL_SUBMIT_TASK = "deeptutor.services.rag.pipelines.pageindex.client:submit_local_document"


def local_submit_timeout_seconds() -> float:
    """Per-file budget; override with PAGEINDEX_SUBMIT_TIMEOUT_SECONDS."""
    raw = os.environ.get("PAGEINDEX_SUBMIT_TIMEOUT_SECONDS", "").strip()
    try:
        value = float(raw) if raw else DEFAULT_LOCAL_SUBMIT_TIMEOUT_SECONDS
    except ValueError:
        value = DEFAULT_LOCAL_SUBMIT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_LOCAL_SUBMIT_TIMEOUT_SECONDS


@lru_cache(maxsize=1)
def _flash_structure_refusals() -> tuple[re.Pattern[str], ...]:
    """Patterns for the Flash refusals the SDK says standard mode can resolve.

    Built from the SDK's own refusal policy, so a reworded message still
    matches; without that helper the fallback is disabled instead of guessed.
    A missing text layer is deliberately excluded: standard mode reads the
    same text and cannot help there.
    """
    try:
        from pageindex.flash.api import FLAT_TREE_MAX_NODES, flash_rejection_reason
    except Exception:  # noqa: BLE001 - optional SDK internals
        return ()
    # The flat-tree refusal embeds the page count; a sentinel count marks it.
    sentinel = FLAT_TREE_MAX_NODES + 4321
    samples = (
        flash_rejection_reason({"toc_source": "pages", "structure": [{}] * sentinel}),
        flash_rejection_reason({"toc_source": "detected", "structure": []}),
    )
    return tuple(
        re.compile(re.escape(sample).replace(str(sentinel), r"\d+")) for sample in samples if sample
    )


def is_flash_structure_rejection(exc: BaseException) -> bool:
    """True only when the SDK itself refused a Flash tree for lacking structure."""
    raised_as = getattr(exc, "remote_type", None) or type(exc).__name__
    if raised_as != "PageIndexAPIError":
        return False
    message = str(exc)
    return any(pattern.search(message) for pattern in _flash_structure_refusals())


def _sdk_types():
    from pageindex import PageIndexCloudClient, PageIndexLocalClient

    return PageIndexCloudClient, PageIndexLocalClient


@lru_cache(maxsize=1)
def _cloud_sdk_client(api_key: str):
    """Reuse the SDK's MCP bridge until the global Cloud key changes."""
    cloud_type, _ = _sdk_types()
    return cloud_type(api_key)


def _prefixed_model(prefix: str, model: str) -> str:
    return model if model.startswith(f"{prefix}/") else f"{prefix}/{model}"


def resolve_oss_sdk_config() -> tuple[str, dict[str, Any]]:
    """Translate DeepTutor's active LLM into PageIndex's indexing lane."""
    from deeptutor.services.config import resolve_llm_runtime_config

    cfg = resolve_llm_runtime_config()
    model = str(getattr(cfg, "model", "") or "").strip()
    binding = str(
        getattr(cfg, "binding", None) or getattr(cfg, "provider_name", None) or "openai"
    ).strip()
    spec = find_by_name(binding)
    if not model:
        raise RuntimeError(
            "PageIndex OSS needs an active LLM. Configure one under Settings → Catalog."
        )
    if (
        spec is None
        or spec.is_oauth
        or spec.backend
        in {
            "openai_codex",
            "github_copilot",
            "codebuddy",
        }
    ):
        raise RuntimeError(
            "PageIndex OSS indexing needs an API-key or local LLM profile; "
            "the active OAuth-only provider cannot be used."
        )

    resolved_model = strip_provider_prefix(model, spec)
    prefix = {
        "anthropic": "anthropic",
        "azure_openai": "azure",
        "openai_compat": "openai",
    }.get(spec.backend)
    if prefix is None:
        raise RuntimeError("The active LLM transport is not supported by PageIndex OSS indexing.")

    sdk_model = _prefixed_model(prefix, resolved_model)
    backend: dict[str, Any] = {}
    api_key = str(getattr(cfg, "api_key", "") or "").strip()
    base_url = str(getattr(cfg, "base_url", "") or "").strip()
    api_version = str(getattr(cfg, "api_version", "") or "").strip()
    headers = getattr(cfg, "extra_headers", None)

    if spec.backend == "openai_compat":
        # PageIndex treats ``openai/...`` as the OpenAI-compatible fast path;
        # this also preserves gateway model ids such as anthropic/claude-*.
        backend["api_key"] = api_key or "sk-no-key-required"
    elif api_key:
        backend["api_key"] = api_key

    if base_url:
        backend["api_base"] = base_url
    if api_version and spec.backend != "openai_compat":
        backend["api_version"] = api_version
    if isinstance(headers, dict) and headers:
        if spec.backend == "openai_compat":
            backend["default_headers"] = dict(headers)
        else:
            backend["extra_headers"] = dict(headers)

    return sdk_model, backend


def _local_sdk_client(storage_path: str, model: str, backend: dict[str, Any]):
    _, local_type = _sdk_types()
    return local_type(
        storage_path=storage_path,
        index_model=model,
        summary_model=model,
        index_backend=backend,
    )


def submit_local_document(
    storage_path: str, model: str, backend: dict[str, Any], file_path: str, mode: str | None
) -> str:
    """Isolated-worker entry point: index one PDF into the Local Library."""
    result = _local_sdk_client(storage_path, model, backend).submit_document(file_path, mode=mode)
    doc_id = result.get("doc_id") if isinstance(result, dict) else None
    if not doc_id:
        raise RuntimeError(f"PageIndex submit_document returned no doc_id: {result!r}")
    return str(doc_id)


class PageIndexClient:
    """Small async facade used by the DeepTutor RAG lifecycle."""

    def __init__(self, sdk_client: Any, *, local_spec: dict[str, Any] | None = None) -> None:
        self.sdk_client = sdk_client
        # Set for indexing-capable Local clients: submits run out of process.
        self._local_spec = local_spec

    @classmethod
    def cloud(cls, config: PageIndexConfig) -> "PageIndexClient":
        return cls(_cloud_sdk_client(config.api_key))

    @classmethod
    def local(cls, storage_path: str | Path) -> "PageIndexClient":
        model, backend = resolve_oss_sdk_config()
        spec = {"storage_path": str(storage_path), "model": model, "backend": backend}
        return cls(_local_sdk_client(**spec), local_spec=spec)

    @classmethod
    def local_read(cls, storage_path: str | Path) -> "PageIndexClient":
        """Open an existing Local Library without resolving indexing credentials."""
        _, local_type = _sdk_types()
        return cls(local_type(storage_path=str(storage_path)))

    async def submit_document(self, file_path: str | Path, *, mode: str | None = None) -> str:
        if self._local_spec is not None:
            return await self._submit_local_isolated(str(file_path), mode)
        result = await asyncio.to_thread(
            self.sdk_client.submit_document,
            str(file_path),
            mode=mode,
            wait=True,
        )
        doc_id = result.get("doc_id") if isinstance(result, dict) else None
        if not doc_id:
            raise RuntimeError(f"PageIndex submit_document returned no doc_id: {result!r}")
        return str(doc_id)

    async def delete_document(self, doc_id: str) -> bool:
        await asyncio.to_thread(self.sdk_client.delete_document, doc_id)
        return True

    async def _submit_local_isolated(self, file_path: str, mode: str | None) -> str:
        from deeptutor.runtime.isolated_worker import (
            IsolatedWorkerTimeout,
            run_in_isolated_process,
        )

        spec = self._local_spec or {}
        timeout = local_submit_timeout_seconds()
        before = await asyncio.to_thread(self._local_snapshot)
        try:
            return await run_in_isolated_process(
                _LOCAL_SUBMIT_TASK,
                spec["storage_path"],
                spec["model"],
                spec["backend"],
                file_path,
                mode,
                timeout=timeout,
            )
        except IsolatedWorkerTimeout as exc:
            await self._discard_before_returning(before)
            raise TimeoutError(
                f"PageIndex processing timed out after {timeout:g}s and was stopped"
            ) from exc
        except BaseException:
            # SDK errors, crashes and cancellation alike: the worker tree is
            # already reaped, so whatever it stored for this file is orphaned.
            await self._discard_before_returning(before)
            raise

    async def _discard_before_returning(self, before: set[str]) -> None:
        """Finish the store cleanup even if cancelled again, then re-raise."""
        cleanup = asyncio.ensure_future(asyncio.to_thread(self._discard_new_docs, before))
        cancelled = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError

    def _local_root(self) -> Path:
        return Path((self._local_spec or {})["storage_path"])

    def _local_snapshot(self) -> set[str]:
        """Listed documents plus raw entries of the SDK's on-disk layout.

        ``save_document`` writes ``docs/<id>/tree.json`` and ``pages.json``
        before ``doc.json``, and ``list_documents`` skips directories without
        it; a killed or failed save is visible only on disk. Atomic writes
        leave ``*.tmp`` siblings when killed mid-write.
        """
        root = self._local_root()
        docs = root / "docs"
        entries = {f"doc:{doc_id}" for doc_id in self._local_doc_ids()}
        if docs.is_dir():
            entries.update(f"doc:{entry.name}" for entry in docs.iterdir() if entry.is_dir())
        if root.is_dir():
            entries.update(f"tmp:{entry.name}" for entry in root.glob("*.tmp"))
        return entries

    def _local_doc_ids(self) -> set[str]:
        ids: set[str] = set()
        offset = 0
        while True:
            page = self.sdk_client.list_documents(limit=100, offset=offset)
            docs = page.get("documents") or []
            ids.update(str(doc["id"]) for doc in docs if doc.get("id"))
            offset += len(docs)
            if not docs or offset >= int(page.get("total") or 0):
                return ids

    def _discard_new_docs(self, before: set[str]) -> None:
        try:
            root = self._local_root()
            for entry in sorted(self._local_snapshot() - before):
                kind, _, name = entry.partition(":")
                if kind == "tmp":
                    (root / name).unlink(missing_ok=True)
                    continue
                try:
                    # Also removes an incomplete directory, then reports it
                    # as not found because doc.json was never written.
                    self.sdk_client.delete_document(name)
                except Exception:
                    pass
                leftover = root / "docs" / name
                if leftover.is_dir():
                    shutil.rmtree(leftover, ignore_errors=True)
        except Exception:
            logger.warning("PageIndex: could not discard an interrupted submit", exc_info=True)

__all__ = [
    "PageIndexClient",
    "resolve_oss_sdk_config",
]
