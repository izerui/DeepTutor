"""B3 P7: teaching store writer safety (B3-14 / T12-M4) and cheap account deletion (T12-M3)."""

from __future__ import annotations

import threading

import pytest

from deeptutor.teaching import store as teaching_store
from deeptutor.teaching.store import TeachingStore


@pytest.fixture
def fresh_write_lock(monkeypatch):
    """Per-test writer locks, so a hung red run cannot poison later tests."""
    from deeptutor.book import registry as books
    from deeptutor.knowledge import registry as kbs

    monkeypatch.setattr(teaching_store, "_write_lock", threading.Lock())
    monkeypatch.setattr(kbs, "_write_lock", threading.RLock())
    monkeypatch.setattr(books, "_write_lock", threading.RLock())


def _in_thread(body) -> BaseException | None:
    """Run ``body`` in a daemon thread; fail if it is still running after 2 s."""
    outcome: dict[str, BaseException | None] = {"error": None}

    def run() -> None:
        try:
            body()
        except BaseException as exc:  # noqa: BLE001 - reported to the test
            outcome["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive(), "nested teaching writer hung instead of raising"
    return outcome["error"]


def _nested_write(path):
    def body() -> None:
        with teaching_store._write(path):
            with teaching_store._write(path):
                pass

    return body


def _write_then_joint(path):
    def body() -> None:
        with teaching_store._write(path):
            with teaching_store._joint_write(path):
                pass

    return body


def _joint_then_write(path):
    def body() -> None:
        with teaching_store._joint_write(path):
            with teaching_store._write(path):
                pass

    return body


@pytest.mark.parametrize("nesting", [_nested_write, _write_then_joint, _joint_then_write])
def test_a_nested_teaching_writer_raises_instead_of_hanging(nesting, fresh_write_lock):
    path = teaching_store.teaching_db_path()
    error = _in_thread(nesting(path))
    assert isinstance(error, RuntimeError)
    # The flag is cleared again: the same thread's next writer works.
    with teaching_store._write(path) as conn:
        conn.execute("SELECT 1")
    with teaching_store._joint_write(path) as conn:
        conn.execute("SELECT 1")


def test_writers_in_other_threads_still_wait_rather_than_raise(fresh_write_lock):
    path = teaching_store.teaching_db_path()
    entered = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []

    def holder() -> None:
        with teaching_store._write(path):
            entered.set()
            release.wait(5)

    def waiter() -> None:
        try:
            with teaching_store._write(path):
                pass
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    first = threading.Thread(target=holder, daemon=True)
    first.start()
    assert entered.wait(2)
    second = threading.Thread(target=waiter, daemon=True)
    second.start()
    second.join(timeout=0.3)
    assert second.is_alive()  # waiting, not refused
    release.set()
    first.join(2)
    second.join(2)
    assert not second.is_alive() and errors == []


@pytest.fixture
def joint_calls(monkeypatch):
    calls: list[object] = []
    real = teaching_store._joint_write

    def counting(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(teaching_store, "_joint_write", counting)
    return calls


def test_deleting_an_account_without_templates_skips_the_joint_writer(joint_calls):
    store = TeachingStore()
    # No teaching DB yet.
    assert store.transfer_owner("student-1", "admin-1") == 0
    store.create_template(
        owner_user_id="teacher-1", title="T", description="", draft_path_id="p",
        draft_workspace_id="w",
    )
    assert store.transfer_owner("student-1", "admin-1") == 0
    assert joint_calls == []


def test_deleting_a_teacher_still_transfers_in_the_joint_writer(joint_calls):
    store = TeachingStore()
    created = store.create_template(
        owner_user_id="teacher-1", title="T", description="", draft_path_id="p",
        draft_workspace_id="w",
    )
    assert store.transfer_owner("teacher-1", "admin-1") == 1
    assert len(joint_calls) == 1
    moved = store.get_template(created.id)
    assert moved.owner_user_id == "admin-1" and moved.draft_path_id == ""
