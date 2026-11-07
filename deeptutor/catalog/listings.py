"""Putting content in the market and reviewing it (platform batch 1 §2, §3).

States: ``draft`` → ``pending`` → ``listed`` (or ``rejected``, with a
reason); ``listed`` ⇄ ``unlisted`` by the author; ``taken_down`` by an
administrator, with a reason. A learner's first listing is reviewed; an
administrator's is not, and once approved an author relists without review.
After an administrator took an item down, it is reviewed again.

Anyone puts their own content forward, and only their own (spec 2026-11-05).
Taking down is the only thing an administrator does to someone else's
listing, besides reviewing it.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from deeptutor.catalog import enrollment, items, taxonomy
from deeptutor.catalog.store import db
from deeptutor.multi_user.models import CurrentUser
from deeptutor.publishing import scope as scopes

logger = logging.getLogger(__name__)

TITLE_MAX = 80
SUMMARY_MAX = 1000
TAG_MAX = 20
TAGS_MAX = 8
REASON_MAX = 500


class ListingError(Exception):
    status_code = 400


class ListingForbidden(ListingError):
    status_code = 403


class ListingNotFound(ListingError):
    status_code = 404


class ListingConflict(ListingError):
    status_code = 409


# ── reading ────────────────────────────────────────────────


def status(kind: str, item_id: str) -> str | None:
    with db.read() as conn:
        if conn is None:
            return None
        row = conn.execute(
            "SELECT status FROM listings WHERE kind = ? AND item_id = ?", (kind, item_id)
        ).fetchone()
    return row["status"] if row is not None else None


def _row(conn, kind: str, item_id: str):
    return conn.execute("SELECT * FROM listings WHERE kind = ? AND item_id = ?", (kind, item_id)).fetchone()


def describe(row, *, learners: int | None = None) -> dict[str, Any]:
    stages = json.loads(row["stages_json"] or "[]")
    subjects = json.loads(row["subjects_json"] or "[]")
    return {
        "kind": row["kind"],
        "id": row["item_id"],
        "owner_id": row["owner_user_id"],
        "status": row["status"],
        "title": row["title"],
        "summary": row["summary"],
        "stages": stages,
        "subjects": subjects,
        "grades": json.loads(_col(row, "grades_json", "[]") or "[]"),
        "difficulty": row["difficulty"],
        "tags": json.loads(row["tags_json"] or "[]"),
        "approved_once": bool(row["approved_once"]),
        "migrated": bool(row["migrated"]),
        "blocked": bool(row["blocked"]),
        "reason": row["reason"],
        "submitted_at": row["submitted_at"],
        "listed_at": row["listed_at"],
        "updated_at": row["updated_at"],
        "needs_classification": not stages or not subjects,
        "learners": learners if learners is not None else enrollment.joined_count(row["kind"], row["item_id"]),
    }


def _col(row, name: str, default: Any = None) -> Any:
    """A column a store made before it existed may lack (read-only reads do not migrate)."""
    return row[name] if name in row.keys() else default


def clean_tags(tags: list[str]) -> list[str]:
    """Trimmed, at most :data:`TAGS_MAX`; one per case-folded spelling, the first spelling kept."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in tags or []:
        tag = " ".join(str(raw).split())
        if not tag:
            continue
        if len(tag) > TAG_MAX:
            raise ListingError(f"A tag has at most {TAG_MAX} characters.")
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
    from deeptutor.catalog.tags import apply_aliases

    out = apply_aliases(out)  # merged tags become the tag they were merged into (batch 2 §8.2)
    if len(out) > TAGS_MAX:
        raise ListingError(f"Add at most {TAGS_MAX} tags.")
    return out


def _admin_ids() -> list[str]:
    from deeptutor.multi_user.accounts import accounts as _accounts

    return [uid for uid, row in _accounts().items() if row["role"] == "admin" and not row["disabled"]]


def _notify(user_ids, kind: str, row, *, reason: str = "") -> None:
    from deeptutor.notifications.service import notify

    link = (
        "/admin/review" if kind == "listing_submitted"
        else f"/market/item?kind={row['kind']}&id={row['item_id']}"
    )
    notify(
        user_ids, kind, {"content": row["kind"], "title": row["title"], "reason": reason},
        link=link, dedupe_key=f"{kind}:{row['kind']}:{row['item_id']}:{int(time.time())}",
    )


def _people_in(kind: str, item_id: str, owner: str) -> set[str]:
    """Everyone in the item now, however they came in (a course: those who joined it)."""
    with db.read() as conn:
        if conn is None:
            return set()
        if kind == scopes.COURSE:
            rows = conn.execute(
                "SELECT DISTINCT user_id FROM enrollments WHERE kind = ? AND item_id = ? AND left_at IS NULL "
                "AND via IN (?, ?)",
                (kind, item_id, *enrollment.DIRECT),
            )
        else:
            rows = conn.execute(
                "SELECT DISTINCT user_id FROM enrollments WHERE kind = ? AND item_id = ? AND left_at IS NULL",
                (kind, item_id),
            )
        return {r[0] for r in rows} - {owner}


def notify_withdrawn(kind: str, item_id: str, how: str, *, title: str = "", owner: str = "") -> None:
    """Tell the people in an item that it was taken down, unlisted or withdrawn (audit fix B §2).

    *how*: ``taken_down`` (an administrator), ``unlisted`` (out of the market,
    still studied), ``author_deleted`` (out of the market because its author's account
    was deleted, still studied; polish batch §3) or ``archived`` (its author archived
    it or made it private).
    Best effort: a notice never makes the change that caused it fail.
    """
    try:
        if not db.path().exists():
            return
        if not title or not owner:
            info = items.info(kind, item_id)
            title = title or (info.title if info else "")
            owner = owner or (info.owner if info else "")
        people = _people_in(kind, item_id, owner)
        if not people:
            return
        from deeptutor.notifications.service import notify

        link = (
            f"/tasks?course={item_id}" if kind == scopes.COURSE
            else f"/market/item?kind={kind}&id={item_id}"
        )
        notify(
            people, "content_withdrawn", {"content": kind, "title": title, "how": how},
            link=link, dedupe_key=f"content_withdrawn:{kind}:{item_id}:{how}:{time.strftime('%Y-%m-%d')}",
        )
    except Exception:
        logger.warning("Could not tell the people in %s %s that it went", kind, item_id, exc_info=True)


def withdrawal(kind: str, item_ids: list[str]) -> dict[str, str]:
    """How each item went, for the people in it (polish batch §1): ``taken_down`` (an
    administrator) or ``unlisted`` (out of the market, still studied); absent otherwise.
    Whether its author archived it is the content's own state."""
    if not item_ids:
        return {}
    with db.read() as conn:
        if conn is None:
            return {}
        marks = ",".join("?" for _ in item_ids)
        rows = conn.execute(
            f"SELECT item_id, status, blocked FROM listings WHERE kind = ? AND item_id IN ({marks})",  # nosec B608
            (kind, *item_ids),
        ).fetchall()
    out: dict[str, str] = {}
    for row in rows:
        if row["blocked"]:
            out[row["item_id"]] = "taken_down"
        elif row["status"] == "unlisted":
            out[row["item_id"]] = "unlisted"
    return out


def _event(conn, kind: str, item_id: str, actor_id: str, action: str, reason: str = "") -> None:
    conn.execute(
        "INSERT INTO listing_events (kind, item_id, actor_id, action, reason, at) VALUES (?, ?, ?, ?, ?, ?)",
        (kind, item_id, actor_id, action, reason, time.time()),
    )


def _after_access_change(kind: str, item_id: str) -> None:
    """Members gain or lose the item: paths hand out or close copies."""
    if kind == scopes.MASTERY_PATH:
        enrollment.reconcile_path(item_id)
    elif kind == scopes.COURSE:
        for template_id in _course_paths(item_id):
            enrollment.reconcile_path(template_id)


def _course_paths(course_id: str) -> list[str]:
    return [t["target_id"] for t in items.course_tasks(course_id) if t["kind"] == "mastery_path" and t["target_id"]]


def _ensure_managed(kind: str, item_id: str, actor_id: str) -> None:
    """A listed item has a stored scope, so its members are its receivers.

    An unmanaged path keeps the learners it was handed to by hand; any other
    unmanaged item gets an empty scope (only its members reach it).
    """
    if scopes.get_scope(kind, item_id) is not None:
        return
    keep: list[str] = []
    if kind == scopes.MASTERY_PATH:
        from deeptutor.teaching.store import TeachingStore

        keep = [a.student_user_id for a in TeachingStore().list_assignments(item_id) if a.status == "active"]
    scopes._store_scope(kind, item_id, scopes.Scope(users=keep), actor_id=actor_id)


# ── the author ─────────────────────────────────────────────


class ListingService:
    def __init__(self, actor: CurrentUser | None = None) -> None:
        from deeptutor.multi_user.context import get_current_user

        self.actor = actor or get_current_user()

    def _role(self) -> str:
        from deeptutor.multi_user.accounts import live_role as _role

        return _role(self.actor)

    def _require_admin(self) -> None:
        if self._role() != "admin":
            raise ListingForbidden("Only administrators review the market.")

    def _own_item(self, kind: str, item_id: str) -> items.ItemInfo:
        from deeptutor.multi_user.ownership import owns

        if kind not in items.KINDS:
            raise ListingNotFound("Not found.")
        info = items.info(kind, item_id)
        if info is None or not owns(info.owner, self.actor):
            raise ListingNotFound("Not found.")
        return info

    def get(self, kind: str, item_id: str) -> dict[str, Any]:
        """The author's view: the listing, or what a new one would start from."""
        info = self._own_item(kind, item_id)
        with db.read() as conn:
            row = _row(conn, kind, item_id) if conn else None
        if row is None:
            return {
                "kind": kind, "id": item_id, "owner_id": info.owner, "status": "none",
                "title": info.title[:TITLE_MAX], "summary": info.description[:SUMMARY_MAX],
                "stages": [], "subjects": [], "grades": [], "difficulty": "", "tags": [], "approved_once": False,
                "migrated": False, "reason": "", "submitted_at": None, "listed_at": None, "updated_at": None,
                "needs_classification": True, "learners": 0, "not_ready": info.not_ready,
            }
        return {**describe(row), "not_ready": info.not_ready}

    def save(
        self, kind: str, item_id: str, *, title: str, summary: str, stages: list[str], subjects: list[str],
        difficulty: str, tags: list[str], grades: list[str] | None = None,
    ) -> dict[str, Any]:
        """The listing's text and classification; checked in full only on submission."""
        info = self._own_item(kind, item_id)
        title = " ".join(str(title or "").split())
        summary = str(summary or "").strip()
        if len(title) > TITLE_MAX:
            raise ListingError(f"The title has at most {TITLE_MAX} characters.")
        if len(summary) > SUMMARY_MAX:
            raise ListingError(f"The introduction has at most {SUMMARY_MAX} characters.")
        with db.read() as conn:
            before = _row(conn, kind, item_id) if conn else None
        try:
            stages, subjects, difficulty = taxonomy.validate(stages, subjects, difficulty, keep=_classification(before))
            grades = taxonomy.validate_grades(grades or [], stages)
        except taxonomy.TaxonomyError as exc:
            raise ListingError(str(exc)) from exc
        tags = clean_tags(tags)
        now = time.time()
        with db.write() as conn:
            row = _row(conn, kind, item_id)
            values = (
                title, summary, json.dumps(stages), json.dumps(subjects), difficulty,
                json.dumps(tags, ensure_ascii=False), now,
            )
            if row is None:
                conn.execute(
                    "INSERT INTO listings (kind, item_id, owner_user_id, status, title, summary, stages_json, "
                    "subjects_json, difficulty, tags_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?)",
                    (kind, item_id, info.owner, *values[:-1], now, now),
                )
            else:
                conn.execute(
                    "UPDATE listings SET title = ?, summary = ?, stages_json = ?, subjects_json = ?, "
                    "difficulty = ?, tags_json = ?, updated_at = ? WHERE kind = ? AND item_id = ?",
                    (*values, kind, item_id),
                )
            conn.execute(
                "UPDATE listings SET grades_json = ? WHERE kind = ? AND item_id = ?",
                (json.dumps(grades), kind, item_id),
            )
        return self.get(kind, item_id)

    def submit(self, kind: str, item_id: str) -> dict[str, Any]:
        """Put forward for review; an administrator's own goes straight to the market."""
        info = self._own_item(kind, item_id)
        if info.not_ready:
            raise ListingError(info.not_ready)
        with db.read() as conn:
            row = _row(conn, kind, item_id) if conn else None
        # Taken down by an administrator: reviewed again, even when its author is one (review M4).
        admin = self._role() == "admin" and not (row is not None and _col(row, "blocked", 0))
        if row is None:
            raise ListingError("Fill in the listing first.")
        if row["status"] not in ("draft", "rejected", "taken_down"):
            raise ListingConflict("This is already in review or in the market.")
        missing = _missing_fields(row)
        if missing:
            raise ListingError(missing)
        _ensure_managed(kind, item_id, self.actor.id)
        now = time.time()
        with db.write() as conn:
            row = _row(conn, kind, item_id)
            if row is None or row["status"] not in ("draft", "rejected", "taken_down"):
                raise ListingConflict("This is already in review or in the market.")
            if admin:
                conn.execute(
                    "UPDATE listings SET status = 'listed', approved_once = 1, blocked = 0, reason = '', "
                    "submitted_at = ?, listed_at = COALESCE(listed_at, ?), updated_at = ? WHERE kind = ? AND item_id = ?",
                    (now, now, now, kind, item_id),
                )
                _event(conn, kind, item_id, self.actor.id, "listed")
            else:
                conn.execute(
                    "UPDATE listings SET status = 'pending', submitted_at = ?, updated_at = ? "
                    "WHERE kind = ? AND item_id = ?",
                    (now, now, kind, item_id),
                )
                _event(conn, kind, item_id, self.actor.id, "submitted")
            row = _row(conn, kind, item_id)
        if admin:
            _after_access_change(kind, item_id)
        else:
            _notify(_admin_ids(), "listing_submitted", row)
        return self.get(kind, item_id)

    def cancel(self, kind: str, item_id: str) -> dict[str, Any]:
        self._own_item(kind, item_id)
        self._move(kind, item_id, ("pending",), "draft", "cancelled")
        return self.get(kind, item_id)

    def unlist(self, kind: str, item_id: str) -> dict[str, Any]:
        """Out of the market; those who joined keep studying it, and are told (audit fix B §2)."""
        self._own_item(kind, item_id)
        row = self._move(kind, item_id, ("listed",), "unlisted", "unlisted")
        notify_withdrawn(kind, item_id, "unlisted", title=row["title"], owner=row["owner_user_id"])
        return self.get(kind, item_id)

    def relist(self, kind: str, item_id: str) -> dict[str, Any]:
        info = self._own_item(kind, item_id)
        if info.not_ready:
            raise ListingError(info.not_ready)
        with db.read() as conn:
            row = _row(conn, kind, item_id) if conn else None
        if row is None or row["status"] != "unlisted":
            raise ListingConflict("Only an item taken out of the market can be put back.")
        if not row["approved_once"]:
            raise ListingConflict("Submit it for review first.")
        self._move(kind, item_id, ("unlisted",), "listed", "relisted")
        return self.get(kind, item_id)

    def _move(self, kind: str, item_id: str, sources: tuple[str, ...], target: str, action: str,
              *, reason: str = "", block: bool = False) -> Any:
        now = time.time()
        with db.write() as conn:
            row = _row(conn, kind, item_id)
            if row is None or row["status"] not in sources:
                raise ListingConflict("This listing changed meanwhile. Reload and try again.")
            extra = ", listed_at = COALESCE(listed_at, ?)" if target == "listed" else ""
            params: list[Any] = [target, reason, now]
            if extra:
                params.append(now)
            conn.execute(
                f"UPDATE listings SET status = ?, reason = ?, updated_at = ?{extra} WHERE kind = ? AND item_id = ?",  # nosec B608
                (*params, kind, item_id),
            )
            if block:
                # In the same write as the state (review M4): never "taken down" yet still open.
                conn.execute("UPDATE listings SET blocked = 1 WHERE kind = ? AND item_id = ?", (kind, item_id))
            _event(conn, kind, item_id, self.actor.id, action, reason)
            return _row(conn, kind, item_id)

    def mine(self) -> list[dict[str, Any]]:
        """The author's listings, with what each needs."""
        from deeptutor.multi_user.ownership import owner_ids

        mine = owner_ids(self.actor)
        with db.read() as conn:
            rows = conn.execute("SELECT * FROM listings ORDER BY updated_at DESC").fetchall() if conn else []
        rows = [r for r in rows if r["owner_user_id"] in mine]
        keys = [(r["kind"], r["item_id"]) for r in rows]
        counts = enrollment.joined_counts(keys)
        recent = enrollment.recent_joins(keys)
        # Polish batch §2: how many came in over the last week, beside how many are in.
        return [
            {**describe(r, learners=counts[(r["kind"], r["item_id"])]), "recent_learners": recent[(r["kind"], r["item_id"])]}
            for r in rows
        ]

    # ── administrators ────────────────────────────────────

    def review_queue(self) -> list[dict[str, Any]]:
        self._require_admin()
        with db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM listings WHERE status = 'pending' ORDER BY submitted_at"
            ).fetchall() if conn else []
        return [_with_author(describe(r, learners=0)) for r in rows]

    def review_item(self, kind: str, item_id: str) -> dict[str, Any]:
        self._require_admin()
        with db.read() as conn:
            row = _row(conn, kind, item_id) if conn else None
        if row is None:
            raise ListingNotFound("Not found.")
        events = []
        with db.read() as conn:
            events = [
                {"action": e["action"], "reason": e["reason"], "at": e["at"], "actor_id": e["actor_id"]}
                for e in conn.execute(
                    "SELECT * FROM listing_events WHERE kind = ? AND item_id = ? ORDER BY at DESC LIMIT 20",
                    (kind, item_id),
                )
            ]
        return {**_with_author(describe(row)), "preview": items.preview(kind, item_id, full=True), "events": events}

    def approve(self, kind: str, item_id: str) -> dict[str, Any]:
        self._require_admin()
        now = time.time()
        with db.write() as conn:
            row = _row(conn, kind, item_id)
            if row is None or row["status"] != "pending":
                raise ListingConflict("This is no longer waiting for review.")
            conn.execute(
                "UPDATE listings SET status = 'listed', approved_once = 1, blocked = 0, reason = '', "
                "listed_at = COALESCE(listed_at, ?), updated_at = ? WHERE kind = ? AND item_id = ?",
                (now, now, kind, item_id),
            )
            _event(conn, kind, item_id, self.actor.id, "approved")
            row = _row(conn, kind, item_id)
        _after_access_change(kind, item_id)
        _notify([row["owner_user_id"]], "listing_approved", row)
        return describe(row)

    def reject(self, kind: str, item_id: str, reason: str) -> dict[str, Any]:
        self._require_admin()
        reason = _reason(reason)
        row = self._move(kind, item_id, ("pending",), "rejected", "rejected", reason=reason)
        _notify([row["owner_user_id"]], "listing_rejected", row, reason=reason)
        return describe(row)

    def take_down(self, kind: str, item_id: str, reason: str) -> dict[str, Any]:
        """Out of the market and out of reach of everyone who joined; their records stay."""
        self._require_admin()
        reason = _reason(reason)
        row = self._move(kind, item_id, ("listed", "unlisted"), "taken_down", "taken_down", reason=reason, block=True)
        _after_access_change(kind, item_id)
        if kind == scopes.MASTERY_PATH:
            from deeptutor.teaching.instances import notify_instance_closed

            notify_instance_closed(item_id)
        from deeptutor.catalog.material_notice import notify_lost

        notify_lost(kind, item_id, owner=row["owner_user_id"], name=row["title"])  # batch 3 §6
        _notify([row["owner_user_id"]], "listing_taken_down", row, reason=reason)
        # Audit fix B: the people in it hear it went (§2); whoever reported it, what was decided (§1).
        notify_withdrawn(kind, item_id, "taken_down", title=row["title"], owner=row["owner_user_id"])
        from deeptutor.catalog.flags import TAKEN_DOWN, resolve

        resolve(kind, item_id, "", TAKEN_DOWN, self.actor.id, title=row["title"])
        return describe(row)

    def relist_handed_over(self, kind: str, item_id: str) -> dict[str, Any]:
        """Back into the market: an item that came to an administrator when its author's
        account was deleted (polish batch §3). Any administrator may; the author's own
        decision to take something out is never overridden here.
        """
        self._require_admin()
        with db.read() as conn:
            row = _row(conn, kind, item_id) if conn else None
        if row is None:
            raise ListingNotFound("Not found.")
        if row["status"] != "unlisted" or author_role(row["owner_user_id"]) != "admin":
            raise ListingConflict("Only content handed over to an administrator can be put back here.")
        if not row["approved_once"]:
            raise ListingConflict("Submit it for review first.")
        info = items.info(kind, item_id)
        if info is None:
            raise ListingNotFound("Not found.")
        if info.not_ready:
            raise ListingError(info.not_ready)
        row = self._move(kind, item_id, ("unlisted",), "listed", "relisted")
        return _with_author(describe(row))

    def classify(
        self, kind: str, item_id: str, *, stages: list[str], subjects: list[str], difficulty: str, tags: list[str],
        grades: list[str] | None = None,
    ) -> dict[str, Any]:
        """An administrator corrects anyone's classification and tags, never the text (batch 2 §8.3)."""
        self._require_admin()
        with db.read() as conn:
            row = _row(conn, kind, item_id) if conn else None
        if row is None:
            raise ListingNotFound("Not found.")
        try:
            stages, subjects, difficulty = taxonomy.validate(stages, subjects, difficulty, keep=_classification(row))
            grades = taxonomy.validate_grades(grades or [], stages)
        except taxonomy.TaxonomyError as exc:
            raise ListingError(str(exc)) from exc
        tags = clean_tags(tags)
        with db.write() as conn:
            conn.execute(
                "UPDATE listings SET stages_json = ?, subjects_json = ?, grades_json = ?, difficulty = ?, tags_json = ?, "
                "updated_at = ? WHERE kind = ? AND item_id = ?",
                (json.dumps(stages), json.dumps(subjects), json.dumps(grades), difficulty,
                 json.dumps(tags, ensure_ascii=False), time.time(), kind, item_id),
            )
            _event(conn, kind, item_id, self.actor.id, "reclassified")
            row = _row(conn, kind, item_id)
        return _with_author(describe(row))

    def listed(self, q: str = "") -> list[dict[str, Any]]:
        """Everything in the market or taken out by its author, for an administrator."""
        self._require_admin()
        with db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM listings WHERE status IN ('listed', 'unlisted') ORDER BY listed_at DESC"
            ).fetchall() if conn else []
        needle = q.strip().casefold()
        if needle:
            rows = [r for r in rows if needle in r["title"].casefold()]
        counts = enrollment.joined_counts([(r["kind"], r["item_id"]) for r in rows])
        return [_with_author(describe(r, learners=counts[(r["kind"], r["item_id"])])) for r in rows[:200]]


def _classification(row) -> list[str]:
    """What a listing already names: kept even if an administrator hid it since."""
    if row is None:
        return []
    return [*json.loads(row["stages_json"] or "[]"), *json.loads(row["subjects_json"] or "[]")]


def _missing_fields(row) -> str:
    if not row["title"]:
        return "Give it a title."
    if not row["summary"]:
        return "Write an introduction."
    if not json.loads(row["stages_json"] or "[]"):
        return "Choose at least one stage."
    if not json.loads(row["subjects_json"] or "[]"):
        return "Choose at least one subject."
    if not row["difficulty"]:
        return "Choose a difficulty."
    return ""


def _reason(reason: str) -> str:
    reason = str(reason or "").strip()
    if not reason:
        raise ListingError("Say why.")
    if len(reason) > REASON_MAX:
        raise ListingError(f"Keep the reason under {REASON_MAX} characters.")
    return reason


def author_name(owner_id: str, accounts: dict[str, dict[str, Any]] | None = None) -> str:
    from deeptutor.multi_user.models import LOCAL_ADMIN_ID

    if owner_id == LOCAL_ADMIN_ID:
        return ""
    if accounts is None:
        from deeptutor.multi_user.accounts import accounts as _accounts

        accounts = _accounts()
    return str((accounts.get(owner_id) or {}).get("username") or "")


def author_role(owner_id: str, accounts: dict[str, dict[str, Any]] | None = None) -> str:
    from deeptutor.multi_user.models import LOCAL_ADMIN_ID, role_label

    if owner_id == LOCAL_ADMIN_ID:
        return "admin"
    if accounts is None:
        from deeptutor.multi_user.accounts import accounts as _accounts

        accounts = _accounts()
    account = accounts.get(owner_id)
    # Spec 2026-11-05: shown as ``admin`` or ``learner``.
    return role_label(account["role"]) if account else ""


def _with_author(entry: dict[str, Any]) -> dict[str, Any]:
    return {**entry, "author": author_name(entry["owner_id"]), "author_role": author_role(entry["owner_id"])}


# ── hooks from the content ─────────────────────────────────


def content_closed(kind: str, item_id: str) -> None:
    """The author archived it or made it private: it leaves the market (§2), and the
    people in it are told (audit fix B §2)."""
    try:
        if not db.path().exists():
            return
        with db.write() as conn:
            row = _row(conn, kind, item_id)
            if row is not None and row["status"] in ("listed", "pending"):
                target = "unlisted" if row["status"] == "listed" else "draft"
                conn.execute(
                    "UPDATE listings SET status = ?, updated_at = ? WHERE kind = ? AND item_id = ?",
                    (target, time.time(), kind, item_id),
                )
                _event(conn, kind, item_id, row["owner_user_id"], "closed")
    except Exception:
        logger.exception("Could not take %s %s out of the market", kind, item_id)
    notify_withdrawn(kind, item_id, "archived")


def forget(kind: str, item_id: str) -> None:
    """The item was deleted."""
    try:
        if db.path().exists():
            with db.write() as conn:
                conn.execute("DELETE FROM listings WHERE kind = ? AND item_id = ?", (kind, item_id))
        enrollment.forget_item(kind, item_id)
        from deeptutor.catalog.announcements import forget_item
        from deeptutor.catalog.reviews import forget_item as forget_reviews

        forget_item(kind, item_id)
        forget_reviews(kind, item_id)
    except Exception:
        logger.exception("Could not forget the listing of %s %s", kind, item_id)


def withdraw_departed(user_id: str) -> int:
    """The author's account was deleted (polish batch §3): what they had in the market
    leaves it, and the people in it are told. Pending requests go back to drafts.

    Taken out, not taken down: those who joined keep studying, as when an author
    takes something out themselves. Runs before :func:`follow_owners` hands the
    listings to the successor; an administrator can put them back later.
    """
    if not db.path().exists():
        return 0
    now = time.time()
    withdrawn = []
    with db.write() as conn:
        rows = conn.execute(
            "SELECT * FROM listings WHERE owner_user_id = ? AND status IN ('listed', 'pending')", (user_id,)
        ).fetchall()
        for row in rows:
            target = "unlisted" if row["status"] == "listed" else "draft"
            conn.execute(
                "UPDATE listings SET status = ?, updated_at = ? WHERE kind = ? AND item_id = ?",
                (target, now, row["kind"], row["item_id"]),
            )
            _event(conn, row["kind"], row["item_id"], "system", "author_deleted")
            if target == "unlisted":
                withdrawn.append(row)
    for row in withdrawn:
        notify_withdrawn(row["kind"], row["item_id"], "author_deleted", title=row["title"], owner=user_id)
    return len(rows)


def follow_owners(from_user_id: str) -> int:
    """Content handed over when its author left (roadmap 6.1): each listing follows its item."""
    if not db.path().exists():
        return 0
    with db.read() as conn:
        rows = conn.execute(
            "SELECT kind, item_id FROM listings WHERE owner_user_id = ?", (from_user_id,)
        ).fetchall()
    moved = 0
    for row in rows:
        info = items.info(row["kind"], row["item_id"])
        if info is None or info.owner == from_user_id:
            continue
        with db.write() as conn:
            conn.execute(
                "UPDATE listings SET owner_user_id = ? WHERE kind = ? AND item_id = ?",
                (info.owner, row["kind"], row["item_id"]),
            )
        moved += 1
    return moved


__all__ = [
    "ListingConflict", "ListingError", "ListingForbidden", "ListingNotFound", "ListingService", "author_name",
    "author_role", "clean_tags", "content_closed", "describe", "follow_owners", "forget", "notify_withdrawn", "status",
    "withdraw_departed", "withdrawal",
]
