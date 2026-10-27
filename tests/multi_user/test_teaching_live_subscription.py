"""A live mastery subscription ends when its teacher path closes (rule R3).

REST, turns, lists and new subscriptions already refuse a revoked or archived
copy. These tests cover a socket that was subscribed *before* the teacher
closed the path: it must stop forwarding the copy's events and say why, with
the same machine-readable code a refused turn carries.
"""

from __future__ import annotations

import threading
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from deeptutor.api.routers import mastery_path
from deeptutor.learning.event_hub import publish_topic_signal
from deeptutor.learning.storage import LearningStore
from deeptutor.teaching.service import TeachingService
from deeptutor.teaching.store import TeachingStore

#: Longest a test waits for the server before a fallback wake-up is sent; the
#: fallback is a screen-only signal that a broken server would forward, so a
#: regression fails fast instead of blocking ``receive_json`` forever.
FALLBACK_SECONDS = 5.0


@pytest.fixture
def ws_client(teaching_http):
    app = FastAPI()
    app.include_router(mastery_path.ws_router, prefix="/ws")
    return TestClient(app)


def _emit(as_account, name: str, path_id: str) -> None:
    """Commit a real change to *name*'s copy (it publishes a hub signal)."""
    with as_account(name):
        store = LearningStore()
        point = store.load(path_id).modules[0].knowledge_points[0].id

        def apply(tx):
            tx.progress.mastery_levels[point] = 0.5
            tx.emit("attempt.recorded", {"test": True})

        store.mutate(path_id, apply)


def _scope(as_account, name: str) -> str:
    with as_account(name):
        return LearningStore().event_scope


def _fallback(scope: str, path_id: str) -> threading.Timer:
    timer = threading.Timer(
        FALLBACK_SECONDS,
        publish_topic_signal,
        args=(path_id, 0, "session.bound"),
        kwargs={"scope": scope},
    )
    timer.daemon = True
    timer.start()
    return timer


def _subscribe(socket, path_id: str) -> dict:
    socket.send_json({"type": "subscribe", "path_id": path_id})
    return socket.receive_json()


def _assert_terminal(message: dict, path_id: str) -> None:
    from deeptutor.teaching.instances import INSTANCE_UNAVAILABLE

    # The code a refused mastery turn carries (``turn_scope``), so one client
    # check covers both.
    assert message == {
        "type": "error",
        "code": "teacher_path_unavailable",
        "path_id": path_id,
        "content": INSTANCE_UNAVAILABLE,
    }


def _assert_quiet_then_refused(socket, path_id: str) -> None:
    """Nothing else was queued after the terminal message; resubscribing is refused."""
    _assert_terminal(_subscribe(socket, path_id), path_id)


@pytest.mark.parametrize("close", ["revoke", "archive"])
def test_closing_the_path_ends_a_live_subscription(
    close, sara_instance, ws_client, as_account, teaching_users
):
    path_id = sara_instance.path_id
    with ws_client.websocket_connect("/ws/mastery-paths?token=sara") as socket:
        assert _subscribe(socket, path_id)["type"] == "subscribed"

        with as_account("tina"):
            if close == "revoke":
                TeachingService().revoke(sara_instance.template_id, teaching_users["sara"]["id"])
            else:
                TeachingService().archive(sara_instance.template_id)
        _emit(as_account, "sara", path_id)

        _assert_terminal(socket.receive_json(), path_id)
        _emit(as_account, "sara", path_id)
        _assert_quiet_then_refused(socket, path_id)


@pytest.mark.parametrize("close", ["revoke", "archive"])
def test_closing_the_path_wakes_an_idle_subscription(
    close, sara_instance, ws_client, as_account, teaching_users
):
    """No learning event is needed: the close itself reaches the socket."""
    path_id = sara_instance.path_id
    scope = _scope(as_account, "sara")
    with ws_client.websocket_connect("/ws/mastery-paths?token=sara") as socket:
        assert _subscribe(socket, path_id)["type"] == "subscribed"
        with as_account("tina"):
            if close == "revoke":
                TeachingService().revoke(sara_instance.template_id, teaching_users["sara"]["id"])
            else:
                TeachingService().archive(sara_instance.template_id)
        timer = _fallback(scope, path_id)
        try:
            _assert_terminal(socket.receive_json(), path_id)
        finally:
            timer.cancel()


def test_a_close_in_another_process_is_noticed_without_a_signal(
    sara_instance, ws_client, as_account, teaching_users, monkeypatch
):
    """The wake-up hub is per process; a periodic re-check covers other workers."""
    monkeypatch.setattr(mastery_path, "_ACCESS_RECHECK_SECONDS", 0.05)
    path_id = sara_instance.path_id
    scope = _scope(as_account, "sara")
    with ws_client.websocket_connect("/ws/mastery-paths?token=sara") as socket:
        assert _subscribe(socket, path_id)["type"] == "subscribed"
        # Straight to the store: no in-process signal, as if another worker revoked.
        TeachingStore().revoke(sara_instance.template_id, teaching_users["sara"]["id"])
        started = time.monotonic()
        timer = _fallback(scope, path_id)
        try:
            _assert_terminal(socket.receive_json(), path_id)
        finally:
            timer.cancel()
        assert time.monotonic() - started < FALLBACK_SECONDS - 1


def test_revoking_one_student_leaves_another_students_subscription_live(
    teacher_draft, publish_template, ws_client, as_account, teaching_users
):
    import asyncio

    teacher_draft()
    template_id = publish_template(students=("sara", "sam"))["template"]["id"]
    for name in ("sara", "sam"):
        with as_account(name):
            asyncio.run(mastery_path.list_topics())
    path_id = f"assigned_{template_id}"

    with ws_client.websocket_connect("/ws/mastery-paths?token=sam") as socket:
        subscribed = _subscribe(socket, path_id)
        assert subscribed["type"] == "subscribed"
        with as_account("tina"):
            TeachingService().revoke(template_id, teaching_users["sara"]["id"])
        _emit(as_account, "sam", path_id)
        message = socket.receive_json()
        assert message["type"] == "topic_event"
        assert message["path_id"] == path_id
        assert message["revision"] > subscribed["revision"]


def test_a_failed_access_read_keeps_the_subscription_alive(
    sara_instance, ws_client, as_account, monkeypatch, caplog
):
    """A transient read error is not a revoke: log once, retry on the next wake."""
    import logging

    real = mastery_path._authorized_tail
    failures = {"left": 2}

    def flaky(*args, **kwargs):
        if failures["left"]:
            failures["left"] -= 1
            raise OSError("teaching database is busy")
        return real(*args, **kwargs)

    monkeypatch.setattr(mastery_path, "_authorized_tail", flaky)
    path_id = sara_instance.path_id
    with (
        caplog.at_level(logging.WARNING, logger=mastery_path.__name__),
        ws_client.websocket_connect("/ws/mastery-paths?token=sara") as socket,
    ):
        subscribed = _subscribe(socket, path_id)
        assert subscribed["type"] == "subscribed"
        for _ in range(3):
            _emit(as_account, "sara", path_id)
            time.sleep(0.2)  # one wake-up per commit (the hub keeps only the newest)
        # A dead forwarder would leave receive_json blocked forever: a late
        # resubscribe makes the server answer something, so it fails instead.
        timer = threading.Timer(
            FALLBACK_SECONDS,
            socket.send_json,
            args=({"type": "subscribe", "path_id": path_id},),
        )
        timer.daemon = True
        timer.start()
        try:
            message = socket.receive_json()
        finally:
            timer.cancel()
        assert message["type"] == "topic_event", message
        # Nothing committed while the reads failed is lost.
        assert message["revision"] == subscribed["revision"] + 3
        assert len(message["events"]) == 3
    warnings = [r for r in caplog.records if "live subscription" in r.getMessage()]
    assert len(warnings) == 1


def test_the_periodic_recheck_forwards_what_it_reads(
    sara_instance, ws_client, as_account, monkeypatch
):
    """An authorized student never loses events read at a re-check timeout."""
    from deeptutor.learning import event_hub

    monkeypatch.setattr(mastery_path, "_ACCESS_RECHECK_SECONDS", 0.05)
    path_id = sara_instance.path_id
    scope = _scope(as_account, "sara")
    with ws_client.websocket_connect("/ws/mastery-paths?token=sara") as socket:
        subscribed = _subscribe(socket, path_id)
        assert subscribed["type"] == "subscribed"
        # Commit without a wake-up, as another worker process would.
        monkeypatch.setattr(event_hub, "publish_topic_signal", lambda *a, **k: None)
        _emit(as_account, "sara", path_id)
        started = time.monotonic()
        timer = threading.Timer(
            FALLBACK_SECONDS,
            event_hub.mastery_topic_event_hub.publish,
            args=(path_id, 0, "session.bound"),
            kwargs={"scope": scope},
        )
        timer.daemon = True
        timer.start()
        try:
            message = socket.receive_json()
        finally:
            timer.cancel()
        assert message["type"] == "topic_event"
        assert message["revision"] == subscribed["revision"] + 1
        assert [event["event_type"] for event in message["events"]] == ["attempt.recorded"]
        assert message["reason"] != "session.bound"
        assert time.monotonic() - started < FALLBACK_SECONDS - 1


def test_the_per_event_recheck_alone_blocks_forwarding(
    sara_instance, ws_client, as_account, teaching_users
):
    """No close wake-up and no periodic re-check: the next event is still refused."""
    path_id = sara_instance.path_id
    with ws_client.websocket_connect("/ws/mastery-paths?token=sara") as socket:
        assert _subscribe(socket, path_id)["type"] == "subscribed"
        TeachingStore().revoke(sara_instance.template_id, teaching_users["sara"]["id"])
        _emit(as_account, "sara", path_id)
        _assert_terminal(socket.receive_json(), path_id)
