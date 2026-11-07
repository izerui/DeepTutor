"""Browsing the market (platform batch 1 §5.1).

Several values in one condition widen ("or"); conditions narrow each other
("and"). Each option's count applies every other condition and leaves its
own condition open. A parent subject matches its children. Items without
a stage or subject are found by keyword and in "all", never under a stage
or subject.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from deeptutor.catalog import enrollment, items, taxonomy
from deeptutor.catalog.listings import ListingNotFound, author_name, author_role, describe
from deeptutor.catalog.store import db
from deeptutor.multi_user.models import CurrentUser

PAGE_SIZE = 24
TAG_FACET_LIMIT = 200
SORTS = ("newest", "popular", "rating")


@dataclass
class Filters:
    q: str = ""
    stages: list[str] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    grades: list[str] = field(default_factory=list)
    difficulties: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)
    sort: str = "newest"
    page: int = 1
    #: Only the viewer's favorites (follow-ups §1).
    favorites: bool = False


def _empty_courses(course_ids: list[str]) -> set[str]:
    """Listed courses holding no item, in one read."""
    if not course_ids:
        return set()
    from deeptutor.tasks.store import db as task_db

    with task_db.read() as conn:
        if conn is None:
            return set(course_ids)
        held = {r[0] for r in conn.execute("SELECT DISTINCT course_id FROM course_items")}
    return set(course_ids) - held


def _entries(accounts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    with db.read() as conn:
        rows = conn.execute("SELECT * FROM listings WHERE status = 'listed'").fetchall() if conn else []
    from deeptutor.catalog.reviews import ratings

    counts = enrollment.joined_counts()
    rated = ratings()
    empty = _empty_courses([row["item_id"] for row in rows if row["kind"] == "course"])
    out = []
    for row in rows:
        if row["kind"] == "course" and row["item_id"] in empty:
            continue  # nothing to learn in it (its author removed every item)
        entry = describe(row, learners=counts.get((row["kind"], row["item_id"]), 0))
        entry["rating"], entry["rating_count"] = rated.get((row["kind"], row["item_id"]), (None, 0))
        entry["author"] = author_name(row["owner_user_id"], accounts)
        entry["author_role"] = author_role(row["owner_user_id"], accounts)
        entry["_subjects"] = {a for s in entry["subjects"] for a in taxonomy.ancestors(s)}
        entry["_tags"] = {t.casefold() for t in entry["tags"]}
        out.append(entry)
    return out


def _keyword(entry: dict[str, Any], q: str) -> bool:
    needle = q.casefold()
    return any(
        needle in text.casefold()
        for text in (entry["title"], entry["summary"], entry["author"], *entry["tags"])
    )


_DIMENSIONS = ("stage", "subject", "grade", "kind", "difficulty", "tag", "author")


def _passes(entry: dict[str, Any], f: Filters, skip: str = "") -> bool:
    if skip != "stage" and f.stages and not set(f.stages) & set(entry["stages"]):
        return False
    if skip != "subject" and f.subjects and not set(f.subjects) & entry["_subjects"]:
        return False
    if skip != "grade" and f.grades and not set(f.grades) & set(entry["grades"]):
        return False
    if skip != "kind" and f.kinds and entry["kind"] not in f.kinds:
        return False
    if skip != "difficulty" and f.difficulties and entry["difficulty"] not in f.difficulties:
        return False
    if skip != "tag" and f.tags and not {t.casefold() for t in f.tags} & entry["_tags"]:
        return False
    if skip != "author" and f.authors and entry["owner_id"] not in f.authors:
        return False
    return True


def _facets(entries: list[dict[str, Any]], f: Filters) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for dimension in _DIMENSIONS:
        open_entries = [e for e in entries if _passes(e, f, skip=dimension)]
        if dimension == "stage":
            out["stages"] = dict(Counter(s for e in open_entries for s in e["stages"]))
        elif dimension == "subject":
            out["subjects"] = dict(Counter(s for e in open_entries for s in e["_subjects"]))
        elif dimension == "grade":
            out["grades"] = dict(Counter(g for e in open_entries for g in e["grades"]))
        elif dimension == "kind":
            out["kinds"] = dict(Counter(e["kind"] for e in open_entries))
        elif dimension == "difficulty":
            out["difficulties"] = dict(Counter(e["difficulty"] for e in open_entries if e["difficulty"]))
        elif dimension == "tag":
            spelled: dict[str, Counter] = {}
            for e in open_entries:
                for tag in e["tags"]:
                    spelled.setdefault(tag.casefold(), Counter())[tag] += 1
            ranked = sorted(spelled.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))
            out["tags"] = [
                {"tag": spellings.most_common(1)[0][0], "count": sum(spellings.values())}
                for _key, spellings in ranked[:TAG_FACET_LIMIT]
            ]
        else:
            authors: dict[str, dict[str, Any]] = {}
            for e in open_entries:
                found = authors.setdefault(
                    e["owner_id"], {"id": e["owner_id"], "name": e["author"], "role": e["author_role"], "count": 0}
                )
                found["count"] += 1
            out["authors"] = sorted(authors.values(), key=lambda a: (-a["count"], a["name"]))
    return out


def _public(
    entry: dict[str, Any], joined: dict[tuple[str, str], str], favorites: set[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    clean = {k: v for k, v in entry.items() if not k.startswith("_")}
    clean["joined"] = joined.get((entry["kind"], entry["id"]))
    clean["favorited"] = (entry["kind"], entry["id"]) in (favorites or set())
    return clean


def search(viewer: CurrentUser, f: Filters) -> dict[str, Any]:
    from deeptutor.multi_user.accounts import accounts as _accounts

    from deeptutor.catalog.reviews import favorites_of

    accounts = _accounts()
    entries = _entries(accounts)
    favorites = favorites_of(viewer.id)
    if f.favorites:
        entries = [e for e in entries if (e["kind"], e["id"]) in favorites]
    if f.q.strip():
        entries = [e for e in entries if _keyword(e, f.q.strip())]
    facets = _facets(entries, f)
    results = [e for e in entries if _passes(e, f)]
    if f.sort == "popular":
        results.sort(key=lambda e: (-e["learners"], -(e["listed_at"] or 0)))
    elif f.sort == "rating":
        # Unrated last; then by average, by how many rated, by newest.
        results.sort(key=lambda e: (e["rating"] is None, -(e["rating"] or 0), -e["rating_count"], -(e["listed_at"] or 0)))
    else:
        results.sort(key=lambda e: -(e["listed_at"] or 0))
    page = max(1, int(f.page or 1))
    start = (page - 1) * PAGE_SIZE
    joined = enrollment.joined_by(viewer.id)
    return {
        "total": len(results),
        "page": page,
        "page_size": PAGE_SIZE,
        "items": [_public(e, joined, favorites) for e in results[start:start + PAGE_SIZE]],
        "facets": facets,
    }


def recent(viewer: CurrentUser, *, since: float, limit: int) -> list[dict[str, Any]]:
    """Newly listed, for the home page (§5.3)."""
    result = search(viewer, Filters(sort="newest"))
    return [e for e in result["items"] if (e["listed_at"] or 0) >= since][:limit]


def _study_target(kind: str, item_id: str) -> dict[str, Any]:
    """Where "go study" leads, for the client to build its link."""
    from deeptutor.publishing import scope as scopes

    target: dict[str, Any] = {"kind": kind, "id": item_id}
    if kind == scopes.MASTERY_PATH:
        from deeptutor.teaching.instances import instance_path_id

        target["path_id"] = instance_path_id(item_id)
    elif kind == scopes.KNOWLEDGE_BASE:
        from deeptutor.knowledge import registry as kb_registry

        record = kb_registry.get_kb(item_id)
        target["name"] = record.name if record else ""
    return target


def detail(viewer: CurrentUser, kind: str, item_id: str) -> dict[str, Any]:
    """The introduction page. Listed items for everyone; others for their author, administrators and members."""
    from deeptutor.multi_user.accounts import accounts as _accounts, live_role as _role
    from deeptutor.multi_user.ownership import owns

    with db.read() as conn:
        row = conn.execute(
            "SELECT * FROM listings WHERE kind = ? AND item_id = ?", (kind, item_id)
        ).fetchone() if conn else None
    info = items.info(kind, item_id) if row is not None else None
    if row is None or info is None:
        raise ListingNotFound("Not found.")
    joined = enrollment.joined_by(viewer.id)
    mine = owns(row["owner_user_id"], viewer)
    member = (kind, item_id) in joined
    if row["status"] != "listed" and not (mine or _role(viewer) == "admin" or member):
        raise ListingNotFound("Not found.")
    from deeptutor.catalog.reviews import favorites_of, ratings

    accounts = _accounts()
    entry = describe(row)
    entry["rating"], entry["rating_count"] = ratings().get((kind, item_id), (None, 0))
    entry["favorited"] = (kind, item_id) in favorites_of(viewer.id)
    entry["author"] = author_name(row["owner_user_id"], accounts)
    entry["author_role"] = author_role(row["owner_user_id"], accounts)
    blocked = bool(row["blocked"] if "blocked" in row.keys() else 0)
    admin = _role(viewer) == "admin"
    from deeptutor.catalog.flags import reported_by

    return {
        **entry,
        "joined": joined.get((kind, item_id)),
        "mine": mine,
        # Taken down by an administrator, or archived / made private by its author:
        # nobody studies it now (audit fix B §2), whatever their records say.
        "closed": info.closed or blocked,
        "unavailable": "taken_down" if blocked else ("archived" if info.closed else None),
        "can_join": row["status"] == "listed" and not mine and not info.closed,
        # Audit fix B §1: anyone but its author reports it, once while the report is open.
        "can_report": not mine and not admin,
        "reported": "" in reported_by(viewer.id, kind, item_id),
        # A taken-down item shows its contents to its author and administrators only (review L1).
        "preview": items.preview(kind, item_id) if not blocked or mine or admin else {},
        "study": _study_target(kind, item_id),
    }


def author_page(viewer: CurrentUser, author_id: str) -> dict[str, Any]:
    from deeptutor.multi_user.accounts import accounts as _accounts

    accounts = _accounts()
    entries = [e for e in _entries(accounts) if e["owner_id"] == author_id]
    name = author_name(author_id, accounts)
    if not entries:
        # Review L1: an author page exists for people with something in the market only.
        raise ListingNotFound("Not found.")
    joined = enrollment.joined_by(viewer.id)
    entries.sort(key=lambda e: -(e["listed_at"] or 0))
    # Polish batch §2: who came in over the last week, per item and in all (each person once).
    recent = enrollment.recent_joins([(e["kind"], e["id"]) for e in entries])
    for e in entries:
        e["recent_learners"] = recent[(e["kind"], e["id"])]
    rated = [e for e in entries if e["rating"] is not None and e["rating_count"]]
    rating_count = sum(e["rating_count"] for e in rated)
    # Spec 2026-11-06 §2: the average over rated items, weighted by how many rated each.
    rating = round(sum(e["rating"] * e["rating_count"] for e in rated) / rating_count, 2) if rating_count else None
    return {
        "id": author_id,
        "name": name,
        "role": author_role(author_id, accounts),
        "learners": sum(e["learners"] for e in entries),
        "recent_learners": _recent_people(author_id, [(e["kind"], e["id"]) for e in entries]),
        "item_count": len(entries),
        "rating": rating,
        "rating_count": rating_count,
        "items": [_public(e, joined) for e in entries],
    }


def _recent_people(author_id: str, keys: list[tuple[str, str]]) -> int:
    """Distinct people who came into any of *keys* over the last week."""
    import time

    if not keys:
        return 0
    since = time.time() - enrollment.RECENT_DAYS * 86400
    wanted = set(keys)
    with db.read() as conn:
        if conn is None:
            return 0
        rows = conn.execute(
            "SELECT kind, item_id, user_id FROM enrollments WHERE left_at IS NULL AND joined_at >= ?", (since,)
        ).fetchall()
    return len({r["user_id"] for r in rows if (r["kind"], r["item_id"]) in wanted and r["user_id"] != author_id})


FEATURED_LIMIT = 8
FEATURED_MIN_RATINGS = 3


def featured(viewer: CurrentUser) -> dict[str, Any]:
    """Two rows above the market list (spec 2026-11-06 §3): most learners, best rated."""
    from deeptutor.multi_user.accounts import accounts as _accounts

    from deeptutor.catalog.reviews import favorites_of

    entries = _entries(_accounts())
    popular = sorted(entries, key=lambda e: (-e["learners"], -(e["listed_at"] or 0)))[:FEATURED_LIMIT]
    rated = [e for e in entries if e["rating"] is not None and e["rating_count"] >= FEATURED_MIN_RATINGS]
    rated.sort(key=lambda e: (-e["rating"], -e["rating_count"], -(e["listed_at"] or 0)))
    joined = enrollment.joined_by(viewer.id)
    favorites = favorites_of(viewer.id)
    return {
        "popular": [_public(e, joined, favorites) for e in popular],
        "top_rated": [_public(e, joined, favorites) for e in rated[:FEATURED_LIMIT]],
    }


def parse_list(value: str | None) -> list[str]:
    return [part for part in (value or "").split(",") if part][:50]


__all__ = ["Filters", "PAGE_SIZE", "SORTS", "author_page", "detail", "featured", "parse_list", "recent", "search"]
