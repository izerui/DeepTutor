"""Assessment values shared by producers, persistence, and the HTTP API."""

from typing import Literal, get_args

AssessmentSource = Literal[
    "deep_question", "mastery_path", "immersive_reading", "book", "partner_chat", "import",
    # Spec 2026-10-29 §8.3: answers to a teacher's practice set.
    "practice_set",
]
QuestionOriginType = Literal["conversation", "external_import", "document_analysis", "practice_set"]
AssessmentType = Literal["quiz", "focus_check", "qualitative", "review"]
AssessmentResult = Literal["correct", "incorrect", "partial", "ungraded", "voided"]

ASSESSMENT_SOURCES = frozenset(get_args(AssessmentSource))
QUESTION_ORIGIN_TYPES = frozenset(get_args(QuestionOriginType))
ASSESSMENT_TYPES = frozenset(get_args(AssessmentType))
ASSESSMENT_RESULTS = frozenset(get_args(AssessmentResult))
