"""System-level persistence for tasks and courses (spec 2026-11-03 batch 1).

Lives in ``<SYSTEM_ROOT>/tasks.sqlite3``:

* ``tasks``: what an author (or administrator) asks of learners — study a
  Mastery Path, answer a practice set, read chapters of a book, or a plain
  instruction;
* ``task_completions``: once a learner completed a task (or a practice set /
  Mastery Path shown as one), it stays completed (R24);
* ``courses`` / ``course_items``: tasks in an order.
"""

from __future__ import annotations


from deeptutor.multi_user.system_db import SystemDb

db = SystemDb(
    "tasks.sqlite3",
    """\
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('mastery_path', 'practice_set', 'book', 'note')),
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    target_id TEXT NOT NULL DEFAULT '',
    settings_json TEXT NOT NULL DEFAULT '{}',
    due_at REAL,
    -- Before it, learners see only "opens on …" (spec 2026-11-03 batch 2 §6).
    open_at REAL,
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

-- Author courses: tasks in an order, with a description (spec 2026-11-03 batch 2 §5).
CREATE TABLE IF NOT EXISTS courses (
    id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published', 'archived')),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    published_at REAL
);
CREATE TABLE IF NOT EXISTS course_items (
    course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    PRIMARY KEY (course_id, task_id)
);
""",
    "Task",
    columns={"tasks": {"open_at": "REAL"}},
)


__all__ = ["db"]
