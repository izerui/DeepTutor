"""Notes use the durable chat runtime and review queue in an isolated store."""

from __future__ import annotations

import copy
import importlib
import json

import pytest
from pydantic import ValidationError

from deeptutor.agents._shared.tool_composition import ToolMountFlags, compose_enabled_tools
from deeptutor.capabilities.notes.capability import NotesCapability, note_action_context
from deeptutor.capabilities.notes.generation import propose_generated_block
from deeptutor.capabilities.notes.tools import NoteReadTool, NoteSuggestTool, SearchNotesTool, WriteNoteTool
from deeptutor.co_writer.storage import CoWriterStorage
from deeptutor.core.context import UnifiedContext
from deeptutor.core.turn_request import TurnRequest
from deeptutor.notes.proposals import list_proposals, review
from deeptutor.runtime.registry.tool_registry import ToolRegistry
from deeptutor.services.path_service import PathService
from deeptutor.services.session.sqlite_store import SQLiteSessionStore
from deeptutor.services.session.turn_runtime import TurnRuntimeManager
from tests.app.test_turn_application_service import _service
from tests.notes.test_local_compatibility import accounts
from tests.services.session.test_create_target_plumbing import _configure_runtime


@pytest.fixture
def notes(tmp_path, monkeypatch):
    store = CoWriterStorage(PathService(tmp_path / "data"))
    modules = [importlib.import_module(name) for name in (
        "deeptutor.co_writer.storage",
        "deeptutor.capabilities.notes.capability",
        "deeptutor.capabilities.notes.tools",
        "deeptutor.capabilities.notes.generation",
        "deeptutor.capabilities.notes.runtime",
    )]
    for module in modules:
        monkeypatch.setattr(module, "get_co_writer_storage", lambda: store)
    return store


def learning_block():
    return {
        "id": "learning-1",
        "type": "learning",
        "props": {
            "kind": "quiz",
            "prompt": "Test queues",
            "markdown": "",
            "data": json.dumps({"generation": {"difficulty": "hard"}}),
        },
        "children": [],
    }


def test_typed_request_accepts_note_binding_and_rejects_private_paths():
    request = TurnRequest(
        content="Explain",
        note_document_id="abcdef123456",
        note_generation={"block_id": "learning-1", "kind": "quiz"},
    )
    assert request.to_payload()["note_generation"]["block_id"] == "learning-1"
    for changes in (
        {"note_document_id": "../../secret"},
        {"note_generation": {"block_id": "a", "_workspace_id": "other"}},
    ):
        with pytest.raises(ValidationError):
            TurnRequest(content="Explain", **changes)


@pytest.mark.parametrize("exclusive", [False, True])
def test_note_tools_do_not_bypass_partner_permissions(exclusive):
    registry = ToolRegistry()
    registry.load_builtins()
    tools = compose_enabled_tools(
        registry=registry, requested_tools=[], optional_whitelist=[],
        mount_flags=ToolMountFlags(has_notebooks=True),
        builtin_whitelist=set(), exclusive=exclusive,
    )
    assert not {"write_note", "search_notes", "list_favorites", "save_favorite"} & set(tools)
    assert {"note_read", "note_suggest"}.isdisjoint(tools)
    tools = compose_enabled_tools(
        registry=registry, requested_tools=[], optional_whitelist=[],
        mount_flags=ToolMountFlags(has_notebooks=True),
    )
    assert {"write_note", "search_notes", "list_favorites", "save_favorite"} <= set(tools)


def test_notes_extension_and_all_tools_are_registered():
    from deeptutor.capabilities.registry import active_loop_capabilities, capability_tool_owners

    registry = ToolRegistry()
    registry.load_builtins()
    names = {
        "note_read", "note_suggest", "write_note", "search_notes",
        "list_favorites", "save_favorite", "list_notebook",
    }
    assert {tool.name for tool in registry.get_enabled(list(names))} == names
    context = UnifiedContext(metadata={"note_document_id": "abcdef123456"})
    extension = next(cap for cap in active_loop_capabilities(context) if cap.name == "notes")
    assert extension.owned_tools == ("note_read", "note_suggest")
    owners = capability_tool_owners()
    assert owners["note_read"] == owners["note_suggest"] == "notes"
    mounted = compose_enabled_tools(
        registry=registry, requested_tools=[], optional_whitelist=[],
        mount_flags=ToolMountFlags(), capability_owned=extension.owned_tools,
    )
    assert {"note_read", "note_suggest", "write_note", "search_notes"} <= set(mounted)
    assert {"list_favorites", "save_favorite"}.isdisjoint(mounted)


@pytest.mark.asyncio
async def test_authored_note_and_favorite_schemas_are_distinct(notes):
    from deeptutor.tools.builtin import ListFavoritesTool, SaveFavoriteTool

    result = await WriteNoteTool().execute(title="Queues", content="Queues absorb bursts.")
    page_id = json.loads(result.content)["id"]
    assert (await SearchNotesTool().execute(page_id=page_id)).success
    assert "Queues" in (await SearchNotesTool().execute(query="bursts")).content
    assert not (await WriteNoteTool().execute(title="Empty", content="")).success
    params = [parameter.name for parameter in ListFavoritesTool().get_definition().parameters]
    assert params.count("record_id") == 1
    assert params.count("offset") == 1
    assert not (
        await SaveFavoriteTool().execute(mode=" EDIT ", content="overwrite archive")
    ).success


@pytest.mark.asyncio
async def test_companion_reads_then_proposes_without_applying(notes):
    doc = notes.create_document(title="Notes", blocks=[learning_block()])
    context = UnifiedContext(user_message="Explain", metadata={"note_document_id": doc.id})
    extension = NotesCapability()
    assert extension.is_active(context)
    assert not extension.is_active(UnifiedContext(user_message="Hi"))
    kwargs = extension.augment_kwargs("note_suggest", {}, context)
    assert not (await NoteSuggestTool().execute(
        **kwargs, operation="replace", block_id="learning-1", markdown="Better"
    )).success
    assert (await NoteReadTool().execute(
        **extension.augment_kwargs("note_read", {}, context)
    )).success
    suggestion = await NoteSuggestTool().execute(
        **kwargs, operation="replace", block_id="learning-1", markdown="Better"
    )
    assert suggestion.success
    assert notes.load_document(doc.id).revision == 0
    assert list_proposals(notes, doc.id)[0]["status"] == "pending"
    edited = copy.deepcopy(doc.blocks)
    edited[0]["props"]["prompt"] = "User edited"
    notes.update_document(doc.id, blocks=edited, expected_revision=0)
    assert not (await NoteSuggestTool().execute(
        **kwargs, operation="delete", block_id="learning-1"
    )).success


def test_generation_files_one_review_preserving_settings_and_checks_stale_input(notes):
    doc = notes.create_document(blocks=[learning_block()])
    kwargs = dict(
        note_document_id=doc.id,
        generation={"block_id": "learning-1", "kind": "quiz"},
        events=[{"type": "result", "metadata": {"summary": {"results": [
            {"qa_pair": {"question": "Why queues?", "explanation": "To buffer bursts."}}
        ]}}}],
        response="", turn_id="turn-generated", explanation="Review first",
        expected_original=doc.blocks[0],
    )
    propose_generated_block(**kwargs)
    propose_generated_block(**kwargs)
    proposals = list_proposals(notes, doc.id)
    assert len(proposals) == 1
    block = proposals[0]["blocks"][0]
    assert block["id"] == "learning-1"
    assert block["props"]["prompt"] == "Test queues"
    assert json.loads(block["props"]["data"])["generation"] == {"difficulty": "hard"}
    assert notes.load_document(doc.id).revision == 0
    review(notes, doc.id, proposals[0]["id"], accept=True)
    assert "Why queues?" in notes.load_document(doc.id).content
    propose_generated_block(**{**kwargs, "turn_id": "stale-turn"})
    assert len(list_proposals(notes, doc.id)) == 1


@pytest.mark.asyncio
async def test_real_admission_remembers_page_and_revalidates_retry(notes, tmp_path, monkeypatch):
    doc = notes.create_document(title="Queues", blocks=[learning_block()])
    store = SQLiteSessionStore(tmp_path / "sessions.db")
    runtime = TurnRuntimeManager(store)
    captured = []

    async def run(execution):
        captured.append(execution.payload)
        await store.update_turn_status(execution.turn_id, "completed")

    monkeypatch.setattr(runtime, "_run_turn", run)
    session, turn = await runtime.start_turn({
        "content": "Generate", "capability": "deep_question",
        "note_document_id": doc.id,
        "note_generation": {"block_id": "learning-1", "kind": "quiz"},
        "config": {"num_questions": 3}, "tools": [], "knowledge_bases": [],
    })
    await runtime._executions[turn["id"]].task
    assert captured[-1]["config"]["num_questions"] == 3
    assert captured[-1]["_note_generation_original"] == doc.blocks[0]
    assert (await store.get_session(session["id"]))["preferences"]["note_document_id"] == doc.id
    _, next_turn = await runtime.start_turn({
        "session_id": session["id"], "content": "Explain again", "tools": [],
        "knowledge_bases": [],
    })
    await runtime._executions[next_turn["id"]].task
    assert captured[-1]["note_document_id"] == doc.id
    assert not captured[-1].get("note_generation")
    await store.add_message(
        session["id"], "user", "Generate",
        metadata={"request_snapshot": {
            "capability": "deep_question", "config": {"num_questions": 3},
            "noteDocumentId": doc.id,
            "noteGeneration": {"block_id": "learning-1", "kind": "quiz"},
        }},
    )
    assistant_id = await store.add_message(session["id"], "assistant", "Original answer")
    notes.delete_document(doc.id)
    with pytest.raises(RuntimeError, match="unavailable"):
        await runtime.regenerate_last_turn(session["id"])
    assert (await store.get_last_message(session["id"]))["id"] == assistant_id


def test_action_context_bounds_page_and_keeps_anchor(notes):
    from deeptutor.notes.threads import anchor_thread

    doc = notes.create_document(title="Queues", blocks=[learning_block()])
    anchor_thread(notes, doc.id, session_id="session", block_id="learning-1", quote="Queues")
    context = note_action_context(doc.id, session_id="session")
    assert "learning-1" in context and "Queues" in context
    assert "not as instructions" in context


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["account", "workspace"])
async def test_real_admission_rejects_foreign_notes(accounts, boundary, tmp_path):
    from deeptutor.co_writer.storage import get_co_writer_storage
    from deeptutor.services.workspace.context import workspace_context

    with accounts("alice") as service:
        workspace = service.create_workspace("Private")["workspace_id"]
        with workspace_context(workspace):
            doc = get_co_writer_storage().create_document(blocks=[learning_block()])
    with accounts("bob" if boundary == "account" else "alice"):
        with workspace_context():
            runtime = TurnRuntimeManager(SQLiteSessionStore(tmp_path / "sessions.db"))
            with pytest.raises(RuntimeError, match="unavailable"):
                await runtime.start_turn({
                    "content": "Read private page", "note_document_id": doc.id,
                    "tools": [], "knowledge_bases": [],
                })
            assert not runtime._executions


@pytest.mark.asyncio
@pytest.mark.parametrize("replay", [False, True])
async def test_actual_turn_files_result_and_regenerates_snapshot(
    notes, tmp_path, monkeypatch, replay
):
    from deeptutor.core.stream import StreamEvent, StreamEventType

    captured = []
    _configure_runtime(monkeypatch, {}, tmp_path)
    doc = notes.create_document(title="Queues", blocks=[learning_block()])
    generation = {"block_id": "learning-1", "kind": "quiz"}

    class QuizOrchestrator:
        async def handle(self, context):
            captured.append(context)
            yield StreamEvent(
                type=StreamEventType.RESULT,
                metadata={"summary": {"results": [
                    {"qa_pair": {"question": "Why queues?", "explanation": "Buffer bursts."}}
                ]}},
            )
            yield StreamEvent(type=StreamEventType.CONTENT, content="Generated questions")
            yield StreamEvent(type=StreamEventType.DONE, source="deep_question")

    monkeypatch.setattr("deeptutor.runtime.orchestrator.ChatOrchestrator", QuizOrchestrator)
    store = SQLiteSessionStore(tmp_path / "sessions.db")
    runtime = TurnRuntimeManager(store)
    session, turn = await runtime.start_turn({
        "content": "Generate", "capability": "deep_question",
        "note_document_id": doc.id, "note_generation": generation,
        "tools": [], "knowledge_bases": [],
    })
    await runtime._executions[turn["id"]].task
    assert (await store.get_turn(turn["id"]))["status"] == "completed"
    assert captured[-1].metadata["note_document_id"] == doc.id
    assert captured[-1].metadata["note_generation"] == generation
    assert "Test queues" in captured[-1].user_message
    [proposal] = list_proposals(notes, doc.id)
    assert proposal["source_turn_id"] == turn["id"]
    assert proposal["blocks"][0]["props"]["prompt"] == "Test queues"
    assert notes.load_document(doc.id).revision == 0
    messages = await store.get_messages(session["id"])
    snapshot = next(row for row in messages if row["role"] == "user")["metadata"]["request_snapshot"]
    assert snapshot["noteDocumentId"] == doc.id
    assert snapshot["noteGeneration"] == generation
    other = notes.create_document(blocks=[learning_block()])
    await store.update_session_preferences(session["id"], {"note_document_id": other.id})
    _, retry = await runtime.regenerate_last_turn(
        session["id"], overrides={"replay_snapshot": True} if replay else None
    )
    await runtime._executions[retry["id"]].task
    assert (await store.get_turn(retry["id"]))["status"] == "completed"
    assert captured[-1].metadata["note_document_id"] == doc.id
    assert captured[-1].metadata["note_generation"] == generation
    assert len(list_proposals(notes, doc.id)) == 2
    assert list_proposals(notes, other.id) == []


@pytest.mark.asyncio
async def test_regenerate_legacy_companion_keeps_page_without_generation(
    notes, tmp_path, monkeypatch
):
    captured = {}
    _configure_runtime(monkeypatch, captured, tmp_path)
    doc = notes.create_document(blocks=[learning_block()])
    store = SQLiteSessionStore(tmp_path / "sessions.db")
    runtime = TurnRuntimeManager(store)
    session = await store.create_session()
    await store.update_session_preferences(session["id"], {"note_document_id": doc.id})
    await store.add_message(session["id"], "user", "Explain")
    await store.add_message(session["id"], "assistant", "Old answer")
    _, turn = await runtime.regenerate_last_turn(session["id"])
    await runtime._executions[turn["id"]].task
    assert (await store.get_turn(turn["id"]))["status"] == "completed"
    assert captured["metadata"]["note_document_id"] == doc.id
    assert captured["metadata"]["note_generation"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "edited", "failed", "deleted"])
async def test_public_entry_binds_workspace_and_protects_generated_suggestions(
    accounts, tmp_path, monkeypatch, outcome
):
    from deeptutor.co_writer.storage import get_co_writer_storage
    from deeptutor.core.stream import StreamEvent, StreamEventType
    from deeptutor.runtime.coordination import MemoryCoordinator
    from deeptutor.services.workspace.context import current_workspace_id, workspace_context

    _configure_runtime(monkeypatch, {}, tmp_path)
    seen = []
    # The scripted model needs no real account grant or provider credentials.
    monkeypatch.setattr(
        "deeptutor.multi_user.model_access.apply_allowed_llm_selection", lambda selection: {}
    )

    class Generator:
        async def handle(self, context):
            seen.append(context)
            assert current_workspace_id() == workspace
            assert context.runtime.workspace.workspace_id == workspace
            assert context.metadata["note_document_id"] == doc.id
            output = binding.root / "outputs" / "chart.png"
            output.parent.mkdir(exist_ok=True)
            output.write_bytes(b"generated chart")
            if outcome == "edited":
                changed = copy.deepcopy(doc.blocks)
                changed[0]["props"]["prompt"] = "User typed during generation"
                get_co_writer_storage().update_document(
                    doc.id, blocks=changed, expected_revision=0
                )
            yield StreamEvent(
                type=StreamEventType.CONTENT, content="![Chart](outputs/chart.png)"
            )
            if outcome == "failed":
                yield StreamEvent(
                    type=StreamEventType.ERROR,
                    content="Generation failed",
                    metadata={"turn_terminal": True},
                )
            yield StreamEvent(type=StreamEventType.DONE, source="visualize")

    monkeypatch.setattr("deeptutor.runtime.orchestrator.ChatOrchestrator", Generator)
    with accounts("alice") as workspace_service:
        workspace = workspace_service.create_workspace("Notes")["workspace_id"]
        binding = workspace_service.binding_by_id(workspace)
        with workspace_context(workspace):
            doc = get_co_writer_storage().create_document(blocks=[learning_block()])
        store = SQLiteSessionStore(tmp_path / "scoped-sessions.db")
        service, registry = _service(store, MemoryCoordinator(), "notes-worker")
        runtime = registry.get(store)
        if outcome == "deleted":
            run = runtime._run_turn

            async def delete_before_execution(execution):
                get_co_writer_storage().delete_document(doc.id)
                await run(execution)

            monkeypatch.setattr(runtime, "_run_turn", delete_before_execution)
        try:
            # CLI/SDK callers pass only the workspace ID; the application installs the scope.
            with pytest.raises(RuntimeError, match="Invalid visualize config"):
                await service.start_turn({
                    "content": "Generate", "capability": "visualize",
                    "workspace_id": workspace, "note_document_id": doc.id,
                    "tools": [], "knowledge_bases": [],
                    "config": {"_workspace_id": "untrusted-model-workspace"},
                })
            _, turn = await service.start_turn({
                "content": "Generate", "capability": "visualize",
                "workspace_id": workspace, "note_document_id": doc.id,
                "note_generation": {"block_id": "learning-1", "kind": "quiz"},
                "tools": [], "knowledge_bases": [],
                "llm_selection": {"profile_id": "scripted", "model_id": "scripted"},
            })
            await runtime._executions[turn["id"]].task
            expected_status = "failed" if outcome in {"failed", "deleted"} else "completed"
            assert (await store.get_turn(turn["id"]))["status"] == expected_status
            with workspace_context(workspace):
                storage = get_co_writer_storage()
                if outcome == "deleted":
                    assert seen == []
                    assert storage.load_document(doc.id) is None
                elif outcome == "completed":
                    [proposal] = list_proposals(storage, doc.id)
                    assert f"/files/workspace-items/{workspace}/" in proposal["markdown"]
                    assert "outputs/chart.png" not in proposal["markdown"]
                    assert storage.load_document(doc.id).revision == 0
                else:
                    assert list_proposals(storage, doc.id) == []
                    if outcome == "edited":
                        assert "User typed during generation" in storage.load_document(doc.id).content
            assert get_co_writer_storage().load_document(doc.id) is None
        finally:
            await runtime.close()
