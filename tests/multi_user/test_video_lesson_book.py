"""A chat video lesson inserted into a book plays for everyone who may read the book.

Spec 2026-11-20 §6 (batch 5): the lesson page is published *with the book*
(copied into its assets), not linked into the author's private outputs, so a
learner who joined a shared/listed book, or reads it through a course, can play
it; nobody else can. Older animation blocks had the same problem and are served
the same way.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import HTTPException
import pytest

from deeptutor.api.routers import book as book_router
from deeptutor.book.models import (
    Block,
    BlockStatus,
    BlockType,
    Book,
    Chapter,
    Page,
    PageStatus,
    Spine,
)

BOOK = "bk_lessons"
LESSON_HTML = "<!doctype html><html><body><script>/* player */</script></body></html>"


def _own_output(relative: str, text: str = LESSON_HTML) -> str:
    """Write a file in the current account's outputs; return its /files/outputs URL."""
    from deeptutor.multi_user.paths import get_current_path_service

    root = get_current_path_service().get_public_outputs_root()
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return f"/files/outputs/{relative}"


def _lesson_url(turn: str = "turn_1") -> str:
    return _own_output(f"workspace/chat/video_lesson/{turn}/artifacts/lesson.html")


@pytest.fixture
def tina_book(as_account, teaching_users):
    """Tina's public book (empty publish scope: only who joined reads it), two chapters."""
    from deeptutor.book import registry as book_registry
    from deeptutor.book.storage import get_book_storage
    from deeptutor.multi_user import book_access
    from deeptutor.publishing import scope as scopes

    with as_account("tina"):
        storage = get_book_storage()
        storage.save_book(Book(id=BOOK, title="Waves", page_count=2, chapter_count=2))
        storage.save_spine(Spine(book_id=BOOK, chapters=[
            Chapter(id="ch_1", title="Nodes", page_ids=["pg_1"], order=0),
            Chapter(id="ch_2", title="Beats", page_ids=["pg_2"], order=1),
        ]))
        for page_id, chapter in (("pg_1", "ch_1"), ("pg_2", "ch_2")):
            storage.save_page(Page(
                id=page_id, book_id=BOOK, chapter_id=chapter, title=f"Page {page_id}",
                status=PageStatus.READY,
                blocks=[Block(id=f"txt_{page_id}", type=BlockType.TEXT, status=BlockStatus.READY,
                              payload={"body": "Text"})],
            ))
        record = book_access.register_current_scope_book(BOOK)
        book_registry.set_book_visibility(BOOK, "public")
        scopes._store_scope(scopes.BOOK, BOOK, scopes.Scope(), actor_id=teaching_users["tina"]["id"])
    return record


def _insert(page_id: str = "pg_1", **extra):
    request = book_router.InsertVideoLessonRequest(
        book_id=extra.pop("book_id", BOOK), page_id=page_id,
        output_url=extra.pop("output_url"), title=extra.pop("title", "Standing waves"),
        summary=extra.pop("summary", "Nodes and antinodes."), **extra,
    )
    return asyncio.run(book_router.insert_video_lesson(request))


def _play(block_id: str, page_id: str = "pg_1", name: str = "lesson.html"):
    return asyncio.run(book_router.get_block_media(BOOK, page_id, block_id, name))


def _denied(call) -> int:
    with pytest.raises(HTTPException) as caught:
        call()
    return caught.value.status_code


def _inserted_lesson(as_account, page_id: str = "pg_1") -> str:
    with as_account("tina"):
        return _insert(page_id, output_url=_lesson_url())["block"]["id"]


# ── inserting ────────────────────────────────────────────────


def test_inserting_copies_the_lesson_into_the_book(as_account, tina_book) -> None:
    from deeptutor.book.storage import get_book_storage
    from deeptutor.multi_user.paths import get_current_path_service

    with as_account("tina"):
        url = _lesson_url()
        result = _insert(output_url=url)
        block = result["block"]
        assert block["type"] == "video_lesson" and block["status"] == "ready"
        assert block["title"] == "Standing waves"
        payload = block["payload"]
        assert payload["media"] == ["lesson.html"]
        assert payload["summary"] == "Nodes and antinodes."
        assert payload["lesson_url"] == (
            f"/api/books/{BOOK}/pages/pg_1/blocks/{block['id']}/media/lesson.html"
        )
        assert "/files/outputs" not in str(payload)
        published = (
            get_book_storage().path_service.get_book_assets_dir(BOOK)
            / "blocks" / block["id"] / "lesson.html"
        )
        assert published.read_text(encoding="utf-8") == LESSON_HTML
        # The chat original stays where it was.
        original = get_current_path_service().get_public_outputs_root() / url[len("/files/outputs/"):]
        assert original.is_file()
        page = get_book_storage().load_page(BOOK, "pg_1")
        assert [b.type for b in page.blocks] == [BlockType.TEXT, BlockType.VIDEO_LESSON]


def test_the_book_keeps_playing_after_the_chat_original_is_gone(as_account, teaching_users, tina_book, deliver) -> None:
    from deeptutor.multi_user.paths import get_current_path_service

    with as_account("tina"):
        url = _lesson_url()
        block_id = _insert(output_url=url)["block"]["id"]
        (get_current_path_service().get_public_outputs_root() / url[len("/files/outputs/"):]).unlink()
    deliver("book", BOOK, "sara")
    with as_account("sara"):
        assert Path(_play(block_id).path).read_text(encoding="utf-8") == LESSON_HTML


def test_only_the_callers_own_chat_lesson_page_can_be_inserted(as_account, teaching_users, tina_book) -> None:
    with as_account("tina"):
        tinas = _lesson_url()
        source = _own_output("workspace/chat/video_lesson/turn_1/source/Lesson.tsx", "secret")
        other = _own_output("workspace/chat/math_animator/turn_1/artifacts/clip.mp4", "mp4")
        for bad in (source, other, "/files/outputs/../../etc/passwd", "https://example.com/x.html"):
            assert _denied(lambda: _insert(output_url=bad)) == 400
    # Sara owns no such page: Tina's URL resolves in Sara's own workspace.
    with as_account("sara"):
        assert _denied(lambda: _insert(output_url=tinas)) == 404


def test_only_an_editor_of_the_book_can_insert(as_account, teaching_users, tina_book, deliver) -> None:
    deliver("book", BOOK, "sara")
    with as_account("sara"):
        url = _lesson_url()
        assert _denied(lambda: _insert(output_url=url)) == 404


def test_lessons_are_not_generated_inside_the_book(as_account, tina_book) -> None:
    with as_account("tina"):
        for call in (
            lambda: book_router.insert_block(book_router.InsertBlockRequest(
                book_id=BOOK, page_id="pg_1", block_type="video_lesson")),
            lambda: book_router.change_block_type(book_router.ChangeBlockTypeRequest(
                book_id=BOOK, page_id="pg_1", block_id="txt_pg_1", new_type="video_lesson")),
        ):
            assert _denied(lambda: asyncio.run(call())) == 400


def test_regenerating_or_force_compiling_keeps_the_lesson(as_account, tina_book) -> None:
    from deeptutor.book.blocks.base import BlockContext, get_block_registry
    from deeptutor.book.engine import BookEngine
    from deeptutor.book.storage import get_book_storage

    block_id = _inserted_lesson(as_account)
    with as_account("tina"):
        storage = get_book_storage()
        page = storage.load_page(BOOK, "pg_1")
        block = page.block_by_id(block_id)
        before = dict(block.payload)
        BookEngine._reset_page_for_force_compile(page)
        assert block.status == BlockStatus.READY and block.payload == before
        generator = get_block_registry().get(BlockType.VIDEO_LESSON)
        chapter = storage.load_spine(BOOK).chapters[0]
        asyncio.run(generator.generate(BlockContext(
            book_id=BOOK, chapter=chapter, page=page, block=block, storage=storage,
        )))
        assert block.status == BlockStatus.READY and block.payload == before
        empty = Block(type=BlockType.VIDEO_LESSON)
        asyncio.run(generator.generate(BlockContext(
            book_id=BOOK, chapter=chapter, page=page, block=empty, storage=storage,
        )))
        assert empty.status == BlockStatus.ERROR and "Add to book" in empty.error


# ── playing ──────────────────────────────────────────────────


def test_owner_and_joined_learner_play_it_and_a_non_member_cannot(as_account, teaching_users, tina_book, deliver) -> None:
    from deeptutor.api.routers.outputs import SANDBOXED_LESSON_CSP

    block_id = _inserted_lesson(as_account)
    deliver("book", BOOK, "sara")
    for name in ("tina", "sara"):
        with as_account(name):
            response = _play(block_id)
            assert Path(response.path).read_text(encoding="utf-8") == LESSON_HTML
            assert response.headers["content-security-policy"] == SANDBOXED_LESSON_CSP
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.media_type == "text/html"
    for name in ("uma", "sam"):
        with as_account(name):
            assert _denied(lambda: _play(block_id)) == 404


def test_only_listed_media_of_a_block_on_that_page_is_served(as_account, teaching_users, tina_book, deliver) -> None:
    from deeptutor.book.storage import get_book_storage

    block_id = _inserted_lesson(as_account)
    with as_account("tina"):
        assets = get_book_storage().path_service.get_book_assets_dir(BOOK)
        (assets / "blocks" / block_id / "notes.txt").write_text("not listed", encoding="utf-8")
        (assets / "secret.html").write_text("x", encoding="utf-8")
        assert _denied(lambda: _play(block_id, name="notes.txt")) == 404
        assert _denied(lambda: _play(block_id, name="..")) == 404
        assert _denied(lambda: _play(block_id, page_id="pg_2")) == 404
        assert _denied(lambda: _play("../secret.html")) == 404
        asyncio.run(book_router.delete_block(book_router.DeleteBlockRequest(
            book_id=BOOK, page_id="pg_1", block_id=block_id)))
        assert _denied(lambda: _play(block_id)) == 404


def test_a_course_learner_plays_the_lesson_of_a_book_lesson(as_account, teaching_users, tina_book, deliver) -> None:
    """A course's "book" lesson points at the chapter; its learners can play the video there."""
    from deeptutor.tasks.courses import CourseService
    from deeptutor.tasks.service import TaskService
    from deeptutor.tasks.store import db as task_db

    block_id = _inserted_lesson(as_account)
    with as_account("tina"):
        task = TaskService().create("book", "Read the waves chapter")
        course = CourseService().create("Waves course")
    with task_db.write() as conn:
        conn.execute("UPDATE tasks SET target_id = ? WHERE id = ?", (BOOK, task["id"]))
        conn.execute("INSERT INTO course_items VALUES (?, ?, 0)", (course["id"], task["id"]))
        conn.execute("UPDATE tasks SET status = 'published' WHERE id = ?", (task["id"],))
        conn.execute("UPDATE courses SET status = 'published' WHERE id = ?", (course["id"],))
    deliver("course", course["id"], "sara")
    with as_account("sara"):
        assert Path(_play(block_id).path).read_text(encoding="utf-8") == LESSON_HTML
        # The reader shows the block itself, with the URL it plays from.
        page = asyncio.run(book_router.get_page(BOOK, "pg_1"))["page"]
        lesson = next(b for b in page["blocks"] if b["id"] == block_id)
        assert lesson["payload"]["lesson_url"].endswith(f"/blocks/{block_id}/media/lesson.html")
    with as_account("uma"):
        assert _denied(lambda: _play(block_id)) == 404


def test_a_market_visitor_plays_the_trial_chapter_only(as_account, teaching_users, tina_book) -> None:
    from deeptutor.catalog.listings import ListingService

    first = _inserted_lesson(as_account, "pg_1")
    second = _inserted_lesson(as_account, "pg_2")
    meta = dict(title="Waves", summary="Waves.", stages=["primary"], subjects=["primary.math"],
                difficulty="beginner", tags=[])
    with as_account("tina"):
        ListingService().save("book", BOOK, **meta)
        ListingService().submit("book", BOOK)
    with as_account("adam"):
        if ListingService().review_queue():
            ListingService().approve("book", BOOK)
    with as_account("uma"):
        assert Path(_play(first).path).is_file()
        assert _denied(lambda: _play(second, page_id="pg_2")) == 404


def test_a_private_book_lesson_is_only_for_its_owner(as_account, teaching_users, tina_book, deliver) -> None:
    from deeptutor.book import registry as book_registry

    block_id = _inserted_lesson(as_account)
    book_registry.set_book_visibility(BOOK, "private", force=True)
    with as_account("tina"):
        assert Path(_play(block_id).path).is_file()
    for name in ("sara", "uma"):
        with as_account(name):
            assert _denied(lambda: _play(block_id)) == 404


# ── the existing animation block ─────────────────────────────


def _animation_block(as_account, url: str, block_id: str = "anim_1") -> None:
    from deeptutor.book.storage import get_book_storage

    with as_account("tina"):
        storage = get_book_storage()
        page = storage.load_page(BOOK, "pg_1")
        page.blocks.append(Block(
            id=block_id, type=BlockType.ANIMATION, status=BlockStatus.READY,
            payload={"render_type": "video", "video_url": url,
                     "artifacts": [{"type": "video", "url": url, "filename": "clip.mp4"}]},
        ))
        storage.save_page(page)


def test_an_older_animation_block_now_plays_for_joined_learners(as_account, teaching_users, tina_book, deliver) -> None:
    with as_account("tina"):
        url = _own_output(f"workspace/chat/math_animator/book-{BOOK}-anim_1/artifacts/clip.mp4", "mp4")
    _animation_block(as_account, url)
    deliver("book", BOOK, "sara")
    with as_account("sara"):
        response = _play("anim_1", name="clip.mp4")
        assert Path(response.path).read_text() == "mp4"
        assert "content-security-policy" not in response.headers
        # The author's outputs URL itself still does not resolve for her.
        from deeptutor.multi_user.paths import get_current_path_service

        assert get_current_path_service().resolve_public_output_path(url[len("/files/outputs/"):]) is None
    with as_account("uma"):
        assert _denied(lambda: _play("anim_1", name="clip.mp4")) == 404


def test_an_animation_block_cannot_point_learners_at_other_author_files(as_account, teaching_users, tina_book, deliver) -> None:
    with as_account("tina"):
        lesson = _lesson_url("private_turn")
        other_turn = _own_output("workspace/chat/math_animator/turn_9/artifacts/clip.mp4", "mp4")
    _animation_block(as_account, lesson, "anim_a")
    _animation_block(as_account, other_turn, "anim_b")
    deliver("book", BOOK, "sara")
    with as_account("sara"):
        assert _denied(lambda: _play("anim_a", name="lesson.html")) == 404
        assert _denied(lambda: _play("anim_b", name="clip.mp4")) == 404


def test_a_new_animation_is_published_with_the_book(as_account, teaching_users, tina_book, deliver) -> None:
    from deeptutor.book.media import publish_animation_artifacts
    from deeptutor.book.storage import get_book_storage
    from deeptutor.multi_user.paths import get_current_path_service

    with as_account("tina"):
        url = _own_output(f"workspace/chat/math_animator/book-{BOOK}-anim_new/artifacts/clip.mp4", "mp4")
        artifacts, media = publish_animation_artifacts(
            get_book_storage(), BOOK, "pg_1", "anim_new",
            [{"type": "video", "url": url, "filename": "clip.mp4"}, {"type": "image", "url": "/elsewhere.png"}],
            get_current_path_service(),
        )
        assert media == ["clip.mp4"]
        assert artifacts[0]["url"] == f"/api/books/{BOOK}/pages/pg_1/blocks/anim_new/media/clip.mp4"
        assert artifacts[1]["url"] == "/elsewhere.png"
        storage = get_book_storage()
        page = storage.load_page(BOOK, "pg_1")
        page.blocks.append(Block(
            id="anim_new", type=BlockType.ANIMATION, status=BlockStatus.READY,
            payload={"video_url": artifacts[0]["url"], "artifacts": artifacts, "media": media},
        ))
        storage.save_page(page)
    deliver("book", BOOK, "sara")
    with as_account("sara"):
        assert Path(_play("anim_new", name="clip.mp4").path).read_text() == "mp4"


# ── an author path's reference-only book ─────────────────────


def test_an_author_paths_learner_plays_it_through_the_path(
    as_account, teaching_users, author_draft, publish_template, teaching_http,
) -> None:
    """The frame loads by URL with the session cookie and ``?mastery_template=``."""
    from deeptutor.api.routers import mastery_path
    from deeptutor.api.routers.outputs import SANDBOXED_LESSON_CSP
    from deeptutor.book import registry as book_registry
    from deeptutor.book.storage import get_book_storage
    from deeptutor.learning.models import TopicSource, TopicSourceKind

    with as_account("tina"):
        storage = get_book_storage()
        storage.save_book(Book(id=BOOK, title="Waves", page_count=1))
        storage.save_spine(Spine(book_id=BOOK, chapters=[Chapter(id="ch_1", title="Waves", page_ids=["pg_1"])]))
        storage.save_page(Page(id="pg_1", book_id=BOOK, chapter_id="ch_1", title="Waves", status=PageStatus.READY))
        assert book_registry.resolve_book_ref(BOOK) is not None
        block_id = _insert(output_url=_lesson_url())["block"]["id"]
    author_draft(sources=(
        TopicSource(id="b0", kind=TopicSourceKind.BOOK, source_id=BOOK, label="Waves", position=0),
    ))
    template_id = publish_template(
        learners=("sara",), book_choices={BOOK: "reference_only"},
    )["template"]["id"]
    with as_account("sara"):
        asyncio.run(mastery_path.list_topics())
    assert book_registry.get_book(BOOK).visibility == "private"

    client = teaching_http.client
    url = f"/api/books/{BOOK}/pages/pg_1/blocks/{block_id}/media/lesson.html"
    played = client.get(f"{url}?mastery_template={template_id}", cookies={"dt_token": "sara"})
    assert played.status_code == 200
    assert played.text == LESSON_HTML
    assert played.headers["content-security-policy"] == SANDBOXED_LESSON_CSP
    client.cookies.clear()
    assert client.get(url, cookies={"dt_token": "sara"}).status_code == 404
    client.cookies.clear()
    assert client.get(f"{url}?mastery_template={template_id}", cookies={"dt_token": "sam"}).status_code == 404
    client.cookies.clear()
    assert client.get(url).status_code == 401
