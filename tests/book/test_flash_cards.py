from __future__ import annotations

import asyncio

import pytest

from deeptutor.book.blocks import flash_cards
from deeptutor.book.blocks.base import BlockContext
from deeptutor.book.models import Block, BlockStatus, BlockType, Chapter, Page


def _context() -> BlockContext:
    chapter = Chapter(id="ch-cards", title="Atomic structure", summary="Electron shells")
    block = Block(type=BlockType.FLASH_CARDS, params={"count": 3})
    return BlockContext(
        book_id="book-cards",
        chapter=chapter,
        page=Page(id="page-cards", book_id="book-cards", chapter_id=chapter.id),
        block=block,
        language="zh",
        rag_enabled=False,
    )


def test_flash_cards_accept_question_answer_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_llm_json(**_kwargs: object) -> dict[str, object]:
        return {
            "cards": [
                {
                    "question": "电子层数决定什么？",
                    "answer": "元素所在的周期。",
                    "tip": "层数定周期",
                }
            ]
        }

    monkeypatch.setattr(flash_cards, "llm_json", fake_llm_json)

    result = asyncio.run(flash_cards.FlashCardsGenerator().generate(_context()))

    assert result.status == BlockStatus.READY
    assert result.payload["cards"] == [
        {
            "front": "电子层数决定什么？",
            "back": "元素所在的周期。",
            "hint": "层数定周期",
        }
    ]
