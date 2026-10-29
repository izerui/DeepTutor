"""One-time move to three roles (spec 2026-10-29 P2, D1).

``user`` accounts become ``student`` without losing anything they could do:
they get an explicit student policy that keeps personal Mastery Paths and
private knowledge bases (students default to no private knowledge bases).
Accounts that used the retired ``learner`` preset get its learning
restrictions written into their grant, so the restrictions no longer depend
on the preset field. ``users.json`` is backed up before the first change; the
migration is safe to run on every start.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import shutil
from typing import Any

logger = logging.getLogger(__name__)

PRESERVED_STUDENT_POLICY = {"allow_personal_mastery": True, "allow_private_kb": True}


def _backup_users_file() -> None:
    from . import identity, paths

    if not identity.USERS_FILE.exists():
        return
    backups = paths.SYSTEM_ROOT / "auth" / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    shutil.copy2(identity.USERS_FILE, backups / f"users-{stamp}.json")


def run_role_migration() -> dict[str, Any]:
    from . import identity
    from .grants import learner_grant, load_grant, save_grant

    report: dict[str, Any] = {"students": [], "learner_policies": []}
    if not identity.USERS_FILE.exists():
        return report
    with identity._USERS_WRITE_LOCK:
        users = identity.load_users()
        pending = [
            (name, record) for name, record in users.items()
            if record.get("role") == "user"
            or (str(record.get("preset") or "") == "learner" and record.get("role") != "admin")
        ]
        if not pending:
            return report
        needs_role_change = any(record.get("role") == "user" for _name, record in pending)
        if needs_role_change:
            _backup_users_file()
        for username, record in pending:
            user_id = str(record.get("id") or "")
            grant = load_grant(user_id)
            changed = False
            if str(record.get("preset") or "") == "learner" and grant.get("learning_policy") is None:
                grant["learning_policy"] = learner_grant(user_id)["learning_policy"]
                report["learner_policies"].append(username)
                changed = True
            if record.get("role") == "user":
                if grant.get("student_policy") is None:
                    grant["student_policy"] = dict(PRESERVED_STUDENT_POLICY)
                    changed = True
                record["role"] = "student"
                report["students"].append(username)
            if changed:
                save_grant(user_id, grant)
        if report["students"]:
            identity._write_users(users)
    if report["students"] or report["learner_policies"]:
        logger.info("Role migration: %s", json.dumps(report, ensure_ascii=False))
    return report


__all__ = ["PRESERVED_STUDENT_POLICY", "run_role_migration"]
