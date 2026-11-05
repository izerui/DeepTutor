"""Students' daily AI limit (spec 2026-11-03 batch 3 §8).

Counted in tokens from the account's own usage ledger (``usage_ledger``),
from local midnight. Only students are limited. Calls made on a teacher's
behalf — grading a practice set's answers — run under :func:`exempt` and
are recorded but not counted.

A turn that was admitted runs to its end: only its first call is checked.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing, contextmanager
from contextvars import ContextVar
from datetime import datetime
import json
from pathlib import Path
import sqlite3

EXEMPT_PREFIX = "exempt:"
LIMIT_MESSAGE = "Today's AI usage is used up. Come back tomorrow."

_exempt: ContextVar[bool] = ContextVar("ai_usage_exempt", default=False)


from deeptutor.services.llm.exceptions import LLMError


class AiLimitReached(LLMError):
    """An ``LLMError`` so error mapping passes it on as itself, never as a vague API error."""

    def __init__(self) -> None:
        super().__init__(LIMIT_MESSAGE)

    def __str__(self) -> str:
        # The plain sentence, translated by the client; no provider prefix.
        return LIMIT_MESSAGE


@contextmanager
def exempt() -> Iterator[None]:
    """Calls made inside are not counted against the student's limit."""
    token = _exempt.set(True)
    try:
        yield
    finally:
        _exempt.reset(token)


def is_exempt() -> bool:
    return _exempt.get()


# ── the school default ─────────────────────────────────────


def _settings_file() -> Path:
    from deeptutor.multi_user import paths

    return paths.SYSTEM_ROOT / "auth" / "ai_usage.json"


def school_default_daily_tokens() -> int:
    """``0`` (no limit) unless an administrator set one."""
    try:
        value = json.loads(_settings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    tokens = value.get("default_daily_tokens") if isinstance(value, dict) else None
    return tokens if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0 else 0


def set_school_default_daily_tokens(tokens: int) -> int:
    from deeptutor.multi_user.roles import AI_DAILY_TOKENS_MAX
    from deeptutor.utils.secret_files import ensure_private_directory

    if isinstance(tokens, bool) or not isinstance(tokens, int) or not 0 <= tokens <= AI_DAILY_TOKENS_MAX:
        raise ValueError("Choose a daily limit between 0 (no limit) and 100000000 tokens.")
    path = _settings_file()
    ensure_private_directory(path.parent)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"default_daily_tokens": tokens}), encoding="utf-8")
    temporary.replace(path)
    return tokens


# ── counting ───────────────────────────────────────────────


def day_start(now: datetime | None = None) -> float:
    now = now or datetime.now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def used_today(path: Path) -> int:
    """Tokens counted since local midnight in one account's ledger."""
    if not path.exists():
        return 0
    try:
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=5)) as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'llm_calls'").fetchone():
                return 0
            rows = conn.execute(
                "SELECT usage_json FROM llm_calls WHERE started_at >= ? AND source NOT LIKE ?",
                (day_start(), f"{EXEMPT_PREFIX}%"),
            ).fetchall()
    except sqlite3.Error:
        return 0
    total = 0
    for (raw,) in rows:
        try:
            total += int(json.loads(raw).get("total_tokens") or 0)
        except (ValueError, TypeError, AttributeError):
            continue
    return total


def _student() -> str | None:
    """The acting student's id, or ``None`` for anyone who is not limited."""
    from deeptutor.multi_user.context import get_current_user_or_none

    user = get_current_user_or_none()
    if user is None or not user.id or user.is_admin:
        return None
    from deeptutor.multi_user.roles import current_role

    try:
        return user.id if current_role() == "student" else None
    except Exception:
        return None


def status_for_current_user() -> dict[str, int | None]:
    """``{limit, used, remaining_percent}``; ``limit`` is ``0`` when there is none."""
    student = _student()
    if student is None:
        return {"limit": 0, "used": 0, "remaining_percent": None}
    from deeptutor.multi_user.roles import effective_ai_daily_tokens

    from .usage_ledger import ledger_path

    limit = effective_ai_daily_tokens(student)
    used = used_today(ledger_path())
    if not limit:
        return {"limit": 0, "used": used, "remaining_percent": None}
    return {"limit": limit, "used": used, "remaining_percent": max(0, round(100 * (limit - used) / limit))}


def admit_call(collector: object | None) -> None:
    """Refuse a student's call once today's tokens reached their limit.

    Within a turn (``collector``) only the first call is checked, so an
    answer that started is never cut off halfway.
    """
    if is_exempt():
        return
    if collector is not None and getattr(collector, "ai_limit_admitted", False):
        return
    student = _student()
    if student is not None:
        from deeptutor.multi_user.roles import effective_ai_daily_tokens

        from .usage_ledger import ledger_path

        limit = effective_ai_daily_tokens(student)
        if limit and used_today(ledger_path()) >= limit:
            raise AiLimitReached()
    if collector is not None:
        try:
            setattr(collector, "ai_limit_admitted", True)
        except AttributeError:
            pass


__all__ = [
    "AiLimitReached", "LIMIT_MESSAGE", "admit_call", "exempt", "is_exempt", "school_default_daily_tokens",
    "set_school_default_daily_tokens", "status_for_current_user", "used_today",
]
