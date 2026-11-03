"""System-level persistence for the learning market (platform batch 1 §8).

Lives in ``<SYSTEM_ROOT>/catalog.sqlite3``:

* ``listings``: one row per item put forward for the market, with its
  state, classification and tags;
* ``listing_events``: every state change, who made it and why;
* ``enrollments``: who joined what. One row per way in (``self``,
  ``migrated``, ``course:<id>``); leaving stamps ``left_at`` and keeps the row;
* ``catalog_meta``: one-off markers (the 9.1 migration).
"""

from __future__ import annotations

from deeptutor.multi_user.system_db import SystemDb

STATES = ("draft", "pending", "rejected", "listed", "unlisted", "taken_down")

db = SystemDb(
    "catalog.sqlite3",
    """\
CREATE TABLE IF NOT EXISTS listings (
    kind TEXT NOT NULL,
    item_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'pending', 'rejected', 'listed', 'unlisted', 'taken_down')),
    title TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    stages_json TEXT NOT NULL DEFAULT '[]',
    subjects_json TEXT NOT NULL DEFAULT '[]',
    difficulty TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    -- Once approved (or listed by an administrator), relisting needs no review.
    approved_once INTEGER NOT NULL DEFAULT 0,
    migrated INTEGER NOT NULL DEFAULT 0,
    -- Taken down by an administrator: nobody reaches it until approved again.
    blocked INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    submitted_at REAL,
    listed_at REAL,
    PRIMARY KEY (kind, item_id)
);
CREATE INDEX IF NOT EXISTS idx_listings_status ON listings(status, listed_at DESC);
CREATE INDEX IF NOT EXISTS idx_listings_owner ON listings(owner_user_id);

CREATE TABLE IF NOT EXISTS listing_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    item_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_listing_events_item ON listing_events(kind, item_id, at DESC);

CREATE TABLE IF NOT EXISTS enrollments (
    kind TEXT NOT NULL,
    item_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    via TEXT NOT NULL,
    joined_at REAL NOT NULL,
    left_at REAL,
    PRIMARY KEY (kind, item_id, user_id, via)
);
CREATE INDEX IF NOT EXISTS idx_enrollments_item ON enrollments(kind, item_id);
CREATE INDEX IF NOT EXISTS idx_enrollments_user ON enrollments(user_id);

-- Administrators' changes to the built-in classification, and their own entries (batch 2 §8.1).
CREATE TABLE IF NOT EXISTS taxonomy_nodes (
    node_id TEXT PRIMARY KEY,
    -- '' for a stage; the parent's id otherwise. Only read for administrators' own entries.
    parent TEXT NOT NULL DEFAULT '',
    name_zh TEXT NOT NULL DEFAULT '',
    name_en TEXT NOT NULL DEFAULT '',
    hidden INTEGER NOT NULL DEFAULT 0,
    position REAL,
    custom INTEGER NOT NULL DEFAULT 0
);

-- A merged tag and the one it became (batch 2 §8.2), keyed by its case-folded spelling.
CREATE TABLE IF NOT EXISTS tag_aliases (
    from_key TEXT PRIMARY KEY,
    from_tag TEXT NOT NULL,
    to_tag TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS catalog_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
""",
    "Catalog",
)

__all__ = ["STATES", "db"]
