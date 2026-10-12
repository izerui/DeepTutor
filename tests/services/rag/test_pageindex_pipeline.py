"""Unit tests for PageIndex SDK lifecycle and provider routing.

The pipeline talks to PageIndex's REST API through an injectable client, so we
    exercise the orchestration against a fake client without network calls.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from deeptutor.services.rag.factory import get_pipeline, normalize_provider_name
from deeptutor.services.rag.index_versioning import resolve_storage_dir_for_read
from deeptutor.services.rag.pipelines.pageindex import client as client_mod
from deeptutor.services.rag.pipelines.pageindex import storage
from deeptutor.services.rag.pipelines.pageindex.client import PageIndexClient
from deeptutor.services.rag.pipelines.pageindex.config import PageIndexConfig
from deeptutor.services.rag.pipelines.pageindex.pipeline import (
    PageIndexPipeline,
    is_supported_file,
)


class FakeClient:
    """Stand-in for :class:`PageIndexClient` — records calls, no network."""

    def __init__(self) -> None:
        self.submitted: list[str] = []
        self.deleted: list[str] = []
        self.modes: list[str | None] = []

    async def submit_document(self, file_path, *, mode=None) -> str:
        self.submitted.append(str(file_path))
        self.modes.append(mode)
        return f"pi-{Path(file_path).name}"

    async def delete_document(self, doc_id) -> bool:
        self.deleted.append(doc_id)
        return True


def _pipe(tmp_path, client) -> PageIndexPipeline:
    return PageIndexPipeline(kb_base_dir=str(tmp_path), client=client)


def _manifest(tmp_path, kb_name) -> dict:
    sdir = resolve_storage_dir_for_read(Path(tmp_path) / kb_name, None)
    return storage.read_manifest(sdir)


def test_is_supported_file() -> None:
    # Mirrors PageIndex POST /doc/ accepted formats.
    for name in ("a.pdf", "b.MD", "c.markdown", "d.docx", "e.txt", "f.XLSX", "g.pptx", "h.csv"):
        assert is_supported_file(name), name
    assert not is_supported_file("i.png")
    assert not is_supported_file("j.zip")  # containers are unpacked upstream
    assert is_supported_file("a.PDF", "pageindex-oss")
    assert not is_supported_file("b.docx", "pageindex-oss")


def test_initialize_submits_supported_and_skips_others(tmp_path) -> None:
    client = FakeClient()
    pdf = tmp_path / "a.pdf"
    pdf.write_text("x")
    docx = tmp_path / "b.docx"
    docx.write_text("y")
    png = tmp_path / "c.png"
    png.write_text("z")

    ok = asyncio.run(_pipe(tmp_path, client).initialize("kb1", [str(pdf), str(docx), str(png)]))

    assert ok is True
    assert sorted(Path(p).name for p in client.submitted) == ["a.pdf", "b.docx"]
    docs = _manifest(tmp_path, "kb1")["docs"]
    assert set(docs) == {"a.pdf", "b.docx"}
    assert docs["a.pdf"]["doc_id"] == "pi-a.pdf"


def test_initialize_receipt_includes_only_published_manifest_files(tmp_path: Path) -> None:
    client = FakeClient()
    first = tmp_path / "first" / "same.pdf"
    second = tmp_path / "second" / "same.pdf"
    skipped = tmp_path / "skipped.png"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    skipped.write_text("image", encoding="utf-8")
    receipts: list[list[str]] = []

    assert asyncio.run(
        _pipe(tmp_path, client).initialize(
            "kb",
            [str(first), str(second), str(skipped)],
            indexed_file_callback=receipts.append,
        )
    )

    assert receipts == [[str(second)]]
    assert set(_manifest(tmp_path, "kb")["docs"]) == {"same.pdf"}


def test_initialize_no_supported_returns_false(tmp_path) -> None:
    client = FakeClient()
    png = tmp_path / "c.png"
    png.write_text("z")
    ok = asyncio.run(_pipe(tmp_path, client).initialize("kb2", [str(png)]))
    assert ok is False
    assert client.submitted == []


def test_add_documents_appends_to_manifest(tmp_path) -> None:
    client = FakeClient()
    pipe = _pipe(tmp_path, client)
    a = tmp_path / "a.pdf"
    a.write_text("x")
    asyncio.run(pipe.initialize("kb", [str(a)]))

    b = tmp_path / "b.pdf"
    b.write_text("y")
    ok = asyncio.run(pipe.add_documents("kb", [str(b)]))

    assert ok is True
    assert set(_manifest(tmp_path, "kb")["docs"]) == {"a.pdf", "b.pdf"}


def test_oss_accepts_pdf_only_and_forwards_explicit_mode(tmp_path, monkeypatch) -> None:
    client = FakeClient()
    pipe = PageIndexPipeline(
        kb_base_dir=str(tmp_path),
        client=client,
        provider="pageindex-oss",
    )
    monkeypatch.setattr(pipe, "_processing_mode", lambda _kb: "standard")
    pdf = tmp_path / "a.pdf"
    pdf.write_text("x")
    docx = tmp_path / "b.docx"
    docx.write_text("y")

    assert asyncio.run(pipe.initialize("oss", [str(pdf), str(docx)])) is True
    assert [Path(path).name for path in client.submitted] == ["a.pdf"]
    assert client.modes == ["standard"]
    assert _manifest(tmp_path, "oss")["provider"] == "pageindex-oss"


def test_search_requires_reasoning_as_retrieval(tmp_path) -> None:
    client = FakeClient()
    pipe = _pipe(tmp_path, client)
    pdf = tmp_path / "a.pdf"
    pdf.write_text("x")
    asyncio.run(pipe.initialize("kb", [str(pdf)]))

    res = asyncio.run(pipe.search("what?", "kb"))

    assert res["provider"] == "pageindex"
    assert res["error_type"] == "reasoning_as_retrieval_required"
    assert res["content"] == ""
    assert res["sources"] == []


def test_document_map_exposes_manifest(tmp_path) -> None:
    client = FakeClient()
    pipe = _pipe(tmp_path, client)
    pdf = tmp_path / "a.pdf"
    pdf.write_text("x")
    asyncio.run(pipe.initialize("kb", [str(pdf)]))

    assert pipe.document_map("kb") == {"a.pdf": "pi-a.pdf"}
    assert pipe.document_map("missing-kb") == {}


def test_search_without_documents_still_requires_agent_loop(tmp_path) -> None:
    res = asyncio.run(_pipe(tmp_path, FakeClient()).search("q", "missing-kb"))
    assert res["error_type"] == "reasoning_as_retrieval_required"
    assert res["sources"] == []
    assert res["provider"] == "pageindex"


def test_delete_drops_cloud_docs_and_local_dir(tmp_path) -> None:
    client = FakeClient()
    pipe = _pipe(tmp_path, client)
    a = tmp_path / "a.pdf"
    a.write_text("x")
    asyncio.run(pipe.initialize("kb", [str(a)]))

    ok = asyncio.run(pipe.delete("kb"))

    assert ok is True
    assert "pi-a.pdf" in client.deleted
    assert not (tmp_path / "kb").exists()


def test_remove_document_updates_sdk_and_manifest(tmp_path) -> None:
    client = FakeClient()
    pipe = _pipe(tmp_path, client)
    pdf = tmp_path / "a.pdf"
    pdf.write_text("x")
    asyncio.run(pipe.initialize("kb", [str(pdf)]))

    assert asyncio.run(pipe.remove_document("kb", "a.pdf")) is True
    assert client.deleted == ["pi-a.pdf"]
    assert _manifest(tmp_path, "kb")["docs"] == {}


def test_factory_dispatches_by_provider(tmp_path, monkeypatch) -> None:
    # Constructing a real LlamaIndexPipeline resolves the active embedding model
    # from the catalog; CI has none, so stub the settings hook (the same way the
    # llamaindex pipeline tests do) — this test only asserts factory routing.
    from deeptutor.services.rag.pipelines.llamaindex.pipeline import LlamaIndexPipeline

    monkeypatch.setattr(LlamaIndexPipeline, "_configure_settings", lambda self: None)

    assert (
        type(get_pipeline("pageindex", kb_base_dir=str(tmp_path))).__name__ == "PageIndexPipeline"
    )
    oss = get_pipeline("pageindex-oss", kb_base_dir=str(tmp_path))
    assert type(oss).__name__ == "PageIndexPipeline"
    assert oss.provider == "pageindex-oss"
    assert (
        type(get_pipeline("llamaindex", kb_base_dir=str(tmp_path))).__name__ == "LlamaIndexPipeline"
    )
    # Legacy / unknown providers fall back to the default engine.
    assert (
        type(get_pipeline("raganything", kb_base_dir=str(tmp_path))).__name__
        == "LlamaIndexPipeline"
    )
    assert normalize_provider_name("raganything") == "llamaindex"


def test_ragservice_resolves_provider_from_metadata(tmp_path) -> None:
    from deeptutor.services.rag.service import RAGService

    kb = tmp_path / "kbx"
    kb.mkdir()
    (kb / "metadata.json").write_text(json.dumps({"rag_provider": "pageindex"}), encoding="utf-8")

    svc = RAGService(kb_base_dir=str(tmp_path))
    assert svc._resolve_provider("kbx") == "pageindex"
    # Unknown KB → default.
    assert svc._resolve_provider("nope") == "llamaindex"
    # Explicit override wins over metadata.
    svc_override = RAGService(kb_base_dir=str(tmp_path), provider="llamaindex")
    assert svc_override._resolve_provider("kbx") == "llamaindex"


def test_ragservice_guard_prevents_pageindex_pipeline(tmp_path, monkeypatch) -> None:
    from deeptutor.services.rag.service import RAGService

    service = RAGService(kb_base_dir=str(tmp_path), provider="pageindex-oss")
    monkeypatch.setattr(
        service,
        "_get_pipeline",
        lambda *_args, **_kwargs: pytest.fail("PageIndex reached a traditional RAG pipeline"),
    )

    result = asyncio.run(service.search(query="ground this", kb_name="page-kb"))

    assert result["error_type"] == "reasoning_as_retrieval_required"


def test_cloud_sdk_client_is_reused_until_api_key_changes(monkeypatch) -> None:
    created: list[str] = []

    class _CloudClient:
        def __init__(self, api_key: str) -> None:
            created.append(api_key)

    monkeypatch.setattr(client_mod, "_sdk_types", lambda: (_CloudClient, object))
    client_mod._cloud_sdk_client.cache_clear()
    try:
        first = PageIndexClient.cloud(PageIndexConfig("key-a")).sdk_client
        second = PageIndexClient.cloud(PageIndexConfig("key-a")).sdk_client
        rotated = PageIndexClient.cloud(PageIndexConfig("key-b")).sdk_client
    finally:
        client_mod._cloud_sdk_client.cache_clear()

    assert first is second
    assert rotated is not first
    assert created == ["key-a", "key-b"]


def test_submit_document_uses_sdk_wait() -> None:
    calls: list[tuple[str, str | None, bool]] = []

    class _SDKClient:
        def submit_document(self, path: str, *, mode: str | None, wait: bool):
            calls.append((path, mode, wait))
            return {"doc_id": "doc-1"}

    doc_id = asyncio.run(PageIndexClient(_SDKClient()).submit_document("a.pdf", mode="flash"))

    assert doc_id == "doc-1"
    assert calls == [("a.pdf", "flash", True)]


def test_pageindex_ready_omits_embedding_identity(tmp_path, monkeypatch) -> None:
    import deeptutor.knowledge.manager as manager_module
    from deeptutor.knowledge.manager import KnowledgeBaseManager
    from deeptutor.services.rag import embedding_signature as signature_module

    pdf = tmp_path / "manual.pdf"
    pdf.write_text("x")
    asyncio.run(
        PageIndexPipeline(
            kb_base_dir=str(tmp_path),
            client=FakeClient(),
            provider="pageindex-oss",
        ).initialize("page-kb", [str(pdf)])
    )
    manager = KnowledgeBaseManager(base_dir=str(tmp_path))
    manager.config.setdefault("knowledge_bases", {})["page-kb"] = {
        "path": "page-kb",
        "rag_provider": "pageindex-oss",
        "embedding_model": "stale-model",
        "embedding_dim": 123,
        "embedding_signature": "stale-signature",
        "embedding_mismatch": True,
    }
    manager._save_config()
    monkeypatch.setattr(manager_module, "_get_embedding_fingerprint", lambda: ("active", 456))
    monkeypatch.setattr(
        signature_module,
        "signature_from_embedding_config",
        lambda: type("Signature", (), {"hash": lambda self: "active-signature"})(),
    )

    manager.update_kb_status("page-kb", "ready")

    entry = manager._load_config()["knowledge_bases"]["page-kb"]
    assert (
        not {
            "embedding_model",
            "embedding_dim",
            "embedding_signature",
            "embedding_mismatch",
        }
        & entry.keys()
    )
    assert manager.get_info("page-kb")["statistics"]["active_signature"] is None


# ---------------------------------------------------------------------------
# Fault tolerance: one bad file must not abort the entire index
# ---------------------------------------------------------------------------


class FailOnSecondClient(FakeClient):
    """Raises on a specific file name, succeeds for the rest."""

    def __init__(self, fail_name: str) -> None:
        super().__init__()
        self.fail_name = fail_name

    async def submit_document(self, file_path, *, mode=None) -> str:
        if Path(file_path).name == self.fail_name:
            raise RuntimeError("PDF has no content. All pages are blank.")
        return await super().submit_document(file_path, mode=mode)


def test_ingest_skips_failed_file_and_indexes_rest(tmp_path) -> None:
    """A file that the SDK rejects (e.g. blank PDF) is skipped; siblings succeed."""
    client = FailOnSecondClient("bad.pdf")
    good = tmp_path / "good.pdf"
    bad = tmp_path / "bad.pdf"
    also_good = tmp_path / "also_good.pdf"
    for f in (good, bad, also_good):
        f.write_text("x")

    ok = asyncio.run(
        _pipe(tmp_path, client).initialize("kb", [str(good), str(bad), str(also_good)])
    )

    assert ok is True
    assert sorted(Path(p).name for p in client.submitted) == ["also_good.pdf", "good.pdf"]
    docs = _manifest(tmp_path, "kb")["docs"]
    assert set(docs) == {"good.pdf", "also_good.pdf"}
    assert "bad.pdf" not in docs


def test_ingest_all_files_fail_returns_false(tmp_path) -> None:
    """When every file fails, initialize returns False (no usable index)."""
    client = FailOnSecondClient("only.pdf")
    only = tmp_path / "only.pdf"
    only.write_text("x")

    ok = asyncio.run(_pipe(tmp_path, client).initialize("kb", [str(only)]))

    assert ok is False
    assert client.submitted == []


async def _initialize_in_run(tmp_path, client, names, *, create=True):
    from deeptutor.knowledge.indexing_run import IndexingRun

    raw = tmp_path / "kb" / "raw"
    paths = []
    for name in names:
        if create:
            raw.mkdir(parents=True, exist_ok=True)
            (raw / name).write_text("x")
        paths.append(str(raw / name))
    async with IndexingRun(tmp_path / "kb", paths, task_id="t") as run:
        ok = await _pipe(tmp_path, client).initialize("kb", paths)
        return ok, {key: dict(doc) for key, doc in run.data["documents"].items()}


def test_failed_file_is_recorded_in_indexing_run(tmp_path) -> None:
    """The recovery panel shows which file was skipped and why."""
    client = FailOnSecondClient("bad.pdf")

    ok, docs = asyncio.run(_initialize_in_run(tmp_path, client, ["good.pdf", "bad.pdf"]))

    assert ok is True
    assert docs["bad.pdf"]["status"] == "failed"
    assert docs["bad.pdf"]["reason"] == "invalid_output"
    assert "All pages are blank" in docs["bad.pdf"]["detail"]
    assert docs["good.pdf"]["status"] == "parsed"


def test_ragservice_add_documents_partial_success_is_reported(tmp_path, monkeypatch) -> None:
    """Incremental adds confirm successes, so the run ends partial, not completed."""
    from deeptutor.knowledge.indexing_run import load_run
    from deeptutor.services.rag.service import RAGService

    kb = tmp_path / "kb"
    raw = kb / "raw"
    raw.mkdir(parents=True)
    (kb / "metadata.json").write_text(json.dumps({"rag_provider": "pageindex"}), encoding="utf-8")
    good, bad = raw / "good.pdf", raw / "bad.pdf"
    for f in (good, bad):
        f.write_text("x")
    service = RAGService(kb_base_dir=str(tmp_path), provider="pageindex")
    pipe = _pipe(tmp_path, FailOnSecondClient("bad.pdf"))
    monkeypatch.setattr(service, "_get_pipeline", lambda _provider: pipe)
    received: list[str] = []

    ok = asyncio.run(
        service.add_documents(
            kb_name="kb", file_paths=[str(good), str(bad)], indexed_file_callback=received.extend
        )
    )

    assert ok is True
    assert received == [str(good)]
    run = load_run(kb)
    assert run["state"] == "partial"
    assert run["documents"]["good.pdf"]["status"] == "completed"
    assert run["documents"]["bad.pdf"]["status"] == "failed"
    assert "All pages are blank" in run["documents"]["bad.pdf"]["detail"]


# ---------------------------------------------------------------------------
# Local submits run in a killable worker: a fake ``pageindex`` package on
# PYTHONPATH reaches the child, whose blocking call cannot be cancelled. It
# writes the real SDK's on-disk layout, and the parent inspects and cleans it
# with the real ``PageIndexLocalClient``.
# ---------------------------------------------------------------------------

_FAKE_SDK = """
import json, multiprocessing, os, signal, sys, time, uuid
from pathlib import Path


def _flood_stderr_and_block(_signum, _frame):
    # SDK shutdown logging far beyond the pipe buffer, then still stuck.
    for _ in range(256):
        sys.stderr.write("shutdown log " + "x" * 4096 + "\\n")
    sys.stderr.flush()
    time.sleep(3600)


def _save(storage_path, name, *, complete=True):
    # DocStore.save_document order: tree.json, pages.json, then doc.json.
    doc_id = "pi-" + uuid.uuid4().hex
    doc_dir = Path(storage_path, "docs", doc_id)
    doc_dir.mkdir(parents=True)
    (doc_dir / "tree.json").write_text("[]")
    (doc_dir / "pages.json").write_text(json.dumps([{"page_index": 1, "markdown": "x" * 4096}]))
    if complete:
        meta = {"id": doc_id, "name": name, "description": None, "status": "completed",
                "createdAt": "2026-01-01T00:00:00Z", "pageNum": 1, "folderId": None,
                "metadata": None, "mode": "flash"}
        (doc_dir / "doc.json").write_text(json.dumps(meta))
    return doc_id


class PageIndexLocalClient:
    def __init__(self, storage_path, **_kwargs):
        self.storage_path = storage_path
        Path(storage_path).mkdir(parents=True, exist_ok=True)

    def submit_document(self, file_path, mode=None):
        name = Path(file_path).name
        if name.startswith("fail"):
            _save(self.storage_path, name, complete=False)  # partial save, then error
            raise RuntimeError("Failed to submit document: LLM response was malformed")
        if name.startswith("hang"):
            if "noisy" in name:
                signal.signal(signal.SIGTERM, _flood_stderr_and_block)
            # Like Flash's spawn pool for long PDFs: the stall sits in a worker
            # the SDK started, which inherits the isolated child's pipes.
            pool = multiprocessing.get_context("spawn").Process(target=time.sleep, args=(3600,))
            pool.start()
            Path(self.storage_path, name + ".pid").write_text(f"{os.getpid()} {pool.pid}")
            _save(self.storage_path, name)  # a complete late write
            _save(self.storage_path, name, complete=False)  # and one killed mid-save
            time.sleep(3600)  # synchronous: no event loop can cancel this
            _save(self.storage_path, name)
        return {"doc_id": _save(self.storage_path, name), "name": name}


class PageIndexCloudClient:
    pass
"""


@pytest.fixture
def isolated_local_client(tmp_path, monkeypatch):
    from pageindex import PageIndexLocalClient as RealLocalClient

    sdk_root = tmp_path / "fake_sdk"
    (sdk_root / "pageindex").mkdir(parents=True)
    (sdk_root / "pageindex" / "__init__.py").write_text(_FAKE_SDK)
    repo_root = Path(__file__).resolve().parents[3]
    paths = [str(sdk_root), str(repo_root), os.environ.get("PYTHONPATH", "")]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(paths))
    monkeypatch.setenv("PAGEINDEX_SUBMIT_TIMEOUT_SECONDS", "2")
    store = tmp_path / "store"
    store.mkdir()
    spec = {"storage_path": str(store), "model": "openai/test", "backend": {}}
    client = PageIndexClient(RealLocalClient(storage_path=str(store)), local_spec=spec)
    return client, store


def _stored(store: Path) -> dict[str, str | None]:
    """Every doc directory on disk -> its name, or None for an incomplete save."""
    docs = store / "docs"
    if not docs.is_dir():
        return {}
    out: dict[str, str | None] = {}
    for doc_dir in docs.iterdir():
        meta = doc_dir / "doc.json"
        out[doc_dir.name] = json.loads(meta.read_text())["name"] if meta.is_file() else None
    return out


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _assert_tree_reaped(pid_file: Path) -> None:
    """Both the isolated child and the worker it spawned have exited."""
    import time

    pids = [int(pid) for pid in pid_file.read_text().split()]
    assert len(pids) == 2
    deadline = time.monotonic() + 5
    while any(_pid_alive(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not [pid for pid in pids if _pid_alive(pid)]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX liveness probe")
def test_local_timeout_kills_worker_and_discards_its_writes(
    tmp_path, isolated_local_client
) -> None:
    import time

    client, store = isolated_local_client
    hang = tmp_path / "hang.pdf"
    hang.write_text("x")

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="timed out"):
        asyncio.run(client.submit_document(hang))

    assert time.monotonic() - started < 15
    _assert_tree_reaped(store / "hang.pdf.pid")
    # Both the listed write and the save killed before doc.json are gone.
    assert _stored(store) == {}
    time.sleep(0.5)
    assert _stored(store) == {}  # and nothing writes afterwards


def test_local_error_after_partial_write_discards_it(tmp_path, isolated_local_client) -> None:
    """A plain SDK error is also an exit that must not leave a partial save."""
    from deeptutor.runtime.isolated_worker import IsolatedWorkerError

    client, store = isolated_local_client
    for name in ("kept.pdf", "fail.pdf", "next.pdf"):
        (tmp_path / name).write_text("x")
    kept_id = asyncio.run(client.submit_document(tmp_path / "kept.pdf"))

    with pytest.raises(IsolatedWorkerError, match="malformed"):
        asyncio.run(client.submit_document(tmp_path / "fail.pdf"))

    assert _stored(store) == {kept_id: "kept.pdf"}
    next_id = asyncio.run(client.submit_document(tmp_path / "next.pdf"))
    assert _stored(store) == {kept_id: "kept.pdf", next_id: "next.pdf"}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX liveness probe")
def test_stalled_local_files_do_not_starve_later_files(tmp_path, isolated_local_client) -> None:
    import threading

    client, store = isolated_local_client
    raw = tmp_path / "kb" / "raw"
    raw.mkdir(parents=True)
    names = ["hang1.pdf", "hang2.pdf", "hang3-noisy.pdf", "fail.pdf", "ok.pdf"]
    for name in names:
        (raw / name).write_text("x")
    threads_before = threading.active_count()

    ok, docs = asyncio.run(_initialize_in_run(tmp_path, client, names, create=False))

    assert ok is True
    for name in names[:3]:
        assert docs[name]["status"] == "failed"
        assert docs[name]["reason"] == "timeout"
        _assert_tree_reaped(store / f"{name}.pid")
    assert docs["fail.pdf"]["status"] == "failed"
    assert docs["ok.pdf"]["status"] == "parsed"
    assert set(_manifest(tmp_path, "kb")["docs"]) == {"ok.pdf"}
    assert list(_stored(store).values()) == ["ok.pdf"]
    assert threading.active_count() <= threads_before + 1


def test_local_submit_timeout_env_parsing(monkeypatch) -> None:
    default = client_mod.DEFAULT_LOCAL_SUBMIT_TIMEOUT_SECONDS
    monkeypatch.delenv("PAGEINDEX_SUBMIT_TIMEOUT_SECONDS", raising=False)
    assert client_mod.local_submit_timeout_seconds() == default
    monkeypatch.setenv("PAGEINDEX_SUBMIT_TIMEOUT_SECONDS", "5")
    assert client_mod.local_submit_timeout_seconds() == 5
    for bad in ("0", "-1", "abc"):
        monkeypatch.setenv("PAGEINDEX_SUBMIT_TIMEOUT_SECONDS", bad)
        assert client_mod.local_submit_timeout_seconds() == default


# ---------------------------------------------------------------------------
# Flash -> standard fallback: only the SDK's own "no structure" refusals, once.
# Refusal texts come from the installed SDK's policy, so a reworded SDK message
# is exercised here instead of a frozen copy.
# ---------------------------------------------------------------------------


def _sdk_refusal(result: dict) -> Exception:
    from pageindex.flash.api import flash_rejection_reason

    from deeptutor.runtime.isolated_worker import IsolatedWorkerError

    reason = flash_rejection_reason(result)
    assert reason, result
    # What the isolated worker re-raises for local_api's PageIndexAPIError.
    return IsolatedWorkerError(
        f"Failed to submit document: {reason}",
        remote_module="pageindex.errors",
        remote_type="PageIndexAPIError",
    )


def _flat_tree_refusal() -> Exception:
    from pageindex.flash.api import FLAT_TREE_MAX_NODES

    pages = [{"title": f"Page {i}"} for i in range(FLAT_TREE_MAX_NODES + 16)]
    return _sdk_refusal({"toc_source": "pages", "structure": pages})


def _empty_tree_refusal() -> Exception:
    return _sdk_refusal({"toc_source": "detected", "structure": []})


def _no_text_layer_refusal() -> Exception:
    return _sdk_refusal({"toc_source": "unreadable", "structure": []})


class ScriptedClient(FakeClient):
    """Raises the scripted exception for a (file, mode) pair; succeeds otherwise."""

    def __init__(self, script: dict[tuple[str, str | None], Exception]) -> None:
        super().__init__()
        self.script = script
        self.calls: list[tuple[str, str | None]] = []

    async def submit_document(self, file_path, *, mode=None) -> str:
        key = (Path(file_path).name, mode)
        self.calls.append(key)
        if key in self.script:
            raise self.script[key]
        return await super().submit_document(file_path, mode=mode)


def _oss_pipe(tmp_path, client, mode=None) -> PageIndexPipeline:
    pipe = PageIndexPipeline(kb_base_dir=str(tmp_path), client=client, provider="pageindex-oss")
    pipe._processing_mode = lambda _kb: mode
    return pipe


def test_structure_refusal_matcher_uses_the_sdk_policy() -> None:
    from deeptutor.runtime.isolated_worker import IsolatedWorkerError
    from deeptutor.services.rag.pipelines.pageindex.client import is_flash_structure_rejection

    assert is_flash_structure_rejection(_flat_tree_refusal())
    assert is_flash_structure_rejection(_empty_tree_refusal())
    assert not is_flash_structure_rejection(_no_text_layer_refusal())
    # Same wording from something other than the SDK's refusal is not trusted.
    text = str(_flat_tree_refusal())
    assert not is_flash_structure_rejection(
        IsolatedWorkerError(text, remote_module="litellm", remote_type="APIConnectionError")
    )
    assert not is_flash_structure_rejection(RuntimeError(text))
    assert not is_flash_structure_rejection(
        IsolatedWorkerError(
            "Failed to submit document: try mode='standard'",
            remote_module="pageindex.errors",
            remote_type="PageIndexAPIError",
        )
    )


@pytest.mark.parametrize("refusal", [_flat_tree_refusal, _empty_tree_refusal])
@pytest.mark.parametrize("mode", [None, "flash"])
def test_flash_structure_refusal_falls_back_to_standard_once(tmp_path, refusal, mode) -> None:
    client = ScriptedClient({("exam.pdf", mode): refusal()})
    raw = tmp_path / "kb" / "raw"
    raw.mkdir(parents=True)
    (raw / "exam.pdf").write_text("x")

    async def run_once():
        from deeptutor.knowledge.indexing_run import IndexingRun

        paths = [str(raw / "exam.pdf")]
        async with IndexingRun(tmp_path / "kb", paths, task_id="t") as run:
            ok = await _oss_pipe(tmp_path, client, mode).initialize("kb", paths)
            return ok, dict(run.data["documents"]["exam.pdf"])

    ok, doc = asyncio.run(run_once())

    assert ok is True
    assert client.calls == [("exam.pdf", mode), ("exam.pdf", "standard")]
    assert set(_manifest(tmp_path, "kb")["docs"]) == {"exam.pdf"}
    assert doc["status"] == "parsed"
    assert "standard" in doc["detail"]


def test_standard_fallback_is_not_repeated(tmp_path) -> None:
    pdf = tmp_path / "exam.pdf"
    pdf.write_text("x")
    client = ScriptedClient(
        {("exam.pdf", None): _flat_tree_refusal(), ("exam.pdf", "standard"): _empty_tree_refusal()}
    )

    ok = asyncio.run(_oss_pipe(tmp_path, client).initialize("kb", [str(pdf)]))

    assert ok is False
    assert client.calls == [("exam.pdf", None), ("exam.pdf", "standard")]


@pytest.mark.parametrize(
    "error",
    [
        _no_text_layer_refusal,
        lambda: __import__("deeptutor.runtime.isolated_worker", fromlist=["x"]).IsolatedWorkerError(
            "litellm.APIConnectionError: Connection reset by peer",
            remote_module="litellm",
            remote_type="APIConnectionError",
        ),
    ],
    ids=["no-text-layer", "network-error"],
)
def test_other_failures_do_not_fall_back(tmp_path, error) -> None:
    pdf = tmp_path / "scan.pdf"
    pdf.write_text("x")
    client = ScriptedClient({("scan.pdf", None): error()})

    ok = asyncio.run(_oss_pipe(tmp_path, client).initialize("kb", [str(pdf)]))

    assert ok is False
    assert client.calls == [("scan.pdf", None)]


def test_kb_already_in_standard_mode_is_not_retried(tmp_path) -> None:
    pdf = tmp_path / "exam.pdf"
    pdf.write_text("x")
    client = ScriptedClient({("exam.pdf", "standard"): _empty_tree_refusal()})

    ok = asyncio.run(_oss_pipe(tmp_path, client, "standard").initialize("kb", [str(pdf)]))

    assert ok is False
    assert client.calls == [("exam.pdf", "standard")]


def test_cloud_provider_does_not_fall_back(tmp_path) -> None:
    pdf = tmp_path / "exam.pdf"
    pdf.write_text("x")
    client = ScriptedClient({("exam.pdf", None): _flat_tree_refusal()})

    ok = asyncio.run(_pipe(tmp_path, client).initialize("kb", [str(pdf)]))

    assert ok is False
    assert client.calls == [("exam.pdf", None)]


def test_cancel_before_standard_fallback_is_not_reported_as_failure(tmp_path) -> None:
    """A cancel requested during the Flash attempt stops before standard runs."""
    from deeptutor.knowledge.indexing_run import IndexingCancelled, IndexingRun, request_cancel

    kb_dir = tmp_path / "kb"
    raw = kb_dir / "raw"
    raw.mkdir(parents=True)
    (raw / "exam.pdf").write_text("x")
    paths = [str(raw / "exam.pdf")]

    class CancelDuringFlash(ScriptedClient):
        async def submit_document(self, file_path, *, mode=None) -> str:
            if mode is None:
                assert request_cancel(kb_dir, "t")  # the user clicks cancel meanwhile
            return await super().submit_document(file_path, mode=mode)

    client = CancelDuringFlash({("exam.pdf", None): _flat_tree_refusal()})
    seen: dict[str, dict] = {}

    async def run_once():
        async with IndexingRun(kb_dir, paths, task_id="t") as run:
            try:
                await _oss_pipe(tmp_path, client).initialize("kb", paths)
            finally:
                seen.update({k: dict(v) for k, v in run.data["documents"].items()})

    with pytest.raises(IndexingCancelled):
        asyncio.run(run_once())

    assert client.calls == [("exam.pdf", None)]
    assert seen["exam.pdf"]["status"] != "failed"
    assert "reason" not in seen["exam.pdf"] or not seen["exam.pdf"]["reason"]
