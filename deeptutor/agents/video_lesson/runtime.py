"""Remotion runtime for video lessons: install once, compile per lesson, wrap in a page.

The model writes ``Lesson.tsx`` against the shipped kit (``remotion/kit.tsx``).
``build.mjs`` compiles it with esbuild, renders sample frames in a
WebAssembly JavaScript sandbox, and bundles it with ``@remotion/player``.
The page this module writes is self-contained: the learner's browser plays the
composition live, so no video file is ever encoded on the server.

Deployment notes specific to this fork:

* The npm packages are installed **once per deployment** under
  ``data/system/runtime/remotion`` (not under each user's ``.runtime``), so
  every account shares one copy. ``DEEPTUTOR_REMOTION_RUNTIME`` still
  overrides the location for operators who pre-install it elsewhere.
* The first install is serialized with an in-process lock *and* a file lock,
  so several workers (or several learners hitting the feature at once) never
  run ``npm ci`` into the same directory concurrently.
* A missing or too-old Node.js is reported as a clear "runtime unavailable"
  state instead of a stack trace; other features are unaffected.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
import functools
from html import escape
import json
import os
from pathlib import Path
import re
import shutil
import subprocess  # nosec B404 - fixed argv version probe of a resolved executable
import sys
import time
from typing import Any, AsyncIterator

from deeptutor.services.sandbox.backends import RestrictedSubprocessBackend
from deeptutor.services.sandbox.spec import ExecRequest, ExecResult, ResourceLimits

ASSETS_DIR = Path(__file__).resolve().parent / "remotion"
KIT_FILES = ("kit.tsx", "china_outline.json", "voice.ts")
BUILD_TIMEOUT_S = 600
INSTALL_TIMEOUT_S = 900
RENDER_TIMEOUT_S = 180
MIN_NODE_MAJOR = 22
LOCK_FILENAME = ".install.lock"
LOCK_POLL_S = 0.5

UNAVAILABLE_MESSAGE = (
    "The server does not have the video lesson runtime installed "
    "(Node.js {major}+ and npm). Please contact your administrator."
)

_install_lock = asyncio.Lock()


class VideoLessonRuntimeError(RuntimeError):
    """The Node toolchain for video lessons is missing or could not be installed."""

    #: Stable machine-readable reason; the frontend maps it to a localized message.
    code = "runtime_error"


class VideoLessonRuntimeUnavailable(VideoLessonRuntimeError):
    """Node.js/npm are not installed on the server (an operator problem, not a bug)."""

    code = "runtime_unavailable"


@dataclass
class BuildResult:
    ok: bool
    stage: str
    errors: list[str] = field(default_factory=list)
    frames: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class RuntimeStatus:
    """What an administrator needs to know about the video lesson toolchain."""

    available: bool
    reason: str
    node: str | None
    node_version: str | None
    npm: str | None
    installed: bool
    runtime_dir: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def shared_runtime_root() -> Path:
    """The deployment-wide default: ``data/system/runtime/remotion``.

    ``SYSTEM_ROOT`` is read per call so a redirected root (tests, ``--home``)
    is honoured.
    """
    from deeptutor.multi_user import paths

    return Path(paths.SYSTEM_ROOT) / "runtime" / "remotion"


def runtime_dir() -> Path:
    """Where the npm packages live; ``DEEPTUTOR_REMOTION_RUNTIME`` overrides the default."""
    override = os.environ.get("DEEPTUTOR_REMOTION_RUNTIME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return shared_runtime_root()


@functools.lru_cache(maxsize=8)
def _probe_node_version(node: str) -> str | None:
    try:
        completed = subprocess.run(  # nosec B603 - fixed argv
            [node, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (completed.stdout or completed.stderr or "").strip()
    return output or None


def _node_major(version: str | None) -> int | None:
    match = re.match(r"v?(\d+)", version or "")
    return int(match.group(1)) if match else None


def tools_present() -> bool:
    """Cheap check (no subprocess) used for capability availability."""
    return bool(shutil.which("node") and shutil.which("npm"))


def runtime_status() -> RuntimeStatus:
    """Probe Node/npm and the shared install without changing anything."""
    root = runtime_dir()
    node = shutil.which("node")
    npm = shutil.which("npm")
    version = _probe_node_version(node) if node else None
    major = _node_major(version)
    installed = _installed(root)
    if not node:
        reason = "node_missing"
    elif major is None or major < MIN_NODE_MAJOR:
        reason = "node_too_old"
    elif not npm and not installed:
        reason = "npm_missing"
    else:
        reason = "ok"
    return RuntimeStatus(
        available=reason == "ok",
        reason=reason,
        node=node,
        node_version=version,
        npm=npm,
        installed=installed,
        runtime_dir=str(root),
    )


def _node() -> str:
    node = shutil.which("node")
    if not node:
        raise VideoLessonRuntimeUnavailable(UNAVAILABLE_MESSAGE.format(major=MIN_NODE_MAJOR))
    major = _node_major(_probe_node_version(node))
    if major is not None and major < MIN_NODE_MAJOR:
        raise VideoLessonRuntimeUnavailable(
            UNAVAILABLE_MESSAGE.format(major=MIN_NODE_MAJOR)
            + f" (found Node.js {major}, need {MIN_NODE_MAJOR}+)"
        )
    return node


def _installed(root: Path) -> bool:
    manifest = json.loads((ASSETS_DIR / "package.json").read_text(encoding="utf-8"))
    for name, version in manifest["dependencies"].items():
        pkg = root / "node_modules" / name / "package.json"
        if not pkg.is_file():
            return False
        try:
            if json.loads(pkg.read_text(encoding="utf-8")).get("version") != version:
                return False
        except (OSError, ValueError):
            return False
    return True


async def _run_tool(
    argv: list[str], *, timeout: int, cwd: Path | None = None, env: dict[str, str] | None = None
) -> ExecResult:
    """Run trusted tooling with bounded output and cancellation of its process tree.

    Model code runs inside WebAssembly, never in the host JavaScript context.
    This subprocess owns the trusted compiler and the Wasm interpreter.
    """
    return await RestrictedSubprocessBackend(inherit_virtualenv=False).exec(
        ExecRequest.of_argv(
            argv,
            workdir=str(cwd) if cwd else "",
            env=env or {},
            limits=ResourceLimits(timeout_s=timeout, max_output_chars=64_000),
        )
    )


def _require_success(result: ExecResult, action: str) -> None:
    if not result.ok or result.exit_code != 0:
        detail = result.error or result.stderr or result.stdout
        if result.timed_out:
            detail = "timed out"
        raise VideoLessonRuntimeError(f"{action} failed: {detail[-1500:]}")


def _try_lock(handle: Any) -> bool:
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle: Any) -> None:
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@asynccontextmanager
async def install_guard(root: Path, *, wait_s: float | None = None) -> AsyncIterator[None]:
    """Serialize first installs across tasks (asyncio lock) and processes (file lock).

    The lock file sits next to the runtime directory so ``npm ci`` (which
    empties ``node_modules``) never touches it. Waiting polls without blocking
    the event loop; a holder that outlives ``wait_s`` is reported, not raced.
    """
    deadline = time.monotonic() + (INSTALL_TIMEOUT_S + 60 if wait_s is None else wait_s)
    async with _install_lock:
        root.parent.mkdir(parents=True, exist_ok=True)
        lock_path = root.parent / f"{root.name}{LOCK_FILENAME}"
        with open(lock_path, "a+b") as handle:
            while not _try_lock(handle):
                if time.monotonic() >= deadline:
                    raise VideoLessonRuntimeError(
                        "Another server process is still installing the video lesson runtime; "
                        "try again in a few minutes."
                    )
                await asyncio.sleep(LOCK_POLL_S)
            try:
                yield
            finally:
                _unlock(handle)


def npm_registry() -> str:
    """Optional npm mirror (e.g. for deployments behind the Great Firewall)."""
    return os.environ.get("DEEPTUTOR_NPM_REGISTRY", "").strip()


async def ensure_runtime(*, registry: str | None = None) -> Path:
    """Prepare pinned tooling and the isolated renderer; never fall back to host execution."""
    _node()
    root = runtime_dir()
    if _installed(root):
        return root
    async with install_guard(root):
        # Another worker may have finished while we waited for the lock.
        if not _installed(root):
            npm = shutil.which("npm")
            if not npm:
                raise VideoLessonRuntimeUnavailable(
                    UNAVAILABLE_MESSAGE.format(major=MIN_NODE_MAJOR)
                )
            root.mkdir(parents=True, exist_ok=True)
            for name in ("package.json", "package-lock.json"):
                shutil.copy(ASSETS_DIR / name, root / name)
            env: dict[str, str] = {}
            mirror = (registry if registry is not None else npm_registry()).strip()
            if mirror:
                env["npm_config_registry"] = mirror
            result = await _run_tool(
                [npm, "ci", "--no-audit", "--no-fund", "--omit=dev"],
                timeout=INSTALL_TIMEOUT_S,
                cwd=root,
                env=env,
            )
            _require_success(result, "Installing Remotion")
            if not _installed(root):
                raise VideoLessonRuntimeError(
                    "Installed Remotion packages do not match the manifest."
                )
    return root


def prepare_workdir(work_dir: Path, code: str, voice: str | None = None) -> Path:
    """Write the lesson next to a fresh copy of the kit and return the lesson path.

    ``voice`` is the generated ``voice.ts`` module; without it the kit's empty default is used.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    for name in KIT_FILES:
        shutil.copy(ASSETS_DIR / name, work_dir / name)
    if voice is not None:
        (work_dir / "voice.ts").write_text(voice, encoding="utf-8")
    lesson = work_dir / "Lesson.tsx"
    lesson.write_text(code, encoding="utf-8")
    return lesson


async def build_lesson(lesson: Path, bundle: Path) -> BuildResult:
    """Compile the lesson and validate it through the deployment-independent JS sandbox."""
    root = await ensure_runtime()
    result = await _run_tool(
        [_node(), str(ASSETS_DIR / "build.mjs"), str(lesson), str(bundle)],
        timeout=BUILD_TIMEOUT_S,
        env={
            "DEEPTUTOR_REMOTION_RUNTIME": str(root),
            "DEEPTUTOR_RENDER_TIMEOUT_MS": str(RENDER_TIMEOUT_S * 1000),
        },
    )
    if result.timed_out:
        return BuildResult(False, "timeout", [f"build did not finish within {BUILD_TIMEOUT_S}s"])
    lines = result.stdout.strip().splitlines()
    try:
        data = json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError):
        return BuildResult(False, "build", [(result.error or result.stderr or "no output")[-1500:]])
    return BuildResult(
        ok=bool(data.get("ok")) and result.ok and result.exit_code == 0,
        stage=str(data.get("stage", "")),
        errors=[str(e) for e in data.get("errors") or []],
        frames=list(data.get("frames") or []),
    )


PAGE = """<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&family=Lora:ital,wght@0,400;0,500;0,600;1,500&display=swap" rel="stylesheet">
<style>
* {{ box-sizing: border-box }}
html, body {{ margin: 0; background: #fdfcf9 }}
#player {{ width: 100%; max-width: 1280px; margin: 0 auto }}
</style>
</head>
<body>
<div id="player"></div>
<script>{bundle}</script>
</body>
</html>
"""


def write_page(bundle: Path, page: Path, *, title: str, language: str) -> Path:
    script = bundle.read_text(encoding="utf-8").replace("</script", "<\\/script")
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        PAGE.format(
            lang="zh-CN" if language.startswith("zh") else "en", title=escape(title), bundle=script
        ),
        encoding="utf-8",
    )
    return page


__all__ = [
    "BuildResult",
    "RuntimeStatus",
    "UNAVAILABLE_MESSAGE",
    "VideoLessonRuntimeError",
    "VideoLessonRuntimeUnavailable",
    "build_lesson",
    "ensure_runtime",
    "install_guard",
    "npm_registry",
    "prepare_workdir",
    "runtime_dir",
    "runtime_status",
    "shared_runtime_root",
    "tools_present",
    "write_page",
]
