"""Cross-platform lifecycle tests for short-lived heavy workers."""

from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
import sys

import pytest

from deeptutor.runtime.isolated_worker import (
    IsolatedWorkerError,
    IsolatedWorkerTimeout,
    run_in_isolated_process,
    run_in_isolated_process_sync,
)
from deeptutor.utils.document_extractor import (
    EmptyDocumentError,
    extract_text_from_path_isolated,
)


def test_sync_worker_returns_a_pickled_result() -> None:
    assert run_in_isolated_process_sync("operator:add", 20, 22, timeout=5) == 42


@pytest.mark.asyncio
async def test_async_worker_preserves_remote_error_details() -> None:
    with pytest.raises(IsolatedWorkerError) as raised:
        await run_in_isolated_process("builtins:int", "not-an-int", timeout=5)
    assert raised.value.remote_module == "builtins"
    assert raised.value.remote_type == "ValueError"
    assert "not-an-int" in str(raised.value)


@pytest.mark.asyncio
async def test_worker_timeout_terminates_the_child() -> None:
    with pytest.raises(IsolatedWorkerTimeout):
        await run_in_isolated_process("time:sleep", 10, timeout=0.05)


@pytest.mark.asyncio
async def test_worker_cancellation_terminates_the_child() -> None:
    task = asyncio.create_task(run_in_isolated_process("time:sleep", 10, timeout=20))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_document_wrapper_restores_public_error_type(tmp_path: Path) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    with pytest.raises(EmptyDocumentError) as raised:
        await extract_text_from_path_isolated(empty, timeout=5)
    assert raised.value.filename == "empty.txt"


def test_child_allocation_does_not_raise_parent_rss_plateau() -> None:
    pytest.importorskip("psutil")
    probe = """
import gc
import psutil
from deeptutor.runtime.isolated_worker import run_in_isolated_process_sync

process = psutil.Process()
before = process.memory_info().rss
for _ in range(3):
    result = run_in_isolated_process_sync(
        "deeptutor.runtime.worker_tasks:test_allocate_bytes",
        64 * 1024 * 1024,
        timeout=10,
    )
    assert result == 64 * 1024 * 1024
gc.collect()
print(process.memory_info().rss - before)
"""
    completed = subprocess.run(  # noqa: S603 - fixed interpreter, no shell
        [sys.executable, "-c", probe],
        check=True,
        capture_output=True,
        text=True,
        timeout=45,
    )
    retained_bytes = int(completed.stdout.strip())
    # Measure in a dedicated process so allocations from earlier pytest tests
    # cannot be mistaken for memory retained by the isolated worker protocol.
    assert retained_bytes < 20 * 1024 * 1024


def test_text_only_parser_writes_result_from_worker(tmp_path: Path) -> None:
    from deeptutor.services.parsing.engines.text_only.engine import TextOnlyParser

    source = tmp_path / "source.txt"
    source.write_text("isolated parser output", encoding="utf-8")
    workdir = tmp_path / "work"
    workdir.mkdir()
    TextOnlyParser().parse(source, workdir, config={})
    assert (workdir / "source.md").read_text(encoding="utf-8") == "isolated parser output"


# ---------------------------------------------------------------------------
# Process-tree reclamation: callables may start their own workers (PageIndex
# Flash uses a spawn ProcessPoolExecutor for long PDFs). Those grandchildren
# inherit stdout/stderr, so stopping only the direct child leaves both a
# running process and an open pipe behind.
# ---------------------------------------------------------------------------

_TREE_PROBE = '''
import multiprocessing
import os
import time
from pathlib import Path


def spawn_and_hang(pid_file):
    worker = multiprocessing.get_context("spawn").Process(target=time.sleep, args=(3600,))
    worker.start()
    Path(pid_file).write_text(f"{os.getpid()} {worker.pid}")
    time.sleep(3600)
'''


@pytest.fixture
def tree_probe(tmp_path, monkeypatch):
    import os

    (tmp_path / "tree_probe.py").write_text(_TREE_PROBE)
    repo_root = Path(__file__).resolve().parents[2]
    paths = [str(tmp_path), str(repo_root), os.environ.get("PYTHONPATH", "")]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(paths))
    return tmp_path / "pids.txt"


def _wait_for_pids(pid_file: Path) -> list[int]:
    import time

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if pid_file.exists() and len(pid_file.read_text().split()) == 2:
            return [int(pid) for pid in pid_file.read_text().split()]
        time.sleep(0.05)
    raise AssertionError("probe did not report its process tree")


def _assert_reaped(pids: list[int]) -> None:
    import os
    import time

    deadline = time.monotonic() + 5
    alive = list(pids)
    while alive and time.monotonic() < deadline:
        for pid in list(alive):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                alive.remove(pid)
        time.sleep(0.05)
    assert not alive, f"processes still running: {alive}"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX liveness probe")
@pytest.mark.asyncio
async def test_async_timeout_reaps_grandchildren(tree_probe) -> None:
    import time

    started = time.monotonic()
    with pytest.raises(IsolatedWorkerTimeout):
        await asyncio.wait_for(
            run_in_isolated_process(
                "tree_probe:spawn_and_hang", str(tree_probe), timeout=3
            ),
            timeout=30,
        )
    assert time.monotonic() - started < 15
    _assert_reaped(_wait_for_pids(tree_probe))
    assert await run_in_isolated_process("operator:add", 1, 2, timeout=10) == 3


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX liveness probe")
@pytest.mark.asyncio
async def test_async_cancellation_reaps_grandchildren(tree_probe) -> None:
    task = asyncio.create_task(
        run_in_isolated_process("tree_probe:spawn_and_hang", str(tree_probe), timeout=60)
    )
    pids = await asyncio.to_thread(_wait_for_pids, tree_probe)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=30)
    _assert_reaped(pids)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX liveness probe")
def test_sync_timeout_reaps_grandchildren(tree_probe) -> None:
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        future = pool.submit(
            run_in_isolated_process_sync,
            "tree_probe:spawn_and_hang",
            str(tree_probe),
            timeout=3,
        )
        with pytest.raises(IsolatedWorkerTimeout):
            future.result(timeout=30)
    _assert_reaped(_wait_for_pids(tree_probe))


# A tree that logs heavily while shutting down must not stall cleanup: the
# pipe readers have to keep draining after the timeout. Before gh-119710
# (Python < 3.13, including the 3.11 Docker image) an undrained pipe kept
# ``process.wait()`` pending forever, so also run under those interpreters.
_NOISY_PROBE = '''
import os
import signal
import sys
import time
from pathlib import Path


def log_flood_on_term(pid_file):
    def flood(_signum, _frame):
        # Far beyond the pipe buffer plus asyncio's 2 x 64 KiB read limit.
        for _ in range(256):
            sys.stderr.write("shutdown log " + "x" * 4096 + "\\n")
        sys.stderr.flush()
        time.sleep(3600)

    signal.signal(signal.SIGTERM, flood)
    Path(pid_file).write_text(str(os.getpid()))
    time.sleep(3600)
'''

_NOISY_SCENARIO = '''
import asyncio, sys, time
from deeptutor.runtime.isolated_worker import IsolatedWorkerTimeout, run_in_isolated_process


async def main(pid_file):
    started = time.monotonic()
    try:
        await run_in_isolated_process("noisy_probe:log_flood_on_term", pid_file, timeout=2)
    except IsolatedWorkerTimeout:
        pass
    else:
        raise SystemExit("expected a timeout")
    elapsed = time.monotonic() - started
    assert await run_in_isolated_process("operator:add", 1, 2, timeout=10) == 3
    print(f"ok {elapsed:.1f}")


asyncio.run(main(sys.argv[1]))
'''


@pytest.fixture
def noisy_probe(tmp_path, monkeypatch):
    import os

    (tmp_path / "noisy_probe.py").write_text(_NOISY_PROBE)
    (tmp_path / "noisy_scenario.py").write_text(_NOISY_SCENARIO)
    repo_root = Path(__file__).resolve().parents[2]
    paths = [str(tmp_path), str(repo_root), os.environ.get("PYTHONPATH", "")]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(paths))
    return tmp_path


def _pre_gh119710_interpreters() -> list[str]:
    import shutil

    found = []
    for name in ("python3.11", "python3.12"):
        path = shutil.which(name)
        if path and path not in found:
            found.append(path)
    return found or ["missing"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal handler probe")
@pytest.mark.parametrize("interpreter", [sys.executable, *_pre_gh119710_interpreters()])
def test_timeout_cleanup_drains_shutdown_logs(noisy_probe, interpreter) -> None:
    if interpreter == "missing":
        pytest.skip("no Python < 3.13 interpreter on PATH")
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and script, no shell
        [interpreter, str(noisy_probe / "noisy_scenario.py"), str(noisy_probe / "pid")],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert completed.stdout.startswith("ok ")
    assert float(completed.stdout.split()[1]) < 15
    _assert_reaped([int((noisy_probe / "pid").read_text())])


# Cancellation that lands while timeout cleanup is still escalating must not
# abort it: the tree has to be dead before the call ends and the slot frees.
_STUBBORN_PROBE = '''
import multiprocessing
import os
import signal
import time
from pathlib import Path


def _ignore_term_and_sleep():
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(3600)


def stubborn_tree(run_dir):
    run_dir = Path(run_dir)

    def on_term(_signum, _frame):
        (run_dir / "terminating").write_text("1")  # cleanup has started
        time.sleep(3600)

    signal.signal(signal.SIGTERM, on_term)
    worker = multiprocessing.get_context("spawn").Process(target=_ignore_term_and_sleep)
    worker.start()
    (run_dir / "pids.txt").write_text(f"{os.getpid()} {worker.pid}")
    time.sleep(3600)
'''


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal handler probe")
@pytest.mark.asyncio
async def test_cancel_during_timeout_cleanup_still_reaps_tree(tmp_path, monkeypatch) -> None:
    import os

    (tmp_path / "stubborn_probe.py").write_text(_STUBBORN_PROBE)
    repo_root = Path(__file__).resolve().parents[2]
    paths = [str(tmp_path), str(repo_root), os.environ.get("PYTHONPATH", "")]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(paths))

    task = asyncio.create_task(
        run_in_isolated_process("stubborn_probe:stubborn_tree", str(tmp_path), timeout=1)
    )
    pids = await asyncio.to_thread(_wait_for_pids, tmp_path / "pids.txt")
    marker = tmp_path / "terminating"
    for _ in range(600):
        if marker.exists() or task.done():
            break
        await asyncio.sleep(0.05)
    assert marker.exists(), "timeout cleanup never reached the SIGTERM stage"
    assert not task.done()

    task.cancel()  # overlaps the SIGTERM grace period of the timeout path
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=30)

    # Both layers ignore SIGTERM: only a completed SIGKILL escalation stops them.
    _assert_reaped(pids)
    assert await run_in_isolated_process("operator:add", 1, 2, timeout=10) == 3
