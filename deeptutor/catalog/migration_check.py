"""Check the market migration on a COPY of real data before going live.

    DEEPTUTOR_HOME=/path/to/a/copy python -m deeptutor.catalog.migration_check --on-a-copy

For every active account it records what they can open — practice sets,
Mastery Paths, tasks, courses, books, knowledge bases — and their daily AI
limit. Then it runs the migration (content
published to everyone goes to the market) and records it all again. Whatever someone lost is listed; losses the design expects (content
published to everyone that the person never started) are counted apart.
Exit code 0: nothing else was lost and no learner's AI limit changed.

It writes to the data it is pointed at, so it refuses to run without
``--on-a-copy``.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def _actor(user_id: str, account: dict[str, Any]):
    from deeptutor.multi_user.models import CurrentUser
    from deeptutor.multi_user.paths import admin_scope, scope_for_user

    admin = account["role"] == "admin"
    return CurrentUser(user_id, account["username"], account["role"],
                       admin_scope() if admin else scope_for_user(user_id, is_admin=False))


def _reach(user_id: str, account: dict[str, Any]) -> dict[str, Any]:
    """What one account can open now."""
    from deeptutor.book import registry as book_registry
    from deeptutor.knowledge import registry as kb_registry
    from deeptutor.multi_user.context import reset_current_user, set_current_user
    from deeptutor.practice_sets.service import PracticeSetService
    from deeptutor.tasks.courses import assigned_courses
    from deeptutor.tasks.service import StudentTasks
    from deeptutor.teaching.store import TeachingStore

    actor = _actor(user_id, account)
    token = set_current_user(actor)
    try:
        from deeptutor.publishing.scope import reconcile_student

        # As opening the learning list would: stale path assignments are settled first.
        reconcile_student(user_id)
        teaching = TeachingStore()
        out: dict[str, Any] = {
            "practice_set": sorted(s["id"] for s in PracticeSetService(actor).assigned()),
            "mastery_path": sorted(
                a.template_id for a in teaching.assignments_for_student(user_id)
                if a.status == "active" and (t := teaching.get_template(a.template_id)) and t.status == "published"
            ),
            # Practice sets and paths shown as tasks stop being tasks by design (batch 2 §5).
            "task": sorted(t["key"] for t in StudentTasks(actor).assigned() if ":" not in t["key"]),
            "course": sorted(c["id"] for c in assigned_courses(actor)),
            "book": sorted(
                r.book_id for r in book_registry.list_book_records()
                if r.created_by != user_id and book_registry.can_read_book(r, actor)
            ),
            "knowledge_base": sorted(
                r.kb_id for r in kb_registry.list_records()
                if r.created_by != user_id and kb_registry.can_read(r, actor)
            ),
        }
        from deeptutor.multi_user.roles import effective_ai_daily_tokens

        out["ai_daily_tokens"] = effective_ai_daily_tokens(user_id)
        return out
    finally:
        reset_current_user(token)


def _everyone_items() -> dict[tuple[str, str], set[str]]:
    """Items published to everyone before the migration -> who had started on them.

    They go to the market; only who started joins. Losing one is by design only
    for someone who never started it.
    """
    from deeptutor.catalog import items
    from deeptutor.catalog.migration import _candidates, _starters
    from deeptutor.multi_user.accounts import accounts as _accounts

    accounts = _accounts()
    out = {}
    for kind, item_id in _candidates(accounts):
        owner = items.owner(kind, item_id) or ""
        out[(kind, item_id)] = _starters(kind, item_id, owner, accounts)
    return out


def _safe_reach(user_id: str, account: dict[str, Any]) -> dict[str, Any]:
    try:
        return _reach(user_id, account)
    except Exception as exc:  # one broken account must not stop the check
        return {"error": f"{type(exc).__name__}: {exc}"}


def run() -> dict[str, Any]:
    from deeptutor.catalog import migration
    from deeptutor.multi_user.accounts import accounts as _accounts

    accounts = {u: a for u, a in _accounts().items() if not a["disabled"] and a["role"] != "admin"}
    everyone = _everyone_items()
    before = {u: _safe_reach(u, a) for u, a in sorted(accounts.items())}
    migration.run()
    after = {u: _safe_reach(u, a) for u, a in sorted(accounts.items())}
    people = []
    unexpected_total = 0
    for user_id, account in sorted(accounts.items(), key=lambda kv: kv[1]["username"]):
        lost: dict[str, list[str]] = {}
        expected: dict[str, list[str]] = {}
        errors = [f"{when}: {snap['error']}" for when, snap in (("before", before[user_id]), ("after", after[user_id]))
                  if "error" in snap]
        for kind in ("practice_set", "mastery_path", "task", "course", "book", "knowledge_base"):
            if errors:
                break
            for item_id in sorted(set(before[user_id][kind]) - set(after[user_id][kind])):
                started = everyone.get((kind, item_id))
                by_design = started is not None and user_id not in started
                bucket = expected if by_design else lost
                bucket.setdefault(kind, []).append(item_id)
        changed = {} if errors else {
            key: {"before": before[user_id].get(key), "after": after[user_id].get(key)}
            for key in ("ai_daily_tokens",) if before[user_id].get(key) != after[user_id].get(key)
        }
        unexpected = sum(len(v) for v in lost.values()) + len(changed) + len(errors)
        unexpected_total += unexpected
        people.append({
            "username": account["username"], "role": account["role"], "lost": lost,
            "not_started_everyone_items": expected, "abilities_changed": changed, "errors": errors,
            "ok": unexpected == 0,
        })
    return {"migrated": migration.done(), "people": people, "problems": unexpected_total}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--on-a-copy", action="store_true", help="confirm DEEPTUTOR_HOME points at a copy")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = parser.parse_args(argv)
    if not args.on_a_copy:
        print("This changes the data it runs on. Point DEEPTUTOR_HOME at a copy and add --on-a-copy.")
        return 2
    result = run()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"migrated: {result['migrated']}")
        for person in result["people"]:
            line = f"{'OK ' if person['ok'] else '!! '}{person['username']} ({person['role']})"
            if person["lost"]:
                line += f"  LOST {person['lost']}"
            if person["abilities_changed"]:
                line += f"  ABILITIES {person['abilities_changed']}"
            if person["errors"]:
                line += f"  ERROR {person['errors']}"
            if person["not_started_everyone_items"]:
                count = sum(len(v) for v in person["not_started_everyone_items"].values())
                line += f"  (by design: {count} item(s) published to everyone, never started, now in the market)"
            print(line)
        print(f"problems: {result['problems']}")
    return 0 if result["problems"] == 0 and result["migrated"] else 1


if __name__ == "__main__":
    sys.exit(main())
