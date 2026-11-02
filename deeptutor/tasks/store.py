"""System-level persistence for tasks and homework (spec 2026-11-03 batch 1).

Lives in ``<SYSTEM_ROOT>/tasks.sqlite3``:

* ``tasks``: what a teacher (or administrator) asks of students — study a
  Mastery Path, answer a practice set, read chapters of a book, hand in
  homework, or a plain instruction;
* ``task_completions``: once a student completed a task (or a practice set /
  Mastery Path shown as one), it stays completed (R24);
* ``task_submissions``: a student's latest homework for a task, its AI
  suggestion (teacher-only), and the teacher's grade.

Homework is handed to its teacher, so it lives here; the student's chats,
notes and own content never do. Files are kept under
``<SYSTEM_ROOT>/task_files/<task>/<student>/``.
"""

from __future__ import annotations

from pathlib import Path

from deeptutor.multi_user.system_db import SystemDb

db = SystemDb(
    "tasks.sqlite3",
    """\
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('mastery_path', 'practice_set', 'book', 'homework', 'note')),
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    target_id TEXT NOT NULL DEFAULT '',
    settings_json TEXT NOT NULL DEFAULT '{}',
    due_at REAL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'archived')),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    published_at REAL
);
CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks(owner_user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_target ON tasks(kind, target_id);

CREATE TABLE IF NOT EXISTS task_completions (
    -- A task id, or ``practice_set:<id>`` / ``mastery_path:<template id>``.
    task_key TEXT NOT NULL,
    student_id TEXT NOT NULL,
    completed_at REAL NOT NULL,
    late INTEGER NOT NULL DEFAULT 0,
    self_reported INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (task_key, student_id)
);
CREATE INDEX IF NOT EXISTS idx_task_completions_student ON task_completions(student_id);

CREATE TABLE IF NOT EXISTS task_submissions (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    student_id TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    files_json TEXT NOT NULL DEFAULT '[]',
    attempt INTEGER NOT NULL DEFAULT 1,
    submitted_at REAL NOT NULL,
    late INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted', 'returned', 'graded')),
    ai_score REAL,
    ai_reason TEXT NOT NULL DEFAULT '',
    score REAL,
    comment TEXT NOT NULL DEFAULT '',
    return_reason TEXT NOT NULL DEFAULT '',
    graded_by TEXT NOT NULL DEFAULT '',
    graded_at REAL,
    PRIMARY KEY (task_id, student_id)
);
CREATE INDEX IF NOT EXISTS idx_task_submissions_student ON task_submissions(student_id);
""",
    "Task",
)


def files_root() -> Path:
    from deeptutor.multi_user import paths

    base = db.path_override.parent if db.path_override is not None else paths.SYSTEM_ROOT
    return base / "task_files"


__all__ = ["db", "files_root"]
