"""A learning account's Mastery Path turn reads the topic's knowledge bases — only those it may.

The learning policy strips every turn's ``tools`` and ``knowledge_bases``. A
mastery turn must not depend on either: the pipeline re-attaches the KBs the
topic was built from, and ``rag`` mounts because a KB is attached, not because
the learner toggled it. If that regresses, the learner can pick a KB when
creating the goal and the tutor then cannot read a word of it.

The re-attachment goes through ``learning_source_access``, which lifts the
*workspace selection* filter for the topic's refs. It must not lift the
*registry* check: a saved topic pointing at an admin KB that is not public (or
has since been withdrawn) stays unreadable inside the turn.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from deeptutor.agents.loop.pipeline import AgenticLoopPipeline
from deeptutor.capabilities.mastery.pipeline import MasteryLoopPipeline
from deeptutor.capabilities.mastery.tools import MASTERY_TOOL_NAMES
from deeptutor.core.context import UnifiedContext
from deeptutor.learning.models import TopicSource, TopicSourceKind
from deeptutor.multi_user.grants import learner_grant, save_grant
from deeptutor.multi_user.knowledge_access import resolve_for_rag, resolve_kb_manifest
from deeptutor.multi_user.learning_access import apply_learning_policy
from deeptutor.services.workspace.knowledge import learning_source_kbs
from deeptutor.tools.rag_tool import rag_search

ASSIGNED = "admin:kb:calculus"
UNASSIGNED = "admin:kb:answer-keys"


def _kb_source(index: int, ref: str) -> TopicSource:
    return TopicSource(
        id=f"src-{index}",
        kind=TopicSourceKind.KNOWLEDGE_BASE,
        source_id=ref,
        label=ref,
        position=index,
    )


@pytest.fixture
def learner(mu_isolated_root, seed_user, monkeypatch):
    from deeptutor.knowledge import registry
    from deeptutor.multi_user.knowledge_access import admin_kb_base_dir

    admin = seed_user("admin", role="admin")
    user = seed_user("student")
    save_grant(user["id"], learner_grant(user["id"]))
    # Grants no longer hand out KBs; a public admin KB is what every learner reads.
    registry.register_kb(
        base_dir=admin_kb_base_dir(),
        name="calculus",
        kind="uploaded",
        created_by=admin["id"],
        visibility="public",
    )
    registry.register_kb(
        base_dir=admin_kb_base_dir(), name="answer-keys", kind="uploaded", created_by=admin["id"]
    )
    monkeypatch.setattr("deeptutor.capabilities.setup.binding._intro_pending", lambda _c: False)
    return user


def _topic_with(monkeypatch, *refs: str) -> None:
    topic = SimpleNamespace(sources=[_kb_source(i, ref) for i, ref in enumerate(refs)])
    monkeypatch.setattr(
        "deeptutor.learning.storage.LearningStore.get_topic",
        lambda _self, path_id: topic if path_id == "p1" else None,
    )


def _replace_model_loop(monkeypatch, probe) -> None:
    """Run the real mastery pipeline up to the model loop, then ``probe`` instead."""

    async def _loop(self, context, _stream):
        await probe(self, context)
        return {}

    monkeypatch.setattr(AgenticLoopPipeline, "run", _loop)


async def _run_learner_mastery_turn(as_user, learner) -> None:
    with as_user(learner["id"], username="student"):
        payload = apply_learning_policy(
            {
                "capability": "mastery_path",
                "tools": ["web_search"],
                "knowledge_bases": [ASSIGNED, UNASSIGNED],
            }
        )
        assert payload["tools"] == []
        assert payload["knowledge_bases"] == []

        context = UnifiedContext(
            enabled_tools=payload["tools"],
            knowledge_bases=payload["knowledge_bases"],
            metadata={"mastery_mode": True, "mastery_path_id": "p1"},
        )
        await MasteryLoopPipeline(language="zh").run(context, None)


@pytest.mark.asyncio
async def test_learner_mastery_turn_mounts_rag_for_the_topics_knowledge_base(
    learner, as_user, monkeypatch
):
    _topic_with(monkeypatch, ASSIGNED)
    observed: dict = {}

    async def _probe(pipeline, context):
        observed["knowledge_bases"] = list(context.knowledge_bases)
        observed["readable"] = set(learning_source_kbs.get())
        observed["tools"] = pipeline._compose_enabled_tools(context)
        observed["resource"] = resolve_for_rag(ASSIGNED)

    _replace_model_loop(monkeypatch, _probe)
    await _run_learner_mastery_turn(as_user, learner)

    assert observed["knowledge_bases"] == [ASSIGNED]
    assert ASSIGNED in observed["readable"]
    assert "rag" in observed["tools"]
    assert set(MASTERY_TOOL_NAMES) <= set(observed["tools"])
    assert "web_search" not in observed["tools"]
    # Read-only, even though the topic lifted the workspace selection for it.
    assert observed["resource"].assigned is True
    assert observed["resource"].read_only is True


@pytest.mark.asyncio
async def test_learner_mastery_turn_cannot_read_an_unassigned_admin_kb_from_its_topic(
    learner, as_user, monkeypatch
):
    # The topic was saved with a KB the account does not hold — chosen while it
    # was still assigned, or written by hand. Being a topic source is not access.
    _topic_with(monkeypatch, ASSIGNED, UNASSIGNED)
    observed: dict = {}

    async def _probe(_pipeline, _context):
        observed["readable"] = set(learning_source_kbs.get())
        with pytest.raises(HTTPException) as denied:
            resolve_for_rag(UNASSIGNED)
        observed["status"] = denied.value.status_code
        # The tool's own entry point, before any index is touched.
        with pytest.raises(HTTPException):
            await rag_search("chain rule", kb_name=UNASSIGNED)
        observed["manifest"] = resolve_kb_manifest(UNASSIGNED)
        observed["assigned"] = resolve_for_rag(ASSIGNED)

    _replace_model_loop(monkeypatch, _probe)
    await _run_learner_mastery_turn(as_user, learner)

    # Scoped into the turn by the topic, and still refused by the account check.
    assert UNASSIGNED in observed["readable"]
    assert observed["status"] == 403
    assert observed["manifest"] is None
    # The rejection is per KB: the assigned one beside it still resolves.
    assert observed["assigned"] is not None
    assert observed["assigned"].assigned is True
