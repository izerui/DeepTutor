"""Render-file cleanup for published Math Animator artifacts (upstream 38cf3979d)."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import pytest

from tests.api.test_workspace_router import workspace_api as workspace_api


@pytest.fixture
def render_api(workspace_api, monkeypatch: pytest.MonkeyPatch):
    from deeptutor.api.routers import render_storage
    from deeptutor.services import session

    client, service = workspace_api
    monkeypatch.setattr(render_storage, "get_content_workspace_service", lambda: service)
    store = SimpleNamespace(list_nonterminal_turns=AsyncMock(return_value=[]))
    monkeypatch.setattr(session, "get_session_store", lambda: store)
    binding = service.current_binding(ensure_output=True)
    turn = binding.root / "outputs/math_animator/session/turn"
    artifact = turn / "artifacts/movie.mp4"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"final video")
    source = turn / "source/scene.py"
    source.parent.mkdir()
    source.write_text("print('source')")
    cache = turn / "media/videos/partial_movie_files/segment.mp4"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"temporary render")
    item = service.publish(binding, [{"path": service.relative_path(binding, artifact)}])[0]
    item = replace(item, url=urlsplit(item.url).path)
    return client, service, item, turn, store


def test_preview_cleanup_and_playback_preserve_final_files(render_api):
    client, _, item, turn, _ = render_api
    preview = client.get(item.url + "/render-storage")
    assert preview.status_code == 200, preview.json()
    body = preview.json()
    assert body["file_bytes"] == len(b"final video")
    assert body["cache_bytes"] == len(b"temporary render")
    assert body["cache_files"] == 1
    # Develop manages storage locations itself: no server path leaves the API.
    assert not {"file_path", "cache_path", "can_reveal"} & set(body)
    assert str(turn) not in preview.text
    assert client.get(item.url + "/render-storage").json() == body
    cleaned = client.post(item.url + "/clean-render-cache", json={"fingerprint": body["fingerprint"]})
    assert cleaned.status_code == 200
    assert cleaned.json()["freed_bytes"] == len(b"temporary render")
    assert "file_path" not in cleaned.json()
    assert not (turn / "media").exists()
    assert (turn / "source/scene.py").read_text() == "print('source')"
    assert (turn / "artifacts/movie.mp4").read_bytes() == b"final video"
    assert client.get(item.url).content == b"final video"
    assert client.get(item.url + "/render-storage").json()["cache_files"] == 0


def _publish_chat_output(service, suffix="exec/result.txt"):
    binding = service.current_binding(ensure_output=True)
    relative = f"outputs/chat/session/turn_one/{suffix}"
    path = service.resolve(binding, relative, write=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"result")
    item = service.publish(binding, [{"path": relative}])[0]
    return urlsplit(item.url).path, path


@pytest.mark.parametrize("suffix", ["result.txt", "exec/result.txt", "exec/outputs/chat/s/turn_x/result.txt"])
def test_chat_output_cleanup_keeps_snapshot(render_api, suffix):
    client, service, _, _, _ = render_api
    url, path = _publish_chat_output(service, suffix)
    preview = client.get(url + "/file-storage")
    assert preview.status_code == 200
    body = preview.json()
    assert set(body) == {"file_bytes", "fingerprint"}
    assert body["file_bytes"] == 6
    delete = url + "/remove-generated-output"
    assert client.post(delete, json={}).status_code == 422
    assert client.post(delete, json={"fingerprint": "0" * 64}).status_code == 409
    assert client.post(url + "/reveal-generated-output").status_code == 404
    response = client.post(delete, json={"fingerprint": body["fingerprint"]})
    assert response.status_code == 200
    assert response.json() == {"removed": True, "file_bytes": 6}
    assert not path.exists()
    assert client.get(url + "/file-storage").status_code == 404
    assert client.get(url).content == b"result"


@pytest.mark.parametrize("change", ["content", "active", "symlink"])
def test_chat_output_cleanup_rejects_unsafe_removal(render_api, change):
    client, service, _, _, store = render_api
    url, path = _publish_chat_output(service)
    fingerprint = client.get(url + "/file-storage").json()["fingerprint"]
    if change == "content":
        stat = path.stat()
        path.write_bytes(b"edited")
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        # Even a fresh confirmation cannot authorize deleting edited content.
        fingerprint = client.get(url + "/file-storage").json()["fingerprint"]
    elif change == "active":
        store.list_nonterminal_turns.return_value = [{"id": "running"}]
    else:
        target = path.with_name("keep.txt")
        path.rename(target)
        path.symlink_to(target)
        assert client.get(url + "/file-storage").status_code == 404
    response = client.post(url + "/remove-generated-output", json={"fingerprint": fingerprint})
    assert response.status_code == 409
    assert str(path.parent) not in response.text
    assert path.read_bytes() == (b"edited" if change == "content" else b"result")
    assert client.get(url).content == b"result"


def test_cleanup_refuses_changed_preview(render_api):
    client, _, item, turn, _ = render_api
    preview = client.get(item.url + "/render-storage").json()
    (turn / "media/new.txt").write_text("another render")
    response = client.post(
        item.url + "/clean-render-cache", json={"fingerprint": preview["fingerprint"]}
    )
    assert response.status_code == 409
    assert (turn / "media/new.txt").exists()


def test_cleanup_refuses_active_turns(render_api):
    client, _, item, turn, store = render_api
    preview = client.get(item.url + "/render-storage").json()
    store.list_nonterminal_turns.return_value = [{"id": "running"}]
    assert (
        client.post(
            item.url + "/clean-render-cache", json={"fingerprint": preview["fingerprint"]}
        ).status_code
        == 409
    )
    assert (turn / "media").exists()


def test_cleanup_excludes_other_writers(render_api):
    from deeptutor.services.workspace.activity import data_activity

    client, _, item, turn, _ = render_api
    preview = client.get(item.url + "/render-storage").json()
    with data_activity():
        response = client.post(
            item.url + "/clean-render-cache", json={"fingerprint": preview["fingerprint"]}
        )
    assert response.status_code == 409
    assert (turn / "media").exists()


@pytest.mark.parametrize("position", ["media", "child", "artifact-parent"])
def test_symlinks_never_delete_the_target(render_api, tmp_path: Path, position: str):
    client, _, item, turn, _ = render_api
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep")
    preview = client.get(item.url + "/render-storage").json()
    if position == "child":
        (turn / "media/link").symlink_to(outside, target_is_directory=True)
    elif position == "media":
        (turn / "media").rename(turn / "original-media")
        (turn / "media").symlink_to(outside, target_is_directory=True)
    else:
        (turn / "artifacts").rename(turn / "original-artifacts")
        (turn / "artifacts").symlink_to(turn / "original-artifacts", target_is_directory=True)
    assert client.get(item.url + "/render-storage").status_code == 404
    assert (
        client.post(
            item.url + "/clean-render-cache", json={"fingerprint": preview["fingerprint"]}
        ).status_code
        == 409
    )
    assert sentinel.read_text() == "keep"


def test_unrelated_and_missing_originals_are_not_cleanable(render_api):
    client, service, item, turn, _ = render_api
    (turn / "artifacts/movie.mp4").unlink()
    assert client.get(item.url + "/render-storage").status_code == 404
    binding = service.current_binding()
    (binding.root / "notes.md").write_text("private note")
    note = service.publish(binding, [{"path": "notes.md"}])[0]
    assert client.get(urlsplit(note.url).path + "/render-storage").status_code == 404
    assert client.get(item.url).content == b"final video"


def test_another_account_can_neither_open_nor_clean_the_render(render_api, tmp_path: Path):
    """Workspace ids are derived from the account: someone else's id resolves to nothing."""
    from deeptutor.api.routers.auth import require_auth
    from deeptutor.multi_user.context import set_current_user
    from deeptutor.multi_user.models import CurrentUser, UserScope

    client, service, item, turn, _ = render_api
    preview = client.get(item.url + "/render-storage").json()
    url, path = _publish_chat_output(service)
    confirmation = {"fingerprint": client.get(url + "/file-storage").json()["fingerprint"]}
    bob = CurrentUser(
        id="u_bob",
        username="bob",
        role="user",
        scope=UserScope(kind="user", user_id="u_bob", root=tmp_path / "users" / "u_bob"),
    )

    async def as_bob():
        set_current_user(bob)
        return None

    client.app.dependency_overrides[require_auth] = as_bob
    try:
        assert client.get(url).status_code == 404
        assert client.get(url + "/file-storage").status_code == 404
        assert client.post(url + "/remove-generated-output", json=confirmation).status_code == 409
        assert client.get(item.url).status_code == 404
        assert client.head(item.url).status_code == 404
        assert client.get(item.url + "/render-storage").status_code == 404
        response = client.post(
            item.url + "/clean-render-cache", json={"fingerprint": preview["fingerprint"]}
        )
        assert response.status_code == 409
    finally:
        client.app.dependency_overrides.clear()
    assert (turn / "media/videos/partial_movie_files/segment.mp4").exists()
    assert path.read_bytes() == b"result"
    assert client.get(item.url).content == b"final video"
