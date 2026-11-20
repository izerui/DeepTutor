"""The shipped Remotion runtime assets and the player page."""

from __future__ import annotations

import json
from pathlib import Path

from deeptutor.agents.video_lesson.runtime import ASSETS_DIR, KIT_FILES, prepare_workdir, write_page


def test_shipped_runtime_assets_exist() -> None:
    for name in (
        *KIT_FILES,
        "build.mjs",
        "render-platform.ts",
        "package.json",
        "package-lock.json",
    ):
        assert (ASSETS_DIR / name).is_file(), name
    deps = json.loads((ASSETS_DIR / "package.json").read_text(encoding="utf-8"))["dependencies"]
    # Pinned exactly: a lesson that compiled once must compile the same way later.
    assert all(v[0].isdigit() for v in deps.values())


def test_prepare_workdir_copies_the_kit(tmp_path: Path) -> None:
    lesson = prepare_workdir(tmp_path / "source", "export const x = 1;")
    assert lesson.read_text(encoding="utf-8") == "export const x = 1;"
    for name in KIT_FILES:
        assert (tmp_path / "source" / name).is_file()


def test_write_page_inlines_the_bundle_safely(tmp_path: Path) -> None:
    bundle = tmp_path / "player.js"
    bundle.write_text('console.log("</script><b>x</b>");', encoding="utf-8")
    page = write_page(bundle, tmp_path / "out" / "lesson.html", title="a < b", language="zh")
    html = page.read_text(encoding="utf-8")
    assert html.count("</script>") == 1
    assert "<\/script><b>x</b>" in html
    assert "<title>a &lt; b</title>" in html
    assert 'lang="zh-CN"' in html


def test_prepare_workdir_writes_the_voice_module(tmp_path: Path) -> None:
    prepare_workdir(tmp_path / "source", "", voice="export const VOICE = {s1: {}};")
    assert "s1" in (tmp_path / "source" / "voice.ts").read_text(encoding="utf-8")


# --- develop: one shared runtime per deployment, guarded by a file lock ---------


import asyncio
import sys

import pytest

from deeptutor.agents.video_lesson import runtime
from deeptutor.services.sandbox.spec import ExecResult


@pytest.fixture
def system_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from deeptutor.multi_user import paths

    root = tmp_path / "data" / "system"
    monkeypatch.setattr(paths, "SYSTEM_ROOT", root)
    monkeypatch.delenv("DEEPTUTOR_REMOTION_RUNTIME", raising=False)
    monkeypatch.delenv("DEEPTUTOR_NPM_REGISTRY", raising=False)
    return root


def _fake_install(root: Path) -> None:
    manifest = json.loads((ASSETS_DIR / "package.json").read_text(encoding="utf-8"))
    for name, version in manifest["dependencies"].items():
        pkg = root / "node_modules" / name / "package.json"
        pkg.parent.mkdir(parents=True, exist_ok=True)
        pkg.write_text(json.dumps({"name": name, "version": version}), encoding="utf-8")


@pytest.fixture
def fake_node(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        runtime.shutil, "which", lambda name, *a, **k: f"/usr/bin/{name}" if name in {"node", "npm"} else None
    )
    monkeypatch.setattr(runtime, "_probe_node_version", lambda node: "v22.23.4")


def test_runtime_is_shared_across_users_under_system(system_root: Path) -> None:
    """Not ``data/users/<uid>/user/.runtime`` (upstream): one copy per deployment."""
    from deeptutor.multi_user.context import reset_current_user, set_current_user
    from deeptutor.multi_user.models import CurrentUser, UserScope

    seen = set()
    for uid in ("learner-a", "learner-b"):
        token = set_current_user(
            CurrentUser(
                id=uid,
                username=uid,
                role="user",
                scope=UserScope(kind="user", user_id=uid, root=system_root.parent / "users" / uid),
            )
        )
        try:
            seen.add(runtime.runtime_dir())
        finally:
            reset_current_user(token)
    assert seen == {system_root / "runtime" / "remotion"}
    assert "users" not in str(next(iter(seen)))


def test_runtime_dir_override_wins(system_root: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEEPTUTOR_REMOTION_RUNTIME", str(tmp_path / "preinstalled"))
    assert runtime.runtime_dir() == (tmp_path / "preinstalled").resolve()


def test_status_reports_missing_node_clearly(system_root: Path, monkeypatch) -> None:
    monkeypatch.setattr(runtime.shutil, "which", lambda name, *a, **k: None)
    status = runtime.runtime_status()
    assert not status.available and status.reason == "node_missing"
    assert not runtime.tools_present()
    with pytest.raises(runtime.VideoLessonRuntimeUnavailable) as caught:
        asyncio.run(runtime.ensure_runtime())
    assert caught.value.code == "runtime_unavailable"
    assert "contact your administrator" in str(caught.value)


def test_status_rejects_old_node(system_root: Path, monkeypatch) -> None:
    monkeypatch.setattr(runtime.shutil, "which", lambda name, *a, **k: f"/usr/bin/{name}")
    monkeypatch.setattr(runtime, "_probe_node_version", lambda node: "v18.19.0")
    status = runtime.runtime_status()
    assert not status.available and status.reason == "node_too_old"
    with pytest.raises(runtime.VideoLessonRuntimeUnavailable):
        asyncio.run(runtime.ensure_runtime())


def test_status_ok_and_installed(system_root: Path, fake_node) -> None:
    _fake_install(system_root / "runtime" / "remotion")
    status = runtime.runtime_status()
    assert status.available and status.installed and status.reason == "ok"
    assert status.to_dict()["runtime_dir"] == str(system_root / "runtime" / "remotion")


def test_concurrent_first_use_installs_once(system_root: Path, fake_node, monkeypatch) -> None:
    calls: list[list[str]] = []

    async def fake_run_tool(argv, *, timeout, cwd=None, env=None):
        calls.append(list(argv))
        await asyncio.sleep(0.05)
        _fake_install(Path(cwd))
        return ExecResult(stdout="", exit_code=0)

    monkeypatch.setattr(runtime, "_run_tool", fake_run_tool)

    async def main():
        return await asyncio.gather(*(runtime.ensure_runtime() for _ in range(5)))

    roots = asyncio.run(main())
    assert len(calls) == 1 and calls[0][1] == "ci"
    assert set(roots) == {system_root / "runtime" / "remotion"}
    # Installed already: later calls do not even take the lock.
    asyncio.run(runtime.ensure_runtime())
    assert len(calls) == 1


def test_npm_registry_mirror_is_passed_to_npm(system_root: Path, fake_node, monkeypatch) -> None:
    seen = {}

    async def fake_run_tool(argv, *, timeout, cwd=None, env=None):
        seen.update(env or {})
        _fake_install(Path(cwd))
        return ExecResult(exit_code=0)

    monkeypatch.setattr(runtime, "_run_tool", fake_run_tool)
    monkeypatch.setenv("DEEPTUTOR_NPM_REGISTRY", "https://registry.npmmirror.com")
    asyncio.run(runtime.ensure_runtime())
    assert seen["npm_config_registry"] == "https://registry.npmmirror.com"


def test_failed_install_raises_runtime_error(system_root: Path, fake_node, monkeypatch) -> None:
    async def fake_run_tool(argv, *, timeout, cwd=None, env=None):
        return ExecResult(stderr="ENOTFOUND registry", exit_code=1)

    monkeypatch.setattr(runtime, "_run_tool", fake_run_tool)
    with pytest.raises(runtime.VideoLessonRuntimeError, match="ENOTFOUND"):
        asyncio.run(runtime.ensure_runtime())


def test_file_lock_waits_for_another_process(system_root: Path, fake_node, monkeypatch) -> None:
    """A lock held through a separate open file (another worker) blocks the install."""
    root = system_root / "runtime" / "remotion"
    root.parent.mkdir(parents=True)
    lock_path = root.parent / f"remotion{runtime.LOCK_FILENAME}"
    monkeypatch.setattr(runtime, "LOCK_POLL_S", 0.01)
    calls: list[str] = []

    async def fake_run_tool(argv, *, timeout, cwd=None, env=None):
        calls.append("npm")
        _fake_install(Path(cwd))
        return ExecResult(exit_code=0)

    monkeypatch.setattr(runtime, "_run_tool", fake_run_tool)

    with open(lock_path, "a+b") as other:
        assert runtime._try_lock(other)

        async def held_then_released():
            task = asyncio.create_task(runtime.ensure_runtime())
            await asyncio.sleep(0.1)
            assert not task.done(), "install must wait for the other worker's lock"
            # The other worker finishes the install, then releases.
            _fake_install(root)
            runtime._unlock(other)
            return await asyncio.wait_for(task, 5)

        assert asyncio.run(held_then_released()) == root
    # The other worker installed it, so this process never ran npm.
    assert calls == []


def test_file_lock_times_out_with_a_clear_error(system_root: Path) -> None:
    root = system_root / "runtime" / "remotion"
    root.parent.mkdir(parents=True)
    with open(root.parent / f"remotion{runtime.LOCK_FILENAME}", "a+b") as other:
        assert runtime._try_lock(other)

        async def main():
            async with runtime.install_guard(root, wait_s=0.05):
                pass

        with pytest.raises(runtime.VideoLessonRuntimeError, match="still installing"):
            asyncio.run(main())
        runtime._unlock(other)


@pytest.mark.skipif(sys.platform == "win32", reason="relative import path check")
def test_build_script_imports_the_shared_sandbox_module() -> None:
    build = (ASSETS_DIR / "build.mjs").read_text(encoding="utf-8")
    assert '"../../../services/sandbox/javascript.mjs"' in build
    assert (ASSETS_DIR / "../../../services/sandbox/javascript.mjs").resolve().is_file()
