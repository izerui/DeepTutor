"""Run memory-heavy, importable functions in short-lived subprocesses.

Unlike ``multiprocessing.spawn``, the fixed ``python -m`` entry point works
from Uvicorn, the CLI, notebooks, ``python -c``, and frozen test runners
without requiring callers to guard their own main module. Arguments and
results travel through private temporary pickle files; callable paths are
fixed by DeepTutor code and are never accepted from an API request.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path
import pickle
import signal
import subprocess  # nosec B404 - fixed interpreter/module argv, no shell
import sys
import tempfile
import threading
from typing import Any

DEFAULT_ISOLATED_TIMEOUT_SECONDS = 120.0
_WORKER_MODULE = "deeptutor.runtime.worker_process"


def _worker_limit() -> int:
    try:
        return max(1, int(os.environ.get("DEEPTUTOR_ISOLATED_WORKERS", "2") or "2"))
    except ValueError:
        return 2


MAX_CONCURRENT_ISOLATED_WORKERS = _worker_limit()
_WORKER_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_ISOLATED_WORKERS)


class IsolatedWorkerError(RuntimeError):
    """A callable failed inside an isolated worker process."""

    def __init__(
        self,
        message: str,
        *,
        remote_module: str = "",
        remote_type: str = "",
        remote_traceback: str = "",
        remote_attrs: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.remote_module = remote_module
        self.remote_type = remote_type
        self.remote_traceback = remote_traceback
        self.remote_attrs = remote_attrs or {}


class IsolatedWorkerTimeout(TimeoutError):
    """An isolated call exceeded its wall-clock deadline."""


class IsolatedWorkerCrashed(IsolatedWorkerError):
    """The child exited without returning a protocol envelope."""


def _write_request(
    path: Path,
    callable_path: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> None:
    path.write_bytes(
        pickle.dumps(
            {"callable_path": callable_path, "args": args, "kwargs": kwargs},
            protocol=pickle.HIGHEST_PROTOCOL,
        )
    )


def _read_envelope(path: Path, callable_path: str, stderr: bytes = b"") -> Any:
    if not path.is_file():
        detail = stderr.decode("utf-8", errors="replace").strip()[-2000:]
        suffix = f": {detail}" if detail else ""
        raise IsolatedWorkerCrashed(
            f"Isolated call {callable_path!r} exited without a result{suffix}"
        )
    try:
        envelope = pickle.loads(path.read_bytes())  # noqa: S301 - private trusted child file
    except Exception as exc:
        raise IsolatedWorkerCrashed(
            f"Isolated call {callable_path!r} returned an invalid result"
        ) from exc
    return _unwrap(envelope, callable_path)


def _unwrap(envelope: object, callable_path: str) -> Any:
    if not isinstance(envelope, dict):
        raise IsolatedWorkerCrashed(f"Isolated call {callable_path!r} returned an invalid envelope")
    if envelope.get("ok") is True:
        return envelope.get("result")
    raise IsolatedWorkerError(
        str(envelope.get("message") or f"Isolated call {callable_path!r} failed"),
        remote_module=str(envelope.get("module") or ""),
        remote_type=str(envelope.get("type") or ""),
        remote_traceback=str(envelope.get("traceback") or ""),
        remote_attrs=(
            dict(envelope.get("attrs")) if isinstance(envelope.get("attrs"), dict) else {}
        ),
    )


def _command(request_path: Path, result_path: Path) -> list[str]:
    return [sys.executable, "-m", _WORKER_MODULE, str(request_path), str(result_path)]


# Each worker leads its own process group so the whole tree can be stopped:
# callables may start their own workers (e.g. PageIndex Flash's spawn pool for
# long PDFs), and those grandchildren inherit our stdout/stderr pipes.
def _isolation_kwargs() -> dict[str, Any]:
    if os.name == "posix":
        return {"start_new_session": True}
    return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}


def _signal_tree(pid: int, *, force: bool) -> None:
    if os.name == "posix":
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGKILL if force else signal.SIGTERM)
        return
    # Windows has no group signal; descendants are only discoverable while
    # the root is alive, so terminate the tree in one forced pass.
    try:
        import psutil

        root = psutil.Process(pid)
        for proc in [*root.children(recursive=True), root]:
            with contextlib.suppress(psutil.Error):
                proc.kill()
    except ImportError:
        subprocess.run(  # noqa: S603,S607 - fixed argv, no shell
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:  # noqa: BLE001 - the root already exited
        pass


def _stop_sync_process(process: subprocess.Popen) -> None:
    _signal_tree(process.pid, force=False)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=1.0)
    # Unconditional: descendants can outlive a root that obeyed SIGTERM.
    _signal_tree(process.pid, force=True)
    process.communicate()


def run_in_isolated_process_sync(
    callable_path: str,
    *args: Any,
    timeout: float = DEFAULT_ISOLATED_TIMEOUT_SECONDS,
    kwargs: dict[str, Any] | None = None,
) -> Any:
    """Run an importable synchronous callable and block until it exits."""

    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    with _WORKER_SLOTS:
        with tempfile.TemporaryDirectory(prefix="deeptutor-worker-") as temp_dir:
            request_path = Path(temp_dir) / "request.pickle"
            result_path = Path(temp_dir) / "result.pickle"
            _write_request(request_path, callable_path, tuple(args), dict(kwargs or {}))
            process = subprocess.Popen(  # noqa: S603 - fixed argv and shell=False
                _command(request_path, result_path),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                **_isolation_kwargs(),
            )
            try:
                _stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                _stop_sync_process(process)
                raise IsolatedWorkerTimeout(
                    f"Isolated call {callable_path!r} exceeded {timeout:g} seconds"
                ) from exc
            except BaseException:
                _stop_sync_process(process)
                raise
            if os.name == "posix":
                # Reap strays a callable left behind after returning.
                _signal_tree(process.pid, force=True)
            return _read_envelope(result_path, callable_path, stderr)


async def _stop_async_process(process: asyncio.subprocess.Process, drain: asyncio.Future) -> None:
    """Stop the whole tree while ``drain`` (its communicate()) keeps reading.

    Abandoning the readers lets a tree that logs on shutdown fill the pipes;
    before gh-119710 (Python < 3.13) wait() then never returns. Descendants
    may also outlive the root and hold the pipes, so signal the group even
    when the root has already exited.
    """
    _signal_tree(process.pid, force=False)
    await asyncio.wait({drain}, timeout=1.0)
    _signal_tree(process.pid, force=True)
    with contextlib.suppress(Exception):
        await drain


async def _reap_before_returning(
    process: asyncio.subprocess.Process, drain: asyncio.Future
) -> None:
    """Run the tree stop to completion even if this task is cancelled meanwhile.

    The caller frees its slot and may clean shared storage once this returns,
    so a cancellation (e.g. backend shutdown) that lands during the SIGTERM
    grace period is deferred until SIGKILL has run and the pipes are drained,
    then re-raised.
    """
    stop = asyncio.ensure_future(_stop_async_process(process, drain))
    cancelled = False
    while not stop.done():
        try:
            await asyncio.shield(stop)
        except asyncio.CancelledError:
            cancelled = True
    stop.result()
    if cancelled:
        raise asyncio.CancelledError


async def run_in_isolated_process(
    callable_path: str,
    *args: Any,
    timeout: float = DEFAULT_ISOLATED_TIMEOUT_SECONDS,
    kwargs: dict[str, Any] | None = None,
) -> Any:
    """Run a synchronous callable without blocking the caller's event loop.

    Cancelling the coroutine terminates and joins the child before cancellation
    is re-raised, so a disconnected request cannot leave an orphan parser.
    """

    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    acquired = False
    try:
        while not acquired:
            acquired = _WORKER_SLOTS.acquire(blocking=False)
            if not acquired:
                await asyncio.sleep(0.02)
        with tempfile.TemporaryDirectory(prefix="deeptutor-worker-") as temp_dir:
            request_path = Path(temp_dir) / "request.pickle"
            result_path = Path(temp_dir) / "result.pickle"
            _write_request(request_path, callable_path, tuple(args), dict(kwargs or {}))
            process = await asyncio.create_subprocess_exec(
                *_command(request_path, result_path),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **_isolation_kwargs(),
            )
            drain = asyncio.ensure_future(process.communicate())
            try:
                # shield: a timeout must not cancel the pipe readers.
                _stdout, stderr = await asyncio.wait_for(asyncio.shield(drain), timeout=timeout)
            except asyncio.TimeoutError as exc:
                await _reap_before_returning(process, drain)
                raise IsolatedWorkerTimeout(
                    f"Isolated call {callable_path!r} exceeded {timeout:g} seconds"
                ) from exc
            except asyncio.CancelledError:
                await _reap_before_returning(process, drain)
                raise
            if os.name == "posix":
                _signal_tree(process.pid, force=True)
            return _read_envelope(result_path, callable_path, stderr)
    finally:
        if acquired:
            _WORKER_SLOTS.release()


__all__ = [
    "DEFAULT_ISOLATED_TIMEOUT_SECONDS",
    "IsolatedWorkerCrashed",
    "IsolatedWorkerError",
    "IsolatedWorkerTimeout",
    "MAX_CONCURRENT_ISOLATED_WORKERS",
    "run_in_isolated_process",
    "run_in_isolated_process_sync",
]
