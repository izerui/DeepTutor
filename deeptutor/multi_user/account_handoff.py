"""Hand a deleted account's data to a successor, and retry what failed (B2-9).

Account deletion removes the account first and then runs these best-effort
stages, each in its own transaction:

``knowledge_bases``
    :func:`deeptutor.knowledge.registry.transfer_ownership`
``books``
    :func:`deeptutor.book.registry.transfer_book_ownership`
``teacher_paths`` / ``student_assignments`` / ``path_materials``
    :func:`deeptutor.teaching.service.handle_account_deleted`

A pending record (in the KB registry, see ``account_handoffs`` there) is
written before the stages run and narrowed to the failed stages afterwards,
so neither a failure nor a crash leaves entities owned by a deleted id
without a way back. :func:`retry_handoff` re-runs only the recorded stages;
success clears the record. Every stage is idempotent: each selects what the
deleted id still owns (or what is still active), so a second run finds
nothing left to move.

All of this is blocking (SQLite writers with process locks and long busy
timeouts): async callers run it in a worker thread.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
import logging
import time

from deeptutor.knowledge.registry import AccountHandoff

logger = logging.getLogger(__name__)

STAGES: tuple[str, ...] = (
    "knowledge_bases",
    "books",
    "teacher_paths",
    "student_assignments",
    "path_materials",
)
_TEACHING_STAGES = frozenset({"teacher_paths", "student_assignments", "path_materials"})
#: Records one startup pass retries at most.
STARTUP_RETRY_LIMIT = 50
#: Seconds after which a startup pass starts no further record.
STARTUP_RETRY_BUDGET = 300.0


class HandoffBlocked(RuntimeError):
    """The deleted id belongs to an existing account again; nothing ran."""

    def __init__(self, record: AccountHandoff) -> None:
        super().__init__(record.last_error)
        self.record = record


def _ordered(stages: Sequence[str]) -> list[str]:
    wanted = set(stages)
    return [stage for stage in STAGES if stage in wanted]


def run_stages(
    user_id: str, successor_id: str, stages: Sequence[str], *, quiet: bool = False
) -> tuple[list[str], str]:
    """Run the requested stages; return (failed stages, last error).

    Never raises for a stage failure: each one is logged and reported. With
    *quiet* (retries) a failure's traceback goes to DEBUG only; the caller
    logs one summary line per attempt.
    """
    from deeptutor.book import registry as book_registry
    from deeptutor.knowledge import registry as kb_registry

    stages = _ordered(stages)
    failed: list[str] = []
    errors: list[str] = []

    def failure(message: str) -> None:
        if quiet:
            logger.debug(message, user_id, exc_info=True)
        else:
            logger.exception(message, user_id)

    if "knowledge_bases" in stages:
        try:
            moved = kb_registry.transfer_ownership(user_id, successor_id)
        except Exception as exc:
            failure("Could not transfer knowledge bases of deleted user '%s'")
            failed.append("knowledge_bases")
            errors.append(f"knowledge_bases: {exc!r}")
        else:
            if moved:
                logger.info(
                    "Transferred %d knowledge base(s) of '%s' to %s",
                    len(moved), user_id, successor_id,
                )

    if "books" in stages:
        try:
            moved_books = book_registry.transfer_book_ownership(user_id, successor_id)
        except Exception as exc:
            failure("Could not transfer books of deleted user '%s'")
            failed.append("books")
            errors.append(f"books: {exc!r}")
        else:
            if moved_books:
                logger.info(
                    "Transferred %d book(s) of '%s' to %s",
                    len(moved_books), user_id, successor_id,
                )

    teaching = [stage for stage in stages if stage in _TEACHING_STAGES]
    if teaching:
        try:
            from deeptutor.teaching import service as teaching_service

            # R6 / D17: the deleted teacher's paths (and their reference
            # ownership) go to the same successor as their libraries and
            # books; students keep learning. A deleted student's assignments
            # are revoked (waking their live subscriptions) and the materials
            # nobody needs any more are released.
            teaching_errors: list[str] = []
            teaching_failed = teaching_service.handle_account_deleted(
                user_id, successor_id=successor_id, stages=tuple(teaching),
                errors=teaching_errors, quiet=quiet,
            ) or []
        except Exception as exc:
            failure("Could not hand over teacher paths of deleted user '%s'")
            teaching_failed = teaching
            errors.append(f"teaching: {exc!r}")
        else:
            errors.extend(teaching_errors)
        failed.extend(stage for stage in teaching if stage in set(teaching_failed))

    return _ordered(failed), "; ".join(errors)


def hand_over_deleted_account(user_id: str, successor_id: str, *, username: str = "") -> list[str]:
    """Run every stage for a just-deleted account; return the failed ones.

    The pending record is written first. If even that write fails the stages
    still run, and a failure is recorded afterwards when the store is back.
    """
    from deeptutor.knowledge import registry as kb_registry

    try:
        kb_registry.begin_account_handoff(user_id, username, successor_id, STAGES)
    except Exception:
        logger.exception(
            "Could not record the pending hand-over of deleted user '%s'; continuing", user_id
        )
    failed, error = run_stages(user_id, successor_id, STAGES)
    _finish(user_id, username, successor_id, 0, failed, error)
    return failed


def _finish(
    user_id: str,
    username: str,
    successor_id: str,
    expected_attempts: int,
    failed: list[str],
    error: str,
) -> AccountHandoff | None:
    from deeptutor.knowledge import registry as kb_registry

    try:
        return kb_registry.finish_account_handoff(
            user_id,
            username=username,
            successor_id=successor_id,
            expected_attempts=expected_attempts,
            stages=failed,
            error=error,
        )
    except Exception:
        # The last resort is the log: say exactly what an admin must redo.
        logger.exception(
            "Could not store the hand-over outcome of deleted user '%s' (successor %s); "
            "stages still to run: %s",
            user_id, successor_id, failed or "none",
        )
        return None


def list_pending_handoffs() -> list[AccountHandoff]:
    from deeptutor.knowledge import registry as kb_registry

    return kb_registry.list_account_handoffs()


def _account_exists(user_id: str) -> bool:
    from deeptutor.multi_user.identity import get_user_by_id

    return get_user_by_id(user_id) is not None


def _live_successor(successor_id: str, fallback: str) -> str:
    from deeptutor.multi_user.models import LOCAL_ADMIN_ID

    if successor_id == LOCAL_ADMIN_ID or _account_exists(successor_id):
        return successor_id
    return fallback or LOCAL_ADMIN_ID


def retry_handoff(user_id: str, *, fallback_successor: str = "") -> AccountHandoff | None:
    """Re-run the recorded failed stages of one deleted account.

    Returns the remaining record, or ``None`` once it is cleared. Raises
    :class:`LookupError` when there is no record. When the id belongs to an
    existing account again, nothing runs: the record is marked ``blocked``
    (kept and listed for the admin, skipped at startup) and
    :class:`HandoffBlocked` is raised; a later admin retry re-checks it. If
    the recorded successor no longer exists, the hand-over goes to
    ``fallback_successor`` (the retrying admin) or the local admin instead.
    Logs exactly one line per attempt (tracebacks at DEBUG).
    """
    from deeptutor.knowledge import registry as kb_registry

    record = kb_registry.get_account_handoff(user_id)
    if record is None:
        raise LookupError(f"No pending hand-over for {user_id}")
    if _account_exists(user_id):
        blocked = kb_registry.block_account_handoff(
            user_id,
            "Blocked: this user id belongs to an existing account, so its data was not "
            "handed over. Retry after that account is gone.",
        ) or record
        logger.warning(
            "Pending account hand-over for '%s' (%s) is blocked: the id belongs to an "
            "existing account",
            record.username or user_id, user_id,
        )
        raise HandoffBlocked(blocked)
    successor = _live_successor(record.successor_id, fallback_successor)
    if successor != record.successor_id:
        logger.warning(
            "Successor %s of deleted user '%s' is gone; handing over to %s instead",
            record.successor_id, user_id, successor,
        )
    failed, error = run_stages(user_id, successor, record.stages, quiet=True)
    remaining = _finish(user_id, record.username, successor, record.attempts, failed, error)
    if failed:
        logger.warning(
            "Pending account hand-over retry for '%s' (%s), attempt %d: still failing: %s (%s)",
            record.username or user_id, user_id, record.attempts + 1, ", ".join(failed),
            error[:300],
        )
    else:
        logger.info(
            "Pending account hand-over retry for '%s' (%s), attempt %d: completed",
            record.username or user_id, user_id, record.attempts + 1,
        )
    return remaining


def retry_pending_handoffs(
    *, limit: int = STARTUP_RETRY_LIMIT, budget: float = STARTUP_RETRY_BUDGET
) -> dict[str, int]:
    """Startup pass: retry each pending hand-over once, oldest first.

    Blocked records are skipped. Bounded by *limit* records and by *budget*
    seconds (no new record starts after it). Blocking: run it off the loop.
    """
    records = list_pending_handoffs()
    waiting = [record for record in records if record.status != "blocked"]
    deadline = time.monotonic() + budget
    retried = cleared = blocked_now = 0
    for record in waiting[: max(limit, 0)]:
        if time.monotonic() >= deadline:
            break
        retried += 1
        try:
            if retry_handoff(record.user_id) is None:
                cleared += 1
        except LookupError:
            cleared += 1  # Another worker finished it meanwhile.
        except HandoffBlocked:
            blocked_now += 1  # Logged once and parked; startup skips it from now on.
        except Exception as exc:
            logger.warning(
                "Pending account hand-over retry for %s could not run: %r", record.user_id, exc
            )
            logger.debug("Hand-over retry failure", exc_info=True)
    return {
        "retried": retried,
        "cleared": cleared,
        "pending": len(waiting) - cleared - blocked_now,
        "blocked": len(records) - len(waiting) + blocked_now,
    }


def start_background_retry() -> asyncio.Task[dict[str, int] | None]:
    """Schedule the startup pass on a worker thread; serving does not wait."""

    async def run() -> dict[str, int] | None:
        try:
            report = await asyncio.to_thread(retry_pending_handoffs)
        except Exception:
            logger.exception("Pending account hand-over retry failed")
            return None
        if report["retried"] or report["blocked"]:
            logger.info("Pending account hand-overs: %s", report)
        return report

    return asyncio.create_task(run(), name="pending-account-handoffs")
