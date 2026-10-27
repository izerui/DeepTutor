"""Publish/preview never run blocking teaching-store I/O on the event loop."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from deeptutor.teaching import service as service_module
from deeptutor.teaching.models import PublishRequest
from deeptutor.teaching.service import TeachingService
from deeptutor.teaching.store import TeachingStore

_STORE_CALLS = ("get_template", "get_revision", "add_revision", "count_active")


@pytest.fixture
def loop_threads(monkeypatch):
    """Record (name, on_loop_thread) for every synchronous store/registry call."""
    calls: list[tuple[str, bool]] = []
    loop_ident: dict[str, int] = {}

    def spy(label, original):
        def wrapper(*args, **kwargs):
            calls.append((label, threading.get_ident() == loop_ident.get("id")))
            return original(*args, **kwargs)

        return wrapper

    for name in _STORE_CALLS:
        monkeypatch.setattr(TeachingStore, name, spy(name, getattr(TeachingStore, name)))
    monkeypatch.setattr(service_module, "_accounts", spy("_accounts", service_module._accounts))
    monkeypatch.setattr(
        TeachingService,
        "_validate_students",
        spy("_validate_students", TeachingService._validate_students),
    )
    monkeypatch.setattr(
        TeachingService,
        "_unavailable_materials",
        spy("_unavailable_materials", TeachingService._unavailable_materials),
    )

    def run(coro_factory):
        async def main():
            loop_ident["id"] = threading.get_ident()
            return await coro_factory()

        return asyncio.run(main())

    return calls, run


@pytest.mark.usefixtures("quiet_change_model")
def test_preview_and_publish_keep_store_io_off_the_event_loop(
    as_account, teacher_draft, loop_threads, publish_template
):
    calls, run = loop_threads
    teacher_draft()
    # A first revision so the republish path reads the previous revision too.
    template_id = publish_template()["template"]["id"]
    calls.clear()
    with as_account("tina"):
        service = TeachingService()
        preview = run(lambda: service.preview_publish(template_id))
        result = run(
            lambda: service.publish(
                template_id, PublishRequest(draft_revision=preview["draft_revision"])
            )
        )
    assert result["revision"] == 2
    names = {name for name, _ in calls}
    assert {"get_template", "get_revision", "add_revision", "count_active"} <= names
    assert {"_accounts", "_validate_students", "_unavailable_materials"} <= names
    assert [name for name, on_loop in calls if on_loop] == []


@pytest.mark.usefixtures("quiet_change_model")
def test_event_loop_stays_responsive_while_add_revision_waits_for_material_lock(
    as_account, teacher_draft
):
    teacher_draft()
    with as_account("tina"):
        service = TeachingService()
        template = service.register("topic_calculus", "")
        preview = asyncio.run(service.preview_publish(template.id))

        acquired, release = threading.Event(), threading.Event()
        hold_seconds = 1.0

        def hold_material_lock():
            with TeachingStore().material_lock():
                acquired.set()
                release.wait(hold_seconds)

        holder = threading.Thread(target=hold_material_lock)
        holder.start()
        assert acquired.wait(5)

        async def main():
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
                return await service.publish(
                    template.id, PublishRequest(draft_revision=preview["draft_revision"])
                ), observed
            finally:
                done.set()
                await ticking

        try:
            result, gaps = asyncio.run(main())
        finally:
            release.set()
            holder.join()
    assert result["revision"] == 1
    # add_revision waited ~hold_seconds behind the lock; the loop kept ticking.
    assert sum(gaps) >= hold_seconds * 0.8
    assert max(gaps) < hold_seconds / 2
