"""Audit fix A: what the AI relays and what learners read names the author, never "your teacher"."""

from __future__ import annotations

import ast
import inspect
import re

import pytest

TEACHER = re.compile(r"\bteachers?\b", re.IGNORECASE)


def test_relayed_messages_name_the_author():
    from deeptutor.capabilities.mastery.mode import AUTHOR_OUTLINE_LOCKED
    from deeptutor.multi_user.retrieval_scope import AUTHOR_PATH_RETRIEVAL_DENIED
    from deeptutor.teaching.instances import (
        INSTANCE_UNAVAILABLE,
        MATERIALS_UNAVAILABLE,
        OUTLINE_LOCKED,
    )

    for message in (
        AUTHOR_OUTLINE_LOCKED,
        AUTHOR_PATH_RETRIEVAL_DENIED,
        INSTANCE_UNAVAILABLE,
        MATERIALS_UNAVAILABLE,
        OUTLINE_LOCKED,
    ):
        assert not TEACHER.search(message), message
        assert "author" in message, message
    # No feedback channel to the author exists: the AI must not promise one.
    assert "send" not in AUTHOR_OUTLINE_LOCKED.lower()
    assert "feedback" not in OUTLINE_LOCKED.lower()


def _string_literals(module) -> list[str]:
    tree = ast.parse(inspect.getsource(module))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]


@pytest.mark.parametrize(
    "module_name",
    [
        "deeptutor.capabilities.mastery.mode",
        "deeptutor.learning.lesson_evidence",
        "deeptutor.multi_user.retrieval_scope",
        "deeptutor.teaching.changes",
        "deeptutor.teaching.snapshot",
    ],
)
def test_no_model_or_learner_facing_text_says_teacher(module_name):
    import importlib

    module = importlib.import_module(module_name)
    offending = [
        text for text in _string_literals(module)
        # Log lines (``%s`` formats) are for operators, not learners or the model.
        if " " in text and TEACHER.search(text) and "%s" not in text
    ]
    assert offending == []


def test_lesson_document_gone_asks_the_author():
    from deeptutor.learning import lesson_evidence

    texts = [t for t in _string_literals(lesson_evidence) if "no longer available" in t]
    assert texts and all("author of this path" in t for t in texts)
