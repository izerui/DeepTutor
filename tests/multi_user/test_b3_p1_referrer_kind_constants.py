"""Cross-module referrer kinds must agree (account transfer relies on them)."""

from __future__ import annotations

from deeptutor.book import registry as books
from deeptutor.knowledge import registry as kbs
from deeptutor.teaching import kb as teaching_kb


def test_kb_side_referrer_kinds_match_their_owning_modules():
    assert kbs.BOOK_REFERRER_KIND == books.KB_REFERRER_KIND
    assert kbs.MASTERY_TEMPLATE_REFERRER_KIND == teaching_kb.REFERRER_KIND
