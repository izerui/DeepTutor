"""Write-time guards on revision-fenced book edits (port recheck M1, M4, M5)."""

import asyncio
from types import SimpleNamespace

import pytest

import deeptutor.book.authoring as authoring
from deeptutor.book.authoring import BookEdit, apply_edit, preview_edit
from deeptutor.book.models import Progress
from deeptutor.services.workspace.models import WorkspaceError
from tests.book.test_chapter_crud import _seed, _storage


def _files(storage, book_id="bk"):
    root = storage.book_root(book_id)
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_preview_and_apply_recheck_writability_at_write_time(tmp_path, monkeypatch):
    storage = _storage(tmp_path)
    _seed(storage)
    proposal = preview_edit(
        storage, "bk", BookEdit(action="rename_page", page_id="pg_0", title="Renamed")
    )
    before = _files(storage)

    def archived(_storage):
        raise WorkspaceError("Restore the source workspace before changing this book.")

    # The route admitted the request earlier; the source was archived since.
    monkeypatch.setattr(authoring, "require_content_writable", archived)
    with pytest.raises(ValueError, match="Restore the source workspace"):
        preview_edit(storage, "bk", BookEdit(action="rename_page", page_id="pg_1", title="X"))
    with pytest.raises(ValueError, match="Restore the source workspace"):
        apply_edit(storage, "bk", proposal["id"], 1)
    assert _files(storage) == before


def test_structural_apply_and_undo_prune_removed_pages_from_progress(tmp_path):
    storage = _storage(tmp_path)
    _seed(storage)
    added = preview_edit(storage, "bk", BookEdit(action="add_page", chapter_id="ch_0", title="New"))
    apply_edit(storage, "bk", added["id"], 1)
    new_id = next(p.id for p in storage.list_pages("bk") if p.title == "New")
    storage.save_progress(
        Progress(
            book_id="bk",
            current_page_id=new_id,
            visited_page_ids=["pg_0", new_id],
            bookmarked_page_ids=[new_id, "pg_1"],
            weak_chapters=["ch_0", "ch_1"],
        )
    )

    # Undoing the add removes the page again: the progress must forget it.
    apply_edit(storage, "bk", added["id"], 2, undo=True)
    progress = storage.load_progress("bk")
    assert progress.current_page_id == ""
    assert progress.visited_page_ids == ["pg_0"]
    assert progress.bookmarked_page_ids == ["pg_1"]

    # Deleting a whole chapter also drops it from the weak chapters.
    revision = storage.load_book("bk").revision
    removed = preview_edit(storage, "bk", BookEdit(action="delete_chapter", chapter_id="ch_1"))
    apply_edit(storage, "bk", removed["id"], revision)
    progress = storage.load_progress("bk")
    assert progress.weak_chapters == ["ch_0"]
    assert progress.bookmarked_page_ids == []  # pg_1 went with its chapter
    assert progress.visited_page_ids == ["pg_0"]


def test_non_structural_apply_leaves_progress_alone(tmp_path):
    storage = _storage(tmp_path)
    _seed(storage)
    storage.save_progress(Progress(book_id="bk", current_page_id="pg_0", visited_page_ids=["pg_0"]))
    proposal = preview_edit(
        storage, "bk", BookEdit(action="extend_page", page_id="pg_0", body="More")
    )
    apply_edit(storage, "bk", proposal["id"], 1)
    progress = storage.load_progress("bk")
    assert progress.current_page_id == "pg_0"
    assert progress.visited_page_ids == ["pg_0"]


@pytest.mark.asyncio
async def test_book_mutation_lock_blocks_queueing_generation_and_retires(tmp_path):
    storage = _storage(tmp_path)
    engine = _seed(storage)
    queued = asyncio.Event()

    async with engine.book_mutation_lock("bk"):
        runtime = engine._runtimes["bk"]
        assert runtime.lock.locked()

        async def enqueue():
            async with runtime.lock:
                queued.set()

        task = asyncio.create_task(enqueue())
        await asyncio.sleep(0.01)
        assert not queued.is_set()  # generation waits for the edit
    await asyncio.wait_for(task, 1)
    assert queued.is_set()
    # A runtime made only for the edit does not linger on an idle book.
    assert "bk" not in engine._runtimes


def test_editable_authoring_scope_needs_edit_permission(monkeypatch):
    import deeptutor.multi_user.book_access as access
    from deeptutor.book.authoring_tool import editable_authoring_scope

    context = SimpleNamespace(
        metadata={"book_references": [{"book_id": "bk", "page_ids": ["pg_0"]}]}
    )
    for can_edit, expected in ((True, {"book_id": "bk", "page_id": "pg_0"}), (False, None)):
        monkeypatch.setattr(
            access, "resolve_book", lambda book_id, ce=can_edit: SimpleNamespace(can_edit=ce)
        )
        assert editable_authoring_scope(context) == expected
    monkeypatch.setattr(access, "resolve_book", lambda book_id: None)
    assert editable_authoring_scope(context) is None


@pytest.mark.asyncio
async def test_pipeline_mounts_propose_book_edit_only_for_editors(monkeypatch):
    """A reader of someone else's book is not offered the tool (M4)."""
    import deeptutor.multi_user.book_access as access
    from deeptutor.agents.loop.pipeline import AgenticLoopPipeline
    from deeptutor.core.context import UnifiedContext
    from deeptutor.runtime.providers.view import ProviderToolView

    async def providers(self, context):
        self._tool_view = ProviderToolView.empty(self.registry)
        self._deferred_loader = None
        self._deferred_pool = []

    monkeypatch.setattr(AgenticLoopPipeline, "_prepare_deferred_tools", providers)
    pipeline = AgenticLoopPipeline(language="en")
    context = UnifiedContext(
        session_id="synthetic",
        metadata={"book_references": [{"book_id": "bk", "page_ids": ["pg_0"]}]},
    )
    await pipeline._prepare_deferred_tools(context)
    for can_edit in (True, False):
        monkeypatch.setattr(
            access, "resolve_book", lambda book_id, ce=can_edit: SimpleNamespace(can_edit=ce)
        )
        names = pipeline._compose_enabled_tools(context)
        assert ("propose_book_edit" in names) is can_edit
