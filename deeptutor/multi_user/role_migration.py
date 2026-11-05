"""Startup move of the retired ``learner`` preset into grants (spec 2026-10-29 P2).

Accounts that used the retired ``learner`` preset get its learning
restrictions written into their grant, so the restrictions no longer depend
on the preset field. Roles are not rewritten: stored ``teacher``,
``student`` and ``user`` records all read as learners (spec 2026-11-05 §3).
The migration is safe to run on every start.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def run_role_migration() -> dict[str, Any]:
    from . import identity
    from .grants import learner_grant, load_grant, save_grant

    report: dict[str, Any] = {"learner_policies": []}
    if not identity.USERS_FILE.exists():
        return report
    with identity._USERS_WRITE_LOCK:
        users = identity.load_users()
        for username, record in users.items():
            if str(record.get("preset") or "") != "learner" or record.get("role") == "admin":
                continue
            user_id = str(record.get("id") or "")
            grant = load_grant(user_id)
            if grant.get("learning_policy") is None:
                grant["learning_policy"] = learner_grant(user_id)["learning_policy"]
                save_grant(user_id, grant)
                report["learner_policies"].append(username)
    if report["learner_policies"]:
        logger.info("Role migration: %s", json.dumps(report, ensure_ascii=False))
    return report


__all__ = ["run_role_migration"]
