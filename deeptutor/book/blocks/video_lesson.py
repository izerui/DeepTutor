"""Video lesson block – a chat-generated video lesson, published with the book.

Spec 2026-11-20 §6. The lesson is generated in chat (``video_lesson``
capability) and inserted from there (``POST /api/books/insert-video-lesson``),
which copies its page into the book. There is nothing to generate here: a
regenerate keeps the published lesson, and an empty block (for example a type
change) explains where lessons come from.
"""

from __future__ import annotations

from typing import Any

from ..media import published_names
from ..models import BlockType, SourceAnchor
from .base import BlockContext, BlockGenerator, GenerationFailure

INSERT_FROM_CHAT = (
    "Video lessons are added from a chat: generate one with Video Lesson, "
    "then choose “Add to book”."
)


class VideoLessonGenerator(BlockGenerator):
    block_type = BlockType.VIDEO_LESSON

    async def _generate(
        self, ctx: BlockContext
    ) -> tuple[dict[str, Any], list[SourceAnchor], dict[str, Any]]:
        if not published_names(ctx.block):
            raise GenerationFailure(INSERT_FROM_CHAT)
        return dict(ctx.block.payload), list(ctx.block.source_anchors), {}


__all__ = ["INSERT_FROM_CHAT", "VideoLessonGenerator"]
