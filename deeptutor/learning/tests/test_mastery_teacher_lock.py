"""The tutor cannot redesign an outline that belongs to a teacher."""

from __future__ import annotations

from pathlib import Path

import pytest

from deeptutor.capabilities.mastery.mode import admission_error
from deeptutor.learning.models import (
    KnowledgePoint,
    KnowledgeType,
    LearningModule,
    PathOrigin,
)
from deeptutor.learning.storage import LearningStore
from deeptutor.tools.mastery_tool import MasteryBuildTool, MasteryModeTool, MasteryReviseTool

NEW_MODULES = [
    {
        "name": "Something else",
        "objective": "Do something else",
        "knowledge_points": [{"name": "Anything goes", "type": "concept"}],
    }
]


@pytest.fixture
def store(tmp_path, monkeypatch):
    def _init(self, root_arg=None):
        self._root = Path(tmp_path) / "learning"
        self._root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(LearningStore, "__init__", _init)
    return LearningStore()


@pytest.fixture
def instance(store):
    module = LearningModule(
        id="m0",
        name="Calculus",
        order=0,
        objective="Differentiate",
        knowledge_points=[
            KnowledgePoint(id="kp0", name="Limits", type=KnowledgeType.CONCEPT, module_id="m0")
        ],
    )

    def seed(tx):
        tx.progress.origin = PathOrigin(template_id="tpl_x", revision=1)
        tx.progress.modules = [module]
        tx.progress.knowledge_types["kp0"] = KnowledgeType.CONCEPT
        tx.progress.current_module_id = "m0"
        tx.touch()

    store.mutate("assigned_tpl_x", seed, create=True)
    return "assigned_tpl_x"


@pytest.mark.asyncio
async def test_build_is_refused_on_a_teacher_path(instance, store):
    result = await MasteryBuildTool().execute(
        _mastery_path_id=instance, _mastery_session_mode="outline", modules=NEW_MODULES
    )

    assert result.success is False
    assert "teacher" in result.content
    assert [p.id for p in store.load(instance).modules[0].knowledge_points] == ["kp0"]


@pytest.mark.asyncio
async def test_revise_is_refused_on_a_teacher_path(instance, store):
    result = await MasteryReviseTool().execute(
        _mastery_path_id=instance,
        _mastery_session_mode="outline",
        module_id="m0",
        remove=["kp0"],
    )

    assert result.success is False
    assert "teacher" in result.content
    assert [p.id for p in store.load(instance).modules[0].knowledge_points] == ["kp0"]


@pytest.mark.asyncio
async def test_mode_tool_refuses_outline_but_allows_review(instance):
    refused = await MasteryModeTool().execute(
        _mastery_path_id=instance, _mastery_session_mode="study", mode="outline"
    )
    allowed = await MasteryModeTool().execute(
        _mastery_path_id=instance, _mastery_session_mode="study", mode="review"
    )

    assert refused.success is False
    assert "teacher" in refused.content
    assert allowed.success is True


@pytest.mark.asyncio
async def test_learners_own_paths_still_build(store):
    result = await MasteryBuildTool().execute(
        _mastery_path_id="own_path",
        _mastery_session_mode="outline",
        path_name="Mine",
        modules=NEW_MODULES,
    )

    assert result.success is True


def test_admission_error_names_the_teacher_only_for_locked_outlines():
    assert "teacher" in admission_error("outline", has_outline=True, outline_locked=True)
    assert admission_error("study", has_outline=True, outline_locked=True) == ""
    assert admission_error("outline", has_outline=True) == ""
