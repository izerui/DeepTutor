"""Explicit visibility authority, stored beside each registry's own records.

Call only with the registry's write transaction. Migration checks this evidence
under the same writer as publication; default registration is not user intent.
The schema is mandatory because book sharing attaches two registry_meta tables.
"""

import sqlite3
from typing import Literal

Schema = Literal["main", "books"]
_PREFIX = "visibility_intent_v1:"


def _table(schema: Schema) -> str:
    if schema not in ("main", "books"):
        raise ValueError("Unsupported registry schema")
    return f"{schema}.registry_meta"


def explicit_choice(conn: sqlite3.Connection, resource_id: str, *, schema: Schema) -> bool:
    return conn.execute(
        f"SELECT 1 FROM {_table(schema)} WHERE key = ?", (_PREFIX + resource_id,),
    ).fetchone() is not None


def record_choice(
    conn: sqlite3.Connection, resource_id: str, visibility: str, *, schema: Schema,
) -> None:
    if visibility not in ("public", "private"):
        raise ValueError("Invalid visibility intent")
    conn.execute(
        f"INSERT INTO {_table(schema)} (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (_PREFIX + resource_id, visibility),
    )
