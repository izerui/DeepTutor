"""A deleted conversation takes the files it generated with it."""

from __future__ import annotations

from pathlib import Path

import pytest

from deeptutor.services.path_service import PathService
from deeptutor.services.workspace import ContentWorkspaceService, WorkspaceError
from deeptutor.services.workspace.session_outputs import (
    legacy_output_dirs,
    remove_session_outputs,
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    from deeptutor.services import path_service as path_module
    from deeptutor.services import workspace as package
    from deeptutor.services.workspace import service as module

    paths = PathService(workspace_root=tmp_path / "runtime")
    paths.ensure_all_directories()
    monkeypatch.setattr(module, "get_path_service", lambda: paths)
    monkeypatch.setattr(path_module, "get_path_service", lambda: paths)
    service = ContentWorkspaceService()
    monkeypatch.setattr(package, "get_content_workspace_service", lambda: service)
    monkeypatch.delenv("DEEPTUTOR_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("DEEPTUTOR_WORKSPACE_ALLOWED_ROOTS", raising=False)
    return service, paths


def _turn_file(service, session_id, turn_id, name, body, *, capability="chat", workspace_id=""):
    context = service.create_runtime_context(
        capability=capability, session_id=session_id, turn_id=turn_id, workspace_id=workspace_id
    )
    path = Path(context.output_dir) / name
    path.write_bytes(body)
    binding = service.binding_by_id(context.workspace_id)
    item = service.publish(binding, [{"path": service.relative_path(binding, path)}])[0]
    return path, item


def test_deleting_a_session_removes_its_outputs_and_snapshots_only(env):
    service, _ = env
    mine, my_item = _turn_file(service, "s_mine", "turn_1", "report.pdf", b"mine")
    render, render_item = _turn_file(
        service, "s_mine", "turn_2", "movie.mp4", b"video", capability="math_animator"
    )
    theirs, their_item = _turn_file(service, "s_other", "turn_1", "report.pdf", b"theirs")
    # Same bytes in another conversation share one snapshot blob.
    shared, shared_item = _turn_file(service, "s_other", "turn_2", "copy.pdf", b"mine")

    result = remove_session_outputs("s_mine")

    assert result["folders"] == 2
    assert result["snapshots"] == 2
    assert not mine.parent.parent.exists()
    assert not render.parent.parent.exists()
    for item in (my_item, render_item):
        with pytest.raises(WorkspaceError):
            service.resolve_published_item(item.workspace_id, item.workspace_item_id)
    assert theirs.read_bytes() == b"theirs"
    assert shared.read_bytes() == b"mine"
    for item, body in ((their_item, b"theirs"), (shared_item, b"mine")):
        blob, _ = service.resolve_published_item(item.workspace_id, item.workspace_item_id)
        assert blob.read_bytes() == body


def test_outputs_in_a_registered_workspace_are_removed_too(env):
    service, _ = env
    algebra = service.create_workspace("Algebra")
    path, item = _turn_file(
        service, "s_bound", "turn_1", "plot.png", b"png", workspace_id=algebra["workspace_id"]
    )
    assert Path(algebra["path"]) in path.parents

    remove_session_outputs("s_bound")

    assert not path.exists()
    with pytest.raises(WorkspaceError):
        service.resolve_published_item(item.workspace_id, item.workspace_item_id)


def test_legacy_turn_folders_are_removed_only_when_the_session_links_them(env, tmp_path):
    _, paths = env
    chat_root = paths.get_chat_workspace_root()
    lesson = chat_root / "video_lesson" / "turn_lesson" / "artifacts" / "lesson.html"
    other_lesson = chat_root / "video_lesson" / "turn_other" / "artifacts" / "lesson.html"
    book_clip = chat_root / "math_animator" / "book-bk_1-anim_1" / "artifacts" / "clip.mp4"
    session_state = chat_root / "chat" / "s_mine" / "loaded_tools.json"
    for path in (lesson, other_lesson, book_clip, session_state):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    (chat_root / "math_animator" / "turn_link").symlink_to(outside, target_is_directory=True)

    messages = [
        {
            "role": "assistant",
            "content": "Here is your lesson.",
            "events": [
                {
                    "type": "result",
                    "metadata": {
                        "page": {
                            "url": "/files/outputs/workspace/chat/video_lesson/turn_lesson/artifacts/lesson.html"
                        }
                    },
                }
            ],
        },
        {
            "role": "assistant",
            "content": "![clip](/files/outputs/workspace/chat/math_animator/book-bk_1-anim_1/artifacts/clip.mp4)"
            " and /files/outputs/workspace/chat/math_animator/turn_link/artifacts/a.mp4"
            " and /files/outputs/workspace/chat/../../settings/main.yaml",
        },
    ]
    found = legacy_output_dirs(messages)
    assert found == {("video_lesson", "turn_lesson"), ("math_animator", "turn_link")}

    remove_session_outputs("s_mine", found)

    assert not lesson.parent.parent.exists()
    assert not session_state.parent.exists()
    assert other_lesson.exists()
    assert book_clip.exists()
    assert not (chat_root / "math_animator" / "turn_link").exists()
    assert (outside / "keep.txt").read_text() == "keep"


def test_unsafe_session_ids_never_reach_outside_their_folder(env):
    service, paths = env
    path, _ = _turn_file(service, "../escape", "turn_1", "a.txt", b"a")
    keep = paths.get_chat_workspace_root() / "keep.txt"
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_text("keep")

    remove_session_outputs("../escape", {("chat", ".."), ("../x", "turn")})

    assert not path.exists()
    assert keep.read_text() == "keep"
    assert paths.get_chat_workspace_root().is_dir()
