from __future__ import annotations

from copy import deepcopy

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from deeptutor.api.routers import auth as auth_router
from deeptutor.api.routers import book as book_router
from deeptutor.book import registry as book_registry
from deeptutor.book.models import Block, BlockStatus, BlockType, Book, Page, PageStatus
from deeptutor.book.storage import BookStorage
from deeptutor.multi_user.book_permission import BookPermission
from deeptutor.services.auth import TokenPayload


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def shared_env(tmp_path, monkeypatch):
    from deeptutor.book import engine as engine_module
    from deeptutor.book import storage as storage_module
    from deeptutor.multi_user import audit, grants, identity, paths
    from deeptutor.multi_user.grants import learner_grant, save_grant
    from deeptutor.multi_user.identity import save_user, set_book_permission
    from deeptutor.multi_user.paths import get_path_service_for_scope, scope_for_user
    from deeptutor.services import auth as auth_service

    admin_root = (tmp_path / "data").resolve()
    system_root = admin_root / "system"
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(paths, "ADMIN_WORKSPACE_ROOT", admin_root)
    monkeypatch.setattr(paths, "USERS_ROOT", admin_root / "users")
    monkeypatch.setattr(paths, "SYSTEM_ROOT", system_root)
    monkeypatch.setattr(paths, "LEGACY_MULTI_USER_ROOT", tmp_path / "multi-user")
    monkeypatch.setattr(paths, "_path_services", {})
    monkeypatch.setattr(identity, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(identity, "SYSTEM_ROOT", system_root)
    monkeypatch.setattr(identity, "AUTH_DIR", system_root / "auth")
    monkeypatch.setattr(identity, "USERS_FILE", system_root / "auth" / "users.json")
    monkeypatch.setattr(identity, "SECRET_FILE", system_root / "auth" / "auth_secret")
    monkeypatch.setattr(identity, "LEGACY_USERS_FILE", tmp_path / "missing-users.json")
    monkeypatch.setattr(identity, "LEGACY_SECRET_FILE", tmp_path / "missing-secret")
    monkeypatch.setattr(grants, "GRANTS_DIR", system_root / "grants")
    monkeypatch.setattr(audit, "SYSTEM_ROOT", system_root)
    monkeypatch.setattr(auth_service, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_router, "POCKETBASE_ENABLED", False)
    monkeypatch.setattr(auth_service, "AUTH_USERNAME", "")
    monkeypatch.setattr(auth_service, "AUTH_PASSWORD_HASH", "")
    storage_module._storages.clear()
    engine_module._engines.clear()

    root = save_user("root", "hash", role="admin")
    alice = save_user("alice", "hash", role="user")
    bob = save_user("bob", "hash", role="user")
    editor = save_user("editor", "hash", role="user")
    teacher = save_user("teacher", "hash", role="teacher")
    default_learner = save_user("default-learner", "hash", role="user", preset="learner")
    books_without_acl = save_user("books-without-acl", "hash", role="user", preset="learner")
    reader = save_user("reader", "hash", role="user", preset="learner")
    # Stored grants are inert; use raw legacy fixtures, never the live create API.
    users = identity.load_users()
    for name, level in (("alice", "read"), ("bob", "read"), ("editor", "edit")):
        users[name]["book_permission"] = {"books": {"bk_shared": level}}
    identity._write_users(users)
    set_book_permission("default-learner", BookPermission(create=False))
    set_book_permission("books-without-acl", BookPermission(create=False))
    set_book_permission("reader", BookPermission(create=False))
    books_grant = deepcopy(learner_grant(reader["id"]))
    books_grant["learning_policy"]["allowed_surfaces"] = ["chat", "reading", "books"]
    save_grant(books_without_acl["id"], deepcopy(books_grant))
    save_grant(reader["id"], books_grant)
    # Spec 2026-10-29 P2: startup writes the retired learner preset into grants.
    from deeptutor.multi_user.role_migration import run_role_migration

    run_role_migration()

    tokens = {
        "root": TokenPayload(username="root", role="admin", user_id=root["id"]),
        "alice": TokenPayload(username="alice", role="user", user_id=alice["id"]),
        "bob": TokenPayload(username="bob", role="user", user_id=bob["id"]),
        "editor": TokenPayload(username="editor", role="user", user_id=editor["id"]),
        "teacher": TokenPayload(username="teacher", role="teacher", user_id=teacher["id"]),
        "default-learner": TokenPayload(
            username="default-learner", role="user", user_id=default_learner["id"]
        ),
        "books-without-acl": TokenPayload(
            username="books-without-acl", role="user", user_id=books_without_acl["id"]
        ),
        "reader": TokenPayload(username="reader", role="user", user_id=reader["id"]),
    }
    monkeypatch.setattr(auth_router, "decode_token", lambda token: tokens.get(token))

    shared = BookStorage(path_service=get_path_service_for_scope(
        scope_for_user(teacher["id"], is_admin=False),
    ))
    shared.save_book(Book(id="bk_shared", title="Shared", page_count=1))
    shared.save_page(
        Page(
            id="pg_1",
            book_id="bk_shared",
            title="Page",
            status=PageStatus.READY,
            blocks=[
                Block(
                    id="blk_1",
                    type=BlockType.TEXT,
                    status=BlockStatus.READY,
                    payload={"body": "Original"},
                )
            ],
        )
    )
    book_registry.register_book(
        base_dir=shared.path_service.get_book_dir(), dir_name="book_bk_shared",
        title="Shared", created_by=teacher["id"], visibility="public",
    )

    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    app.include_router(
        book_router.router,
        prefix="/api",
        dependencies=[
            Depends(auth_router.require_auth),
            Depends(auth_router.require_learning_surface),
        ],
    )
    app.include_router(book_router.ws_router, prefix="/ws")
    return TestClient(app), shared


def test_learner_books_surface_requires_explicit_grant(shared_env) -> None:
    client, _ = shared_env

    denied = client.get("/api/books", headers=_headers("default-learner"))
    allowed = client.get("/api/books", headers=_headers("reader"))

    assert denied.status_code == 403
    assert allowed.status_code == 200


def test_learner_with_books_surface_sees_public_books_without_legacy_acl(shared_env) -> None:
    client, _ = shared_env

    response = client.get("/api/books", headers=_headers("books-without-acl"))

    assert response.status_code == 200
    assert [book["id"] for book in response.json()["books"]] == ["bk_shared"]
    assert response.json()["can_create"] is False


def test_assigned_shared_book_is_read_only_for_learner(shared_env) -> None:
    client, _ = shared_env

    listing = client.get("/api/books", headers=_headers("reader")).json()
    assert [book["id"] for book in listing["books"]] == ["bk_shared"]
    assert listing["can_create"] is False
    assert listing["books"][0]["source"] == "shared"
    assert listing["books"][0]["permission"] == "read"
    assert listing["books"][0]["can_edit"] is False
    assert listing["books"][0]["can_delete"] is False

    create = client.post(
        "/api/books",
        headers=_headers("reader"),
        json={"user_intent": "New book"},
    )
    update = client.post(
        "/api/books/update-block",
        headers=_headers("reader"),
        json={
            "book_id": "bk_shared",
            "page_id": "pg_1",
            "block_id": "blk_1",
            "body": "Nope",
            "expected_revision": 1,
        },
    )
    delete = client.delete("/api/books/bk_shared", headers=_headers("reader"))

    assert create.status_code == 403
    assert update.status_code == 404
    assert delete.status_code == 404


def test_shared_learning_state_is_isolated_per_reader(shared_env) -> None:
    client, canonical = shared_env
    visit = client.post(
        "/api/books/progress/visit",
        headers=_headers("alice"),
        json={"book_id": "bk_shared", "page_id": "pg_1"},
    )
    assert visit.status_code == 200
    capture = client.post(
        "/api/books/bk_shared/learning-captures",
        headers=_headers("alice"),
        json={"page_id": "pg_1", "source_text": "Alice note"},
    )
    assert capture.status_code == 200
    chat = client.post(
        "/api/books/page-chat-session",
        headers=_headers("alice"),
        json={"book_id": "bk_shared", "page_id": "pg_1", "session_id": "alice-chat"},
    )
    assert chat.status_code == 200

    alice = client.get("/api/books/bk_shared", headers=_headers("alice")).json()
    bob = client.get("/api/books/bk_shared", headers=_headers("bob")).json()
    assert alice["progress"]["current_page_id"] == "pg_1"
    assert alice["book"]["metadata"]["page_chat_sessions"] == {"pg_1": "alice-chat"}
    assert bob["progress"]["current_page_id"] == ""
    assert bob["book"]["metadata"]["page_chat_sessions"] == {}
    assert client.get(
        "/api/books/bk_shared/learning-captures",
        headers=_headers("bob"),
    ).json() == {"captures": []}

    # Nothing reader-specific landed in the admin canonical directory.
    assert canonical.load_progress("bk_shared") is None
    assert canonical.load_learning_captures("bk_shared") == []
    assert canonical.load_book("bk_shared").metadata.get("page_chat_sessions") is None


def test_shared_read_user_cannot_edit_or_delete(shared_env) -> None:
    client, canonical = shared_env
    update = client.post(
        "/api/books/update-block",
        headers=_headers("alice"),
        json={
            "book_id": "bk_shared",
            "page_id": "pg_1",
            "block_id": "blk_1",
            "body": "Nope",
            "expected_revision": 1,
        },
    )
    assert update.status_code == 404
    assert (
        client.delete(
            "/api/books/bk_shared",
            headers=_headers("alice"),
        ).status_code
        == 404
    )
    assert canonical.load_book("bk_shared") is not None


def test_shared_read_health_check_does_not_mutate_canonical_book(shared_env) -> None:
    client, canonical = shared_env
    book = canonical.load_book("bk_shared")
    assert book is not None
    book.stale_page_ids = ["pg_1"]
    canonical.save_book(book)
    updated_at = canonical.load_book("bk_shared").updated_at

    response = client.get(
        "/api/books/bk_shared/health",
        headers=_headers("alice"),
    )

    assert response.status_code == 200
    assert response.json()["kb_drift"]["cached"] is True
    assert response.json()["kb_drift"]["stale_page_ids"] == ["pg_1"]
    assert canonical.load_book("bk_shared").updated_at == updated_at


def test_admin_cannot_edit_a_foreign_public_book(shared_env) -> None:
    # spec 2026-10-29 D10 (P4): an admin withdraws or deletes another account's
    # public book; editing it is refused even with the current revision.
    client, canonical = shared_env
    response = client.post(
        "/api/books/update-block",
        headers=_headers("root"),
        json={
            "book_id": "bk_shared",
            "page_id": "pg_1",
            "block_id": "blk_1",
            "body": "Edited",
            "expected_revision": 1,
        },
    )
    assert response.status_code in (403, 404)
    assert canonical.load_page("bk_shared", "pg_1").blocks[0].payload["body"] == "Original"


def test_shared_capabilities_are_returned_in_list_and_detail(shared_env) -> None:
    client, _ = shared_env
    listing = client.get("/api/books", headers=_headers("alice")).json()
    assert listing["can_create"] is True
    assert listing["books"][0]["source"] == "shared"
    assert listing["books"][0]["permission"] == "read"
    assert listing["books"][0]["can_edit"] is False
    assert listing["books"][0]["can_delete"] is False

    detail = client.get("/api/books/bk_shared", headers=_headers("editor")).json()
    assert detail["book"]["revision"] == 1
    assert detail["book"]["can_edit"] is False
    assert detail["book"]["can_delete"] is False
    assert detail["book"]["ref"] == "book:bk_shared"
    assert detail["book"]["visibility"] == "public"
    assert detail["book"]["group"] == "public"
    assert not detail["book"]["can_set_visibility"]
    own = client.get("/api/books/book:bk_shared", headers=_headers("teacher")).json()["book"]
    assert own["source"] == "own" and own["group"] == "mine"
    assert own["can_edit"] and own["can_delete"] and own["can_set_visibility"]


def test_retired_edit_grant_does_not_allow_writing(shared_env) -> None:
    client, canonical = shared_env
    response = client.post(
        "/api/books/update-block", headers=_headers("editor"),
        json={"book_id": "book:bk_shared", "page_id": "pg_1", "block_id": "blk_1",
              "body": "Denied", "expected_revision": 1},
    )
    assert response.status_code == 404
    assert canonical.load_page("bk_shared", "pg_1").blocks[0].payload["body"] == "Original"


def test_rest_producer_and_canonical_ws_replay_ignore_local_id_collision(shared_env):
    # spec 2026-10-29 D10 (P4): admins no longer edit others' public books; the owner edits.
    from deeptutor.book.models import Spine
    from deeptutor.multi_user.identity import get_user
    from deeptutor.multi_user.paths import get_path_service_for_scope, scope_for_user

    client, canonical = shared_env
    canonical.save_spine(Spine(book_id="bk_shared"))
    alice = get_user("alice")
    local = BookStorage(path_service=get_path_service_for_scope(
        scope_for_user(alice["id"], is_admin=False),
    ))
    local.save_book(Book(id="bk_shared", title="Alice import"))
    with client:
        # A real REST engine producer runs under the editing admin's context.
        paused = client.post(
            "/api/books/pause", headers=_headers("teacher"), json={"book_id": "book:bk_shared"},
        )
        assert paused.status_code == 200
        with client.websocket_connect("/ws/books?token=alice") as ws:
            ws.send_json({"type": "subscribe", "book_id": "bk_shared"})
            ack = ws.receive_json()
            assert ack["type"] == "subscribed" and ack["latest_seq"] == 0
            ws.send_json({"type": "subscribe", "book_id": "book:bk_shared"})
            ack = ws.receive_json()
            assert ack["type"] == "subscribed" and ack["latest_seq"] == 1
            assert ack["book_id"] == "book:bk_shared"
            assert ws.receive_json()["metadata"]["kind"] == "compilation_paused"
            paused = client.post(
                "/api/books/pause", headers=_headers("teacher"), json={"book_id": "book:bk_shared"},
            )
            assert paused.status_code == 200
            assert ws.receive_json()["metadata"]["kind"] == "compilation_paused"
        assert local.load_book("bk_shared").title == "Alice import"


@pytest.mark.parametrize("transport", ["rest", "websocket"])
def test_real_creation_transports_expose_registered_private_book(shared_env, monkeypatch, transport):
    from deeptutor.book.engine import BookEngine
    from deeptutor.book.models import BookProposal

    client, _ = shared_env

    async def proposal(*_args, **_kwargs):
        return BookProposal(title="New")

    monkeypatch.setattr(BookEngine, "_run_ideation", proposal)
    payload = None
    if transport == "rest":
        response = client.post(
            "/api/books", headers=_headers("teacher"), json={"user_intent": "Waves"},
        )
        assert response.status_code == 200
        payload = response.json()["book"]
    else:
        with client.websocket_connect("/ws/books?token=teacher") as ws:
            ws.send_json({"type": "create", "user_intent": "Waves"})
            ready = False
            payload = None
            for _ in range(12):
                frame = ws.receive_json()
                if frame.get("metadata", {}).get("kind") == "proposal_ready":
                    record = book_registry.get_book(frame["metadata"]["book_id"])
                    assert record is not None and record.visibility == "private"
                    assert book_registry.kb_links_initialized(record.book_id)
                    ready = True
                if frame.get("type") == "create_result":
                    payload = frame["book"]
                    break
            # Fanout scheduling may deliver create_result before ready; registration
            # must already be committed at either first observable success.
            assert payload is not None
            assert ready or book_registry.kb_links_initialized(payload["id"])
    assert payload is not None
    record = book_registry.get_book(payload["id"])
    assert record.created_by == payload["owner_id"]
    assert record.visibility == payload["visibility"] == "private"
    assert record.ref == payload["ref"]
    assert payload["group"] == "mine" and payload["can_set_visibility"]


def test_admin_delete_preserves_inert_legacy_evidence(shared_env) -> None:
    from deeptutor.multi_user.book_permission import legacy_book_shares
    from deeptutor.multi_user.identity import get_user

    client, canonical = shared_env
    response = client.delete(
        "/api/books/bk_shared",
        headers=_headers("root"),
    )

    assert response.status_code == 200
    assert canonical.load_book("bk_shared") is None
    alice = get_user("alice")
    editor = get_user("editor")
    assert alice is not None and editor is not None
    assert legacy_book_shares(alice.get("book_permission")).level_for("bk_shared") == "read"
    assert legacy_book_shares(editor.get("book_permission")).level_for("bk_shared") == "edit"
