"""Tags across the market, and merging them (platform batch 2 §8.2).

Merging A into B rewrites every listing's A as B (one per spelling) and
remembers it: a later A becomes B as it is saved. Undoing a merge only stops
that rewriting; listings already changed keep B.
"""

from __future__ import annotations

import json
import time
from typing import Any

from deeptutor.catalog.store import db


class TagError(ValueError):
    pass


def aliases() -> dict[str, str]:
    """Case-folded merged tag -> the tag it became."""
    with db.read() as conn:
        if conn is None:
            return {}
        return {r["from_key"]: r["to_tag"] for r in conn.execute("SELECT from_key, to_tag FROM tag_aliases")}


def apply_aliases(tags: list[str]) -> list[str]:
    merged = aliases()
    if not merged:
        return tags
    out: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        tag = merged.get(tag.casefold(), tag)
        if tag.casefold() not in seen:
            seen.add(tag.casefold())
            out.append(tag)
    return out


def list_tags() -> dict[str, Any]:
    """Every tag on a listing with how many use it, and the merges in force."""
    with db.read() as conn:
        rows = conn.execute("SELECT tags_json FROM listings").fetchall() if conn else []
        merges = [
            {"from": r["from_tag"], "to": r["to_tag"], "at": r["created_at"]}
            for r in conn.execute("SELECT * FROM tag_aliases ORDER BY created_at DESC")
        ] if conn else []
    spellings: dict[str, dict[str, int]] = {}
    for row in rows:
        for tag in json.loads(row["tags_json"] or "[]"):
            spellings.setdefault(tag.casefold(), {}).setdefault(tag, 0)
            spellings[tag.casefold()][tag] += 1
    tags = [
        {"tag": max(found, key=lambda s: found[s]), "count": sum(found.values())}
        for found in spellings.values()
    ]
    tags.sort(key=lambda t: (-t["count"], t["tag"].casefold()))
    return {"tags": tags, "merges": merges}


def merge(from_tag: str, to_tag: str) -> dict[str, Any]:
    from deeptutor.catalog.listings import TAG_MAX

    source = " ".join(str(from_tag or "").split())
    target = " ".join(str(to_tag or "").split())
    if not source or not target:
        raise TagError("Name both tags.")
    if len(target) > TAG_MAX:
        raise TagError(f"A tag has at most {TAG_MAX} characters.")
    key = source.casefold()
    if key == target.casefold():
        raise TagError("Choose two different tags.")
    now = time.time()
    with db.write() as conn:
        if conn.execute("SELECT 1 FROM tag_aliases WHERE from_key = ?", (target.casefold(),)).fetchone():
            raise TagError("That tag was itself merged into another one.")
        conn.execute(
            "INSERT INTO tag_aliases (from_key, from_tag, to_tag, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(from_key) DO UPDATE SET to_tag = excluded.to_tag, created_at = excluded.created_at",
            (key, source, target, now),
        )
        # Earlier merges into the source now lead to the target (compared case-folded, not SQL lower()).
        for alias in conn.execute("SELECT from_key, to_tag FROM tag_aliases WHERE from_key != ?", (key,)).fetchall():
            if alias["to_tag"].casefold() == key:
                conn.execute("UPDATE tag_aliases SET to_tag = ? WHERE from_key = ?", (target, alias["from_key"]))
        for row in conn.execute("SELECT kind, item_id, tags_json FROM listings").fetchall():
            tags = json.loads(row["tags_json"] or "[]")
            if not any(t.casefold() == key for t in tags):
                continue
            out: list[str] = []
            seen: set[str] = set()
            for tag in tags:
                tag = target if tag.casefold() == key else tag
                if tag.casefold() not in seen:
                    seen.add(tag.casefold())
                    out.append(tag)
            conn.execute(
                "UPDATE listings SET tags_json = ? WHERE kind = ? AND item_id = ?",
                (json.dumps(out, ensure_ascii=False), row["kind"], row["item_id"]),
            )
    return list_tags()


def unmerge(from_tag: str) -> dict[str, Any]:
    with db.write() as conn:
        conn.execute("DELETE FROM tag_aliases WHERE from_key = ?", (str(from_tag or "").strip().casefold(),))
    return list_tags()


__all__ = ["TagError", "aliases", "apply_aliases", "list_tags", "merge", "unmerge"]
