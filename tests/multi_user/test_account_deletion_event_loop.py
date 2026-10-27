"""Account deletion never runs its blocking hand-overs on the event loop.

``_delete_admin_user`` deletes the account and then hands knowledge bases,
books and teacher paths to the deleting admin (each through blocking SQLite
writers with process locks and long busy timeouts). The HTTP handlers must run
that work in a worker thread under a copy of the request context, so the
current-user ContextVar (the deleting admin) still reaches the stages that
re-check the actor, while other requests keep being served.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import threading
import time
from typing import Any

import pytest

import deeptutor.api.routers.auth as auth_router
from deeptutor.book import registry as book_registry
from deeptutor.knowledge import registry as kb_registry
from deeptutor.multi_user.context import get_current_user
from deeptutor.services.auth import TokenPayload
from deeptutor.teaching.store import TeachingStore

HOLD_SECONDS = 1.0


def _as_admin(teaching_users) -> TokenPayload:
    admin = teaching_users["adam"]
    return TokenPayload(username="adam", role="admin", user_id=admin["id"])


@pytest.fixture
def stage_calls(monkeypatch):
    """Record (stage, on_loop_thread, current_user_id) for every deletion stage."""
    calls: list[tuple[str, bool, str]] = []
    loop_ident: dict[str, int] = {}

    def spy(label, original):
        def wrapper(*args, **kwargs):
            on_loop = threading.get_ident() == loop_ident.get("id")
            calls.append((label, on_loop, get_current_user().id))
            return original(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(auth_router, "delete_user", spy("delete_user", auth_router.delete_user))
    monkeypatch.setattr(
        kb_registry, "transfer_ownership", spy("kb", kb_registry.transfer_ownership)
    )
    monkeypatch.setattr(
        book_registry, "transfer_book_ownership", spy("book", book_registry.transfer_book_ownership)
    )
    from deeptutor.teaching import service as teaching_service

    monkeypatch.setattr(
        teaching_service,
        "handle_account_deleted",
        spy("teaching", teaching_service.handle_account_deleted),
    )
    return calls, loop_ident


def _run_with_held_kb_writer(
    loop_ident: dict[str, int], coro_factory: Callable[[], Awaitable[Any]]
) -> tuple[Any, list[float]]:
    """Run a route coroutine while another thread holds the KB registry writer.

    Returns (result, gaps between event-loop ticks).
    """
    acquired, release = threading.Event(), threading.Event()

    def hold_kb_writer():
        with kb_registry._write():
            acquired.set()
            release.wait(HOLD_SECONDS)

    holder = threading.Thread(target=hold_kb_writer)
    holder.start()
    assert acquired.wait(5)

    async def main():
        loop_ident["id"] = threading.get_ident()
        observed: list[float] = []
        done = asyncio.Event()

        async def ticker():
            last = time.monotonic()
            while not done.is_set():
                await asyncio.sleep(0.01)
                now = time.monotonic()
                observed.append(now - last)
                last = now

        ticking = asyncio.create_task(ticker())
        try:
            return await coro_factory(), observed
        finally:
            done.set()
            await ticking

    try:
        return asyncio.run(main())
    finally:
        release.set()
        holder.join()


def _assert_responsive(gaps: list[float]) -> None:
    # The KB hand-over waited ~HOLD_SECONDS behind the writer; the loop kept ticking.
    assert sum(gaps) >= HOLD_SECONDS * 0.8
    assert max(gaps) < HOLD_SECONDS / 2


def test_remove_user_keeps_the_event_loop_responsive_and_hands_over_to_the_admin(
    sara_instance, teaching_users, as_account, stage_calls
):
    calls, loop_ident = stage_calls
    admin_id = teaching_users["adam"]["id"]

    with as_account("adam"):
        result, gaps = _run_with_held_kb_writer(
            loop_ident,
            lambda: auth_router.remove_user("tina", current=_as_admin(teaching_users)),
        )

    assert result == {"ok": True}
    _assert_responsive(gaps)
    assert [name for name, _, _ in calls] == ["delete_user", "kb", "book", "teaching"]
    assert [name for name, on_loop, _ in calls if on_loop] == []
    # The worker thread sees the request's current user: the deleting admin.
    assert {user_id for _, _, user_id in calls} == {admin_id}
    assert TeachingStore().get_template(sara_instance.template_id).owner_user_id == admin_id


def test_batch_remove_users_keeps_the_event_loop_responsive_with_unchanged_results(
    sara_instance, teaching_users, as_account, stage_calls
):
    calls, loop_ident = stage_calls
    admin_id = teaching_users["adam"]["id"]
    body = auth_router.AdminBatchDeleteRequest(usernames=["tina", "adam", "missing", "sara"])

    with as_account("adam"):
        result, gaps = _run_with_held_kb_writer(
            loop_ident,
            lambda: auth_router.batch_remove_users(body, current=_as_admin(teaching_users)),
        )

    assert result == {
        "ok": False,
        "deleted_count": 2,
        "failed_count": 2,
        "results": [
            {"username": "tina", "ok": True, "error": None},
            {"username": "adam", "ok": False, "error": "You cannot delete your own account"},
            {"username": "missing", "ok": False, "error": "User not found"},
            {"username": "sara", "ok": True, "error": None},
        ],
    }
    _assert_responsive(gaps)
    assert [name for name, on_loop, _ in calls if on_loop] == []
    assert {user_id for _, _, user_id in calls} == {admin_id}
    store = TeachingStore()
    assert store.get_template(sara_instance.template_id).owner_user_id == admin_id
    assert store.get_assignment(
        sara_instance.template_id, teaching_users["sara"]["id"]
    ).status == "revoked"


def test_a_failing_stage_in_the_worker_thread_still_runs_later_stages(
    sara_instance, teaching_users, as_account, stage_calls, monkeypatch
):
    calls, loop_ident = stage_calls

    def broken(*_args, **_kwargs):
        calls.append(("kb-broken", False, get_current_user().id))
        raise RuntimeError("kb registry locked")

    monkeypatch.setattr(kb_registry, "transfer_ownership", broken)

    with as_account("adam"):
        result = asyncio.run(
            auth_router.remove_user("tina", current=_as_admin(teaching_users))
        )

    assert result == {"ok": True}
    assert [name for name, _, _ in calls] == ["delete_user", "kb-broken", "book", "teaching"]
    admin_id = teaching_users["adam"]["id"]
    assert TeachingStore().get_template(sara_instance.template_id).owner_user_id == admin_id
