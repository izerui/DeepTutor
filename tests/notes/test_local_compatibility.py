"""Local account/workspace boundaries and legacy note compatibility."""

from contextlib import contextmanager
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from deeptutor.api.routers import co_writer
from deeptutor.co_writer.storage import CoWriterStorage, get_co_writer_storage
from deeptutor.multi_user.models import CurrentUser, UserScope
from deeptutor.multi_user.paths import user_context
from deeptutor.notes.resources import normalize_note_resources
from deeptutor.services.path_service import get_path_service
from deeptutor.services.workspace import ContentWorkspaceService
from deeptutor.services.workspace.context import workspace_context
from tests.notes.test_block_pages import Paths, block


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    from deeptutor.multi_user import paths

    monkeypatch.delenv("DEEPTUTOR_WORKSPACE_ROOT", raising=False)
    monkeypatch.setattr(paths, "ADMIN_WORKSPACE_ROOT", tmp_path / "admin")
    monkeypatch.setattr(paths, "USERS_ROOT", tmp_path / "users")
    monkeypatch.setattr(paths, "SYSTEM_ROOT", tmp_path / "system")
    monkeypatch.setattr(paths, "_legacy_migration_done", True)

    @contextmanager
    def account(name):
        root = tmp_path / "users" / name
        user = CurrentUser(name, name, "student", UserScope("user", name, root))
        with user_context(user):
            yield ContentWorkspaceService()

    return account


def test_legacy_manifest_reads_without_rewriting_and_migrates_losslessly(tmp_path):
    storage = CoWriterStorage(Paths(tmp_path))
    doc_id = "abcdef123456"
    path = storage.manifest_path(doc_id)
    path.parent.mkdir(parents=True)
    original = "# Original\n\n```md\n**literal**\n```\n"
    payload = json.dumps(
        dict(id=doc_id, title="Old", content=original, created_at=1, updated_at=2)
    )
    path.write_text(payload)
    doc = storage.load_document(doc_id)
    assert doc.content == original and doc.blocks is None and doc.revision == 0
    assert path.read_text() == payload
    storage.update_document(doc_id, blocks=[block()], expected_revision=0)
    assert (path.parent / "legacy.md").read_text() == original


def test_manifest_identity_cannot_redirect_saves(tmp_path):
    storage = CoWriterStorage(Paths(tmp_path))
    first = storage.create_document(content="first")
    second = storage.create_document(content="second")
    path = storage.manifest_path(first.id)
    data = json.loads(path.read_text())
    data["id"] = second.id
    path.write_text(json.dumps(data))
    storage.update_document(first.id, content="repaired")
    assert storage.load_document(second.id).content == "second"
    assert storage.load_document(first.id).content == "repaired"
    assert storage.load_document(first.id).id == first.id


@pytest.mark.parametrize("boundary", ["account", "workspace"])
def test_http_notes_assets_review_and_export_stay_in_their_scope(accounts, boundary):
    app = FastAPI()
    app.include_router(co_writer.router, prefix="/api")
    client = TestClient(app)
    with accounts("alice") as service:
        workspace = service.create_workspace("Private")["workspace_id"]
        with workspace_context(workspace):
            page = client.post(
                "/api/documents",
                json={
                    "blocks": [
                        block(),
                        {
                            "id": "cards",
                            "type": "learning",
                            "props": {
                                "kind": "flashcards",
                                "data": json.dumps({"cards": [{"front": "Secret", "back": "A"}]}),
                            },
                        },
                    ]
                },
            ).json()
            root = f"/api/documents/{page['id']}"
            suggestion = client.post(
                root + "/suggestions", json={"operation": "delete", "block_id": "a"}
            ).json()
            asset = client.post(
                root + "/assets", files={"file": ("a.png", b"private", "image/png")}
            ).json()["url"]
            card = client.get("/api/documents/review/due").json()["cards"][0]
            assert client.get(root + "/flashcards/export").status_code == 200
            assert client.get(asset).content == b"private"

        with accounts("bob" if boundary == "account" else "alice"):
            with workspace_context():
                assert client.get("/api/documents").json()["documents"] == []
                assert client.get(root).status_code == 404
                assert client.get(asset).status_code == 404
                assert client.get(root + "/suggestions").status_code == 404
                assert client.get(root + "/flashcards/export").status_code == 404
                assert client.get("/api/documents/flashcards/export").status_code == 404
                assert client.get("/api/documents/review/due").json()["cards"] == []
                assert client.post(
                    root + "/review",
                    json={"card_id": card["card_id"], "remembered": True},
                ).status_code == 400
                assert client.post(
                    root + "/suggestions/" + suggestion["id"], json={"accept": True}
                ).status_code == 400
                assert client.delete(root).status_code == 404
        with workspace_context(workspace):
            assert get_co_writer_storage().load_document(page["id"]).content.startswith("Hello")
            assert client.get(asset).content == b"private"


def test_generated_resources_default_to_request_workspace(accounts):
    with accounts("alice") as service:
        workspace = service.create_workspace("Research")["workspace_id"]
        binding = service.binding_by_id(workspace)
        path = binding.root / "outputs" / "chart.png"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"workspace chart")
        default = service.current_binding().root / "outputs" / "chart.png"
        default.parent.mkdir(parents=True, exist_ok=True)
        default.write_bytes(b"wrong default chart")
        with workspace_context(workspace):
            markdown, _ = normalize_note_resources("![chart](outputs/chart.png)")
            assert f"/files/workspace-items/{workspace}/" in markdown


def test_note_storage_resolves_actual_account_paths(accounts):
    with accounts("alice"):
        alice = get_co_writer_storage().create_document(content="Alice")
        alice_root = get_path_service().get_co_writer_docs_dir()
    with accounts("bob"):
        assert get_path_service().get_co_writer_docs_dir() != alice_root
        assert get_co_writer_storage().load_document(alice.id) is None
    with accounts("alice"):
        assert get_co_writer_storage().load_document(alice.id).content == "Alice"
