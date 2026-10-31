"""How strict a Mastery Path's scored gate is (local extension, 2026-10-31).

A path chooses one of three standards. They keep the scoring formula of
``mastery.compute_mastery`` (recency-weighted accuracy over the last five
answers, capped at 0.5 after one answer and 0.8 after two) and move only the
bar a MEMORY / PROCEDURE objective must reach:

* ``strict``   0.9 — three right answers in a row; after a mistake, five more.
                     The original behaviour and the default of every path.
* ``standard`` 0.8 — two right answers; after a mistake, three more.
* ``relaxed``  0.6 — two right answers; after a mistake, about two more.

CONCEPT / DESIGN objectives keep their qualitative pass and are unaffected.
A learner sets the standard of their own path; a student's copy of a teacher
path takes the teacher's, published with the revision, and cannot change it.
"""

from __future__ import annotations

from typing import Literal

PassStandard = Literal["relaxed", "standard", "strict"]

DEFAULT_STANDARD: PassStandard = "strict"

THRESHOLDS: dict[str, float] = {
    "relaxed": 0.6,
    "standard": 0.8,
    "strict": 0.9,
}


def threshold(standard: str | None) -> float:
    """The scored mastery bar of *standard*; unknown values read as strict."""
    return THRESHOLDS.get(str(standard or ""), THRESHOLDS[DEFAULT_STANDARD])


__all__ = ["DEFAULT_STANDARD", "PassStandard", "THRESHOLDS", "threshold"]
