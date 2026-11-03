"""Teacher comments on a student's work (spec 2026-11-03 batch 2 §3).

Lives in ``<SYSTEM_ROOT>/comments.sqlite3``. A comment is between one
teacher and one student: only the author of the commented content writes
it, and only that student and that teacher read it. Homework has its own
grading comment (batch 1); here:

* ``practice_submission`` — ``<set id>:<submission id>``, by the set's author;
* ``path_point`` — ``<template id>:<knowledge point id>``, by the path's author,
  for a student the path was assigned to.

One comment per teacher, target and student; writing again replaces it.
A new comment notifies the student; an edit does not.
"""

from __future__ import annotations

import time
from typing import Any
import uuid

from deeptutor.multi_user.context import get_current_user
from deeptutor.multi_user.models import CurrentUser
from deeptutor.multi_user.system_db import SystemDb

TEXT_MAX = 5000
KINDS = ("practice_submission", "path_point")

db = SystemDb(
    "comments.sqlite3",
    """\
CREATE TABLE IF NOT EXISTS comments (
    id TEXT PRIMARY KEY,
    author_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    target_kind TEXT NOT NULL CHECK (target_kind IN ('practice_submission', 'path_point')),
    target_ref TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (author_id, student_id, target_kind, target_ref)
);
CREATE INDEX IF NOT EXISTS idx_comments_student ON comments(student_id, target_kind);
""",
    "Comment",
)


class CommentError(Exception):
    status = 400


class CommentNotFound(CommentError):
    status = 404


def _subject(kind: str, ref: str) -> str:
    """The commented item's id (set or template), before the colon."""
    return ref.split(":", 1)[0]


def _check_author(kind: str, ref: str, student_id: str, author: CurrentUser) -> tuple[str, str]:
    """The author may comment here; returns (title, link) for the notification."""
    if kind not in KINDS or ":" not in ref:
        raise CommentNotFound("Nothing to comment on.")
    subject, _, detail = ref.partition(":")
    if kind == "practice_submission":
        from deeptutor.practice_sets import store as practice_store

        with practice_store.read() as conn:
            row = conn.execute(
                "SELECT p.title, p.owner_user_id FROM practice_submissions s JOIN practice_sets p "
                "ON p.id = s.set_id WHERE s.id = ? AND s.set_id = ? AND s.student_id = ?",
                (detail, subject, student_id),
            ).fetchone() if conn else None
        if row is None or row["owner_user_id"] != author.id:
            raise CommentNotFound("Only the set's author comments on its answers.")
        return row["title"], f"/practice-sets?set={subject}"
    from deeptutor.teaching.store import TeachingStore

    teaching = TeachingStore()
    template = teaching.get_template(subject)
    if template is None or template.owner_user_id != author.id or teaching.get_assignment(subject, student_id) is None:
        raise CommentNotFound("Only the path's author comments on its students.")
    return template.title, f"/learning/mastery/assigned_{subject}"


def write(kind: str, ref: str, student_id: str, text: str, author: CurrentUser | None = None) -> dict[str, Any]:
    author = author or get_current_user()
    text = str(text or "").strip()
    if not text or len(text) > TEXT_MAX:
        raise CommentError(f"A comment must contain 1 to {TEXT_MAX} characters.")
    title, link = _check_author(kind, ref, student_id, author)
    now = time.time()
    with db.write() as conn:
        existing = conn.execute(
            "SELECT id FROM comments WHERE author_id = ? AND student_id = ? AND target_kind = ? AND target_ref = ?",
            (author.id, student_id, kind, ref),
        ).fetchone()
        if existing:
            conn.execute("UPDATE comments SET text = ?, updated_at = ? WHERE id = ?", (text, now, existing["id"]))
            comment_id = existing["id"]
        else:
            comment_id = f"cmt_{uuid.uuid4().hex[:12]}"
            conn.execute(
                "INSERT INTO comments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (comment_id, author.id, student_id, kind, ref, text, now, now),
            )
    if not existing:
        from deeptutor.notifications.service import notify

        notify([student_id], "teacher_comment", {"teacher": author.username, "title": title},
               link=link, dedupe_key=f"teacher_comment:{comment_id}")
    return get(comment_id, author)


def get(comment_id: str, viewer: CurrentUser | None = None) -> dict[str, Any]:
    viewer = viewer or get_current_user()
    with db.read() as conn:
        row = conn.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone() if conn else None
    if row is None or viewer.id not in (row["author_id"], row["student_id"]):
        raise CommentNotFound("Comment not found.")
    return _view(row)


def _view(row, names: dict[str, str] | None = None) -> dict[str, Any]:
    if names is None:
        from deeptutor.classes.service import _accounts

        names = {uid: a["username"] for uid, a in _accounts().items()}
    return {
        "id": row["id"],
        "author": names.get(row["author_id"], ""),  # "" once the author's account is gone
        "student_id": row["student_id"],
        "kind": row["target_kind"],
        "ref": row["target_ref"],
        "text": row["text"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def delete(comment_id: str, author: CurrentUser | None = None) -> None:
    author = author or get_current_user()
    with db.write() as conn:
        if not conn.execute(
            "DELETE FROM comments WHERE id = ? AND author_id = ?", (comment_id, author.id)
        ).rowcount:
            raise CommentNotFound("Comment not found.")


def for_student(kind: str, subject: str, viewer: CurrentUser | None = None) -> list[dict[str, Any]]:
    """The viewer's comments on one set or path, as the student."""
    viewer = viewer or get_current_user()
    with db.read() as conn:
        rows = conn.execute(
            "SELECT * FROM comments WHERE student_id = ? AND target_kind = ? AND target_ref LIKE ? "
            "ORDER BY created_at",
            (viewer.id, kind, f"{subject}:%"),
        ).fetchall() if conn else []
    names = None
    return [_view(row, names) for row in rows]


def written(kind: str, subject: str, student_id: str, viewer: CurrentUser | None = None) -> list[dict[str, Any]]:
    """What the viewer (the author) wrote to one student on one set or path."""
    viewer = viewer or get_current_user()
    with db.read() as conn:
        rows = conn.execute(
            "SELECT * FROM comments WHERE author_id = ? AND student_id = ? AND target_kind = ? AND target_ref LIKE ?",
            (viewer.id, student_id, kind, f"{subject}:%"),
        ).fetchall() if conn else []
    return [_view(row) for row in rows]


def forget_student(user_id: str) -> None:
    """Hand-over stage ``comments``: a deleted student's comments go (§8)."""
    if not db.path().exists():
        return
    with db.write() as conn:
        conn.execute("DELETE FROM comments WHERE student_id = ?", (user_id,))


__all__ = ["CommentError", "CommentNotFound", "delete", "for_student", "forget_student", "get", "write", "written"]
