"""A failed account hand-over leaves a durable, retryable record (B2-9).

Account deletion deletes the account first and then hands its knowledge bases,
books and teacher paths to the deleting admin. Every stage is best effort, so
a failing one must leave a "pending account handoff" record that names the
stages still to run. A retry (at startup, or by an admin through the API)
re-runs only those stages; a successful retry clears the record, and every
stage is safe to run again.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

import deeptutor.api.routers.auth as auth_router
from deeptutor.book import registry as book_registry
from deeptutor.knowledge import registry as kb_registry
from deeptutor.multi_user import account_handoff
from deeptutor.multi_user.models import LOCAL_ADMIN_ID
from deeptutor.services.auth import TokenPayload
from deeptutor.teaching import service as teaching_service
from deeptutor.teaching.store import TeachingStore

ALL_STAGES = (
    "knowledge_bases",
    "books",
    "teacher_paths",
    "student_assignments",
    "path_materials",
)


def _as_admin(teaching_users) -> TokenPayload:
    admin = teaching_users["adam"]
    return TokenPayload(username="adam", role="admin", user_id=admin["id"])


def _broken(*_args, **_kwargs):
    raise RuntimeError("injected stage failure")


@pytest.fixture
def world(sara_instance, teaching_users, tmp_path):
    """Tina owns a public KB, a book and Sara's teacher path."""
    tina = teaching_users["tina"]["id"]
    kb = kb_registry.register_kb(
        base_dir=tmp_path / "kbs", name="tina-public", kind="uploaded",
        created_by=tina, visibility="public",
    )
    book = book_registry.register_book(
        base_dir=tmp_path / "books", dir_name="book_bk_handoff", title="Handoff",
        created_by=tina,
    )
    return SimpleNamespace(
        kb=kb,
        book=book,
        template_id=sara_instance.template_id,
        tina=tina,
        sara=teaching_users["sara"]["id"],
        adam=teaching_users["adam"]["id"],
    )


# victim, failing stage, (target, attribute) to break, stages left pending
FAULTS = [
    ("tina", "knowledge_bases", (kb_registry, "transfer_ownership"), ["knowledge_bases"]),
    ("tina", "books", (book_registry, "transfer_book_ownership"), ["books"]),
    ("tina", "teacher_paths", (TeachingStore, "transfer_owner"), ["teacher_paths"]),
    (
        "sara",
        "student_assignments",
        (TeachingStore, "revoke_all_for_student"),
        ["student_assignments", "path_materials"],
    ),
    ("sara", "path_materials", (teaching_service, "sync_material_references"), ["path_materials"]),
]


def _owned_by(world, owner: str) -> dict[str, bool]:
    return {
        "knowledge_bases": kb_registry.get_kb(world.kb.kb_id).created_by == owner,
        "books": book_registry.get_book(world.book.book_id).created_by == owner,
        "teacher_paths": TeachingStore().get_template(world.template_id).owner_user_id == owner,
    }


@pytest.mark.parametrize(("victim", "stage", "fault", "pending"), FAULTS, ids=[f[1] for f in FAULTS])
def test_a_failed_stage_is_recorded_and_a_retry_finishes_only_it(
    world, teaching_users, monkeypatch, victim, stage, fault, pending
):
    victim_id = teaching_users[victim]["id"]
    with monkeypatch.context() as broken:
        broken.setattr(*fault, _broken)
        assert auth_router._delete_admin_user(victim, _as_admin(teaching_users)) == (200, "")

    records = account_handoff.list_pending_handoffs()
    assert [r.user_id for r in records] == [victim_id]
    record = records[0]
    assert record.stages == pending
    assert record.successor_id == world.adam
    assert record.username == victim
    assert record.attempts == 1
    assert "injected stage failure" in record.last_error
    assert record.created_at and record.updated_at

    # Only the failed stages run on retry.
    calls: list[str] = []
    for label, target, name in (
        ("knowledge_bases", kb_registry, "transfer_ownership"),
        ("books", book_registry, "transfer_book_ownership"),
    ):
        original = getattr(target, name)

        def spy(*args, _label=label, _original=original, **kwargs):
            calls.append(_label)
            return _original(*args, **kwargs)

        monkeypatch.setattr(target, name, spy)
    original_teaching = teaching_service.handle_account_deleted

    def teaching_spy(*args, **kwargs):
        calls.append(tuple(kwargs.get("stages", ())))
        return original_teaching(*args, **kwargs)

    monkeypatch.setattr(teaching_service, "handle_account_deleted", teaching_spy)

    assert account_handoff.retry_handoff(victim_id) is None
    assert account_handoff.list_pending_handoffs() == []
    teaching_pending = tuple(s for s in pending if s not in ("knowledge_bases", "books"))
    expected_calls = [s for s in pending if s in ("knowledge_bases", "books")]
    if teaching_pending:
        expected_calls.append(teaching_pending)
    assert calls == expected_calls

    if victim == "tina":
        assert _owned_by(world, world.adam) == {
            "knowledge_bases": True, "books": True, "teacher_paths": True,
        }
    else:
        assert TeachingStore().get_assignment(world.template_id, world.sara).status == "revoked"


def test_the_record_exists_while_the_stages_run(world, teaching_users, monkeypatch):
    """Write-ahead: a crash between stages still leaves something to retry."""
    seen: list[list[str]] = []
    original = kb_registry.transfer_ownership

    def spy(*args, **kwargs):
        seen.extend(r.stages for r in account_handoff.list_pending_handoffs())
        return original(*args, **kwargs)

    monkeypatch.setattr(kb_registry, "transfer_ownership", spy)

    assert auth_router._delete_admin_user("tina", _as_admin(teaching_users)) == (200, "")

    assert seen == [list(ALL_STAGES)]
    # Everything succeeded, so nothing is left behind.
    assert account_handoff.list_pending_handoffs() == []


def test_a_retry_that_fails_again_keeps_the_record_and_counts_attempts(
    world, teaching_users, monkeypatch
):
    monkeypatch.setattr(book_registry, "transfer_book_ownership", _broken)
    auth_router._delete_admin_user("tina", _as_admin(teaching_users))

    still = account_handoff.retry_handoff(world.tina)

    assert still is not None
    assert still.stages == ["books"]
    assert still.attempts == 2
    assert [r.attempts for r in account_handoff.list_pending_handoffs()] == [2]


def test_every_stage_is_safe_to_run_again(world, teaching_users, monkeypatch):
    revoked: list[str] = []
    monkeypatch.setattr(
        teaching_service, "notify_instance_closed", lambda template_id: revoked.append(template_id)
    )
    auth_router._delete_admin_user("tina", _as_admin(teaching_users))
    auth_router._delete_admin_user("sara", _as_admin(teaching_users))
    assert revoked == [world.template_id]
    template_before = TeachingStore().get_template(world.template_id)
    kb_before = kb_registry.get_kb(world.kb.kb_id)
    book_before = book_registry.get_book(world.book.book_id)
    assignment_before = TeachingStore().get_assignment(world.template_id, world.sara)

    for victim in (world.tina, world.sara):
        assert account_handoff.run_stages(victim, world.adam, ALL_STAGES) == ([], "")

    assert TeachingStore().get_template(world.template_id) == template_before
    assert kb_registry.get_kb(world.kb.kb_id) == kb_before
    assert book_registry.get_book(world.book.book_id) == book_before
    assert TeachingStore().get_assignment(world.template_id, world.sara) == assignment_before
    assert revoked == [world.template_id]  # nothing new was closed
    assert account_handoff.list_pending_handoffs() == []
    # A cleared record has nothing to retry.
    with pytest.raises(LookupError):
        account_handoff.retry_handoff(world.tina)


def test_a_retried_revocation_wakes_live_subscriptions(world, teaching_users, monkeypatch):
    woken: list[str] = []
    monkeypatch.setattr(
        teaching_service, "notify_instance_closed", lambda template_id: woken.append(template_id)
    )
    with monkeypatch.context() as broken:
        broken.setattr(TeachingStore, "revoke_all_for_student", _broken)
        auth_router._delete_admin_user("sara", _as_admin(teaching_users))
    assert woken == []
    assert TeachingStore().get_assignment(world.template_id, world.sara).status == "active"

    released: list[str] = []
    original = teaching_service.sync_material_references

    def spy(template_id, **kwargs):
        released.append(template_id)
        return original(template_id, **kwargs)

    monkeypatch.setattr(teaching_service, "sync_material_references", spy)

    assert account_handoff.retry_handoff(world.sara) is None
    assert woken == [world.template_id]
    assert released == [world.template_id]
    assert TeachingStore().get_assignment(world.template_id, world.sara).status == "revoked"


def test_a_material_only_retry_releases_the_students_paths(world, teaching_users, monkeypatch):
    with monkeypatch.context() as broken:
        broken.setattr(teaching_service, "sync_material_references", _broken)
        auth_router._delete_admin_user("sara", _as_admin(teaching_users))
    assert TeachingStore().get_assignment(world.template_id, world.sara).status == "revoked"

    released: list[str] = []
    monkeypatch.setattr(
        teaching_service, "sync_material_references",
        lambda template_id, **_kwargs: released.append(template_id),
    )

    assert account_handoff.retry_handoff(world.sara) is None
    assert released == [world.template_id]


def test_a_retry_hands_over_to_a_live_admin_when_the_successor_is_gone(
    world, teaching_users, monkeypatch
):
    with monkeypatch.context() as broken:
        broken.setattr(kb_registry, "transfer_ownership", _broken)
        auth_router._delete_admin_user("tina", _as_admin(teaching_users))
    # The successor account disappears before the retry.
    from deeptutor.multi_user.identity import delete_user

    assert delete_user("adam")

    assert account_handoff.retry_handoff(world.tina) is None
    assert kb_registry.get_kb(world.kb.kb_id).created_by == LOCAL_ADMIN_ID


def test_startup_retries_pending_handoffs_and_logs_each_attempt(
    world, teaching_users, monkeypatch, caplog
):
    with monkeypatch.context() as broken:
        broken.setattr(kb_registry, "transfer_ownership", _broken)
        broken.setattr(book_registry, "transfer_book_ownership", _broken)
        auth_router._delete_admin_user("tina", _as_admin(teaching_users))
        auth_router._delete_admin_user("sara", _as_admin(teaching_users))
    assert len(account_handoff.list_pending_handoffs()) == 2

    caplog.clear()
    with caplog.at_level(logging.INFO, logger=account_handoff.logger.name):
        report = account_handoff.retry_pending_handoffs()

    assert report == {"retried": 2, "cleared": 2, "pending": 0}
    assert account_handoff.list_pending_handoffs() == []
    assert _owned_by(world, world.adam)["knowledge_bases"]
    assert _owned_by(world, world.adam)["books"]
    attempt_lines = [r for r in caplog.records if "account hand-over retry" in r.getMessage()]
    assert len(attempt_lines) == 2


def test_startup_retry_is_bounded(world, teaching_users, monkeypatch):
    with monkeypatch.context() as broken:
        broken.setattr(kb_registry, "transfer_ownership", _broken)
        auth_router._delete_admin_user("tina", _as_admin(teaching_users))
        auth_router._delete_admin_user("sara", _as_admin(teaching_users))

    report = account_handoff.retry_pending_handoffs(limit=1)

    assert report == {"retried": 1, "cleared": 1, "pending": 1}


def test_the_lifespan_retries_pending_handoffs():
    import inspect

    from deeptutor.api import main

    assert "retry_pending_handoffs" in inspect.getsource(main.lifespan)


# ---- admin API ---------------------------------------------------------------


@pytest.fixture
def auth_http(teaching_users, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    tokens = {
        name: TokenPayload(username=name, role=record["role"], user_id=record["id"])
        for name, record in teaching_users.items()
    }
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "decode_token", lambda token: tokens.get(token))
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    client = TestClient(app)

    def call(method: str, path: str, user: str):
        return client.request(method, path, headers={"Authorization": f"Bearer {user}"})

    return call


def test_admin_lists_and_retries_pending_handoffs(world, monkeypatch, auth_http):
    with monkeypatch.context() as broken:
        broken.setattr(book_registry, "transfer_book_ownership", _broken)
        response = auth_http("DELETE", "/api/auth/users/tina", "adam")
        assert response.status_code == 200, response.text

    listed = auth_http("GET", "/api/auth/account-handoffs", "adam")
    assert listed.status_code == 200, listed.text
    assert [(h["user_id"], h["username"], h["stages"], h["successor_id"]) for h in listed.json()] == [
        (world.tina, "tina", ["books"], world.adam)
    ]

    retried = auth_http("POST", f"/api/auth/account-handoffs/{world.tina}/retry", "adam")
    assert retried.status_code == 200, retried.text
    assert retried.json() == {"cleared": True, "handoff": None}
    assert book_registry.get_book(world.book.book_id).created_by == world.adam
    assert auth_http("GET", "/api/auth/account-handoffs", "adam").json() == []

    again = auth_http("POST", f"/api/auth/account-handoffs/{world.tina}/retry", "adam")
    assert again.status_code == 404


@pytest.mark.parametrize("user", ["tom", "sara", "uma"])
def test_non_admins_cannot_list_or_retry_handoffs(world, teaching_users, monkeypatch, auth_http, user):
    with monkeypatch.context() as broken:
        broken.setattr(book_registry, "transfer_book_ownership", _broken)
        auth_router._delete_admin_user("tina", _as_admin(teaching_users))

    assert auth_http("GET", "/api/auth/account-handoffs", user).status_code == 403
    assert (
        auth_http("POST", f"/api/auth/account-handoffs/{world.tina}/retry", user).status_code
        == 403
    )
    assert [r.stages for r in account_handoff.list_pending_handoffs()] == [["books"]]
    assert book_registry.get_book(world.book.book_id).created_by == world.tina


def test_endpoints_do_not_block_the_event_loop(world, teaching_users, monkeypatch):
    """The retry runs blocking SQLite writers, so the route goes off-loop."""
    import threading

    loop_thread: dict[str, int] = {}
    threads: list[int] = []
    monkeypatch.setattr(
        account_handoff, "retry_handoff",
        lambda *_a, **_k: threads.append(threading.get_ident()),
    )
    monkeypatch.setattr(
        account_handoff, "list_pending_handoffs",
        lambda: threads.append(threading.get_ident()) or [],
    )

    async def main():
        loop_thread["id"] = threading.get_ident()
        await auth_router.list_account_handoffs(current=_as_admin(teaching_users))
        await auth_router.retry_account_handoff(world.tina, current=_as_admin(teaching_users))

    asyncio.run(main())
    assert len(threads) == 2
    assert loop_thread["id"] not in threads
