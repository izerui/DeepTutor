"""Regression gates for the import-cheap built-in descriptors."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import subprocess
import sys

from deeptutor.runtime.bootstrap.builtin_capabilities import BUILTIN_CAPABILITY_SPECS
from deeptutor.tools.builtin_specs import BUILTIN_TOOL_SPECS


def test_builtin_tool_descriptors_match_implementations() -> None:
    for spec in BUILTIN_TOOL_SPECS:
        assert spec.create().name == spec.name


def test_builtin_capability_descriptors_match_implementations() -> None:
    for name, spec in BUILTIN_CAPABILITY_SPECS.items():
        module_name, class_name = spec.class_path.rsplit(":", 1)
        actual = getattr(importlib.import_module(module_name), class_name).manifest
        expected = spec.manifest
        assert actual.name == name
        for field in (
            "description",
            "stages",
            "tools_used",
            "cli_aliases",
            "config_defaults",
            "reads_user_inputs",
        ):
            assert getattr(actual, field) == getattr(expected, field), (name, field)


#: Built-ins whose turn runs the shared agent loop (``AgentLoop._run_loop``),
#: which reads the turn inbox before every model call. Any other capability
#: never reads a message sent mid-turn, so it must not claim to.
_INBOX_READERS = {
    "chat",
    "ask_questions",
    "deep_solve",
    "mastery_path",
    "immersive_reading",
    "course_study",
}


def test_only_agent_loop_capabilities_declare_that_they_read_mid_turn_messages() -> None:
    from deeptutor.runtime.registry.capability_registry import CapabilityRegistry

    declared = {
        name for name, spec in BUILTIN_CAPABILITY_SPECS.items() if spec.manifest.reads_user_inputs
    }
    assert declared == _INBOX_READERS

    registry = CapabilityRegistry()
    registry.load_builtins()
    assert all(registry.reads_user_inputs(name) for name in _INBOX_READERS)
    assert not registry.reads_user_inputs("deep_research")
    assert not registry.reads_user_inputs("video_lesson")
    assert not registry.reads_user_inputs("no_such_capability")
    exported = {row["name"]: row["reads_user_inputs"] for row in registry.get_manifests()}
    assert {name for name, reads in exported.items() if reads} == _INBOX_READERS


def test_builtin_catalog_imports_keep_implementations_cold() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    probe = """
import json
import sys

import deeptutor.capabilities.registry
import deeptutor.runtime.registry.capability_registry
import deeptutor.runtime.registry.tool_registry

forbidden = (
    "deeptutor.agents.chat.agentic_pipeline",
    "deeptutor.capabilities.solve.loop",
    "deeptutor.capabilities.mastery.loop",
    "deeptutor.visualizers.loop_capability",
)
print(json.dumps([name for name in forbidden if name in sys.modules]))
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=repo_root,
        capture_output=True,
        check=True,
        text=True,
    )
    assert json.loads(result.stdout) == []
