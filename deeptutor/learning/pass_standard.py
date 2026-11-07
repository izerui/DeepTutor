"""How strict a Mastery Path's scored gate is (local extension, 2026-10-31).

A path chooses one of three standards. They keep the scoring formula of
``mastery.compute_mastery`` (recency-weighted accuracy over the last five
answers, capped at 0.5 after one answer and 0.8 after two) and move only the
bar a MEMORY / PROCEDURE objective must reach:

* ``strict``   0.9 — three right answers in a row; after a mistake, five more.
                     The original behaviour and the default of every path.
* ``standard`` 0.8 — two right answers; after a mistake, three more.
* ``relaxed``  0.6 — two right answers; after a mistake, about two more.

CONCEPT / DESIGN objectives keep their qualitative pass, but the standard
sets what the AI judge asks of an explanation (2026-11-03): see
:func:`explanation_expectation`.
A learner sets the standard of their own path; a learner's copy of an author
path takes the author's, published with the revision, and cannot change it.
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


#: What an explanation must show to be judged correct, per standard (2026-11-03).
#: ``strict`` is the wording every path used before standards reached explanations.
EXPLANATION_EXPECTATIONS: dict[str, str] = {
    "relaxed": (
        "The core idea of the objective, stated correctly in the learner's own words; imprecise wording "
        "and missing secondary points are acceptable. Do not infer mastery from acknowledgements or requests."
    ),
    "standard": (
        "The main points of the objective, mostly complete and without clear mistakes. "
        "Do not infer mastery from acknowledgements or requests."
    ),
    "strict": (
        "A complete, accurate explanation of the objective and its relationships; "
        "do not infer mastery from acknowledgements or requests."
    ),
}


def explanation_expectation(standard: str | None) -> str:
    """What the judge expects of an explanation under *standard*; unknown values read as strict."""
    return EXPLANATION_EXPECTATIONS.get(str(standard or ""), EXPLANATION_EXPECTATIONS[DEFAULT_STANDARD])


def threshold(standard: str | None) -> float:
    """The scored mastery bar of *standard*; unknown values read as strict."""
    return THRESHOLDS.get(str(standard or ""), THRESHOLDS[DEFAULT_STANDARD])


__all__ = [
    "DEFAULT_STANDARD", "EXPLANATION_EXPECTATIONS", "PassStandard", "THRESHOLDS", "explanation_expectation",
    "threshold",
]
