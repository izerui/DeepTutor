from __future__ import annotations

from pathlib import Path

from deeptutor.agents.math_animator.renderer import ManimRenderService


def test_manim_uses_standard_turn_execution_environment(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HOME", "/root")
    workspace_root = tmp_path / "workspace"
    output_dir = workspace_root / "outputs" / "math"
    service = ManimRenderService(
        "turn-1",
        output_dir=output_dir,
        workspace_root=workspace_root,
    )

    env = service._build_process_env()
    execution_root = output_dir / ".deeptutor" / "execution"

    assert env["HOME"] == str(execution_root / "home")
    assert env["TMPDIR"] == str(execution_root / "tmp")
    assert env["MPLCONFIGDIR"] == str(execution_root / "cache" / "matplotlib")
    assert env["DEEPTUTOR_WORKSPACE_ROOT"] == str(workspace_root.resolve())
