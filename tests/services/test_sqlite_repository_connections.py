"""Partner runtime status must close every SQLite connection it opens.

Cron and web-source sync are covered upstream by
``test_background_sqlite_connections.py``; partners has no upstream test, and
its connection setup is the one place that needed a local close-on-failure.

``sqlite3.Connection``'s own context manager commits or rolls back but never
closes, and the connection sits in a reference cycle, so a bare
``with sqlite3.connect(...)`` keeps its file descriptors until the cyclic GC
runs. The schedulers poll these stores every few seconds, which exhausted the
process FD limit in production. Each case also pins the transaction behaviour
the call sites rely on.
"""

from __future__ import annotations

from dataclasses import dataclass
import gc
import os
from pathlib import Path
from typing import Any, Callable

import pytest

from deeptutor.services.partners.runtime_status import PartnerRuntimeStatusRepository


@dataclass(frozen=True)
class Case:
    open: Callable[[Path], Any]
    write: Callable[[Any], None]
    read: Callable[[Any], Any]
    table: str
    # autocommit stores open their own transaction; partners relies on the
    # implicit one sqlite3 starts before DML in its default mode.
    begin: str | None


CASES = {
    "partners": Case(
        open=lambda path: PartnerRuntimeStatusRepository(path / "status.sqlite3"),
        write=lambda repo: repo.set("ada", owner_id="worker-a", running=True, state="running"),
        read=lambda repo: repo.get("ada"),
        table="partner_runtime_status",
        begin=None,
    ),
}


def _open_fds() -> int:
    return len(os.listdir("/dev/fd"))


@pytest.fixture(params=sorted(CASES))
def case(request) -> Case:
    return CASES[request.param]


@pytest.mark.skipif(not os.path.isdir("/dev/fd"), reason="needs /dev/fd")
def test_connections_close_without_the_cyclic_gc(tmp_path: Path, case: Case) -> None:
    repo = case.open(tmp_path)
    case.write(repo)
    gc.collect()
    gc.disable()
    try:
        before = _open_fds()
        for _ in range(50):
            case.read(repo)
            case.write(repo)
        grown = _open_fds() - before
    finally:
        gc.enable()
    # A leaked WAL connection holds two descriptors; 100 calls would add ~200.
    assert grown <= 2


def test_writes_are_visible_to_a_fresh_connection(tmp_path: Path, case: Case) -> None:
    case.write(case.open(tmp_path))

    assert case.read(case.open(tmp_path))


def test_exception_mid_transaction_rolls_back(tmp_path: Path, case: Case) -> None:
    repo = case.open(tmp_path)
    case.write(repo)

    with pytest.raises(RuntimeError, match="boom"):
        with repo._connect() as connection:
            if case.begin:
                connection.execute(case.begin)
            connection.execute(f"DELETE FROM {case.table}")
            raise RuntimeError("boom")

    assert case.read(case.open(tmp_path))


def test_failed_pragma_still_closes_the_connection(tmp_path: Path, case: Case, monkeypatch) -> None:
    import sqlite3

    repo = case.open(tmp_path)
    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    class FailingPragma(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql.lstrip().upper().startswith("PRAGMA"):
                raise sqlite3.OperationalError("disk I/O error")
            return super().execute(sql, *args)

    def connect(*args, **kwargs):
        connection = real_connect(*args, factory=FailingPragma, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        case.read(repo)

    assert opened
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
