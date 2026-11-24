"""Recover which message a legacy Little Tutor thread came from.

Ported from upstream ``thread_attribution`` (50f1d9f52, hardened in 58afb10b5,
cooldown from 75d65b7de) and adapted to the local data model.

Threads opened before 2026-11-20 never wrote ``tutor_source_message_id`` and,
unlike upstream, never wrote ``tutor_selected_text`` either: their preferences
hold only ``parent_session_id`` and ``session_kind``. Selection tutoring was
also never copied into chat history and no quote attachment exists locally.
The one durable record is the sidebar prompt stored with every assistant row
in ``metadata.model_turn.system`` (since v1.6.9, 2026-09-21). It carries the
containing message as it was when the thread was opened (verbatim, or a
bounded excerpt), that message's role, the learner's exact selection and the
source session id.

Matching is deliberately strict:

* the prompt must name this thread's parent conversation;
* only user/assistant messages of that parent, read from the caller's own
  (per-account) store, are candidates, filtered by the recorded role;
* a verbatim containing message must equal the candidate's text, an excerpt
  must be contained in it;
* exactly one candidate is attributed; several, or none, record nothing.

Local preferences distinguish a legacy thread by the *absence* of
``tutor_source_message_id``: opening, moving, detaching and source deletion all
write the key (detach and deletion write ``""``). The write is conditional on
the key still being absent, on the thread still being a tutor thread of the
same parent and on no ``tutor_source_deleted`` mark, so a concurrent detach,
move or deletion wins. A failed attempt is recorded (persisted and in memory)
and retried only after a cooldown, so thread-list reads do not rescan.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import re
import threading
import time
from typing import Any, Sequence
import weakref

from .scope import store_scope
from .selection_grounding import rendered_markdown_text

logger = logging.getLogger(__name__)

TUTOR_SOURCE_KEY = "tutor_source_message_id"
TUTOR_SOURCE_DELETED_KEY = "tutor_source_deleted"
# Wall-clock time of the last attempt that found no single source. Persisted
# so every worker process and a restart honour the cooldown.
BACKFILL_FAILED_AT_KEY = "tutor_source_backfill_failed_at"
BACKFILL_RETRY_SECONDS = 3600
# One request may have to recover several threads; each needs the thread's
# transcript and (at most once) the parent's. Keep a single caller bounded.
MAX_BACKFILLED_THREADS = 50
# Same bound the turn runtime applies when it records a new thread.
TUTOR_SELECTED_TEXT_LIMIT = 2_000
_TRANSCRIPT_ROLES = frozenset({"user", "assistant"})
_OMITTED_MARKER = re.compile(r"\[(?:earlier|later) content omitted\]", re.IGNORECASE)
_TRUNCATED_SUFFIX = re.compile(r"\s*\.\.\.\[truncated\]\s*\Z")
# The two shapes ``_format_selection_tutor_context`` has emitted since the
# prompt was first stored. Parse these, not arbitrary prose.
_EN_PROMPT = re.compile(
    r"\[Containing message\]\n\s*Message role:\s*(?P<role>\w+)\s*\n"
    r"(?P<containing>.*?)\n\s*\[Learner's exact selection\]\s*\n"
    r"(?P<selection>.*?)\n\s*Source session:[ \t]*(?P<source>[^\n]*)",
    re.DOTALL,
)
_ZH_PROMPT = re.compile(
    r"\[原消息上下文\]\n\s*消息角色：\s*(?P<role>\w+)\s*\n"
    r"(?P<containing>.*?)\n\s*\[用户精确选中的内容\]\s*\n"
    r"(?P<selection>.*?)\n\s*来源会话：[ \t]*(?P<source>[^\n]*)",
    re.DOTALL,
)

# In-memory cooldown, keyed by (store scope, thread id). Complements the
# persisted mark when the conditional write itself could not be made.
_FAILED_UNTIL: dict[tuple[str, str], float] = {}

# Parent locks are per event loop and per store scope (account), so
# concurrent thread-list reads of one conversation do not duplicate the scan.
_LOCKS_GUARD = threading.Lock()
_PARENT_LOCKS: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[tuple[str, str], asyncio.Lock]
] = weakref.WeakKeyDictionary()


def _wall_clock() -> float:
    return time.time()


def _monotonic() -> float:
    return time.monotonic()


def _normalized(text: str) -> str:
    return " ".join(str(text or "").split())


def _scope_key(store: Any) -> str:
    try:
        return store_scope(store).cache_key
    except Exception:
        return f"object:{id(store)}"


def _parent_lock(scope_key: str, parent_session_id: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    key = (scope_key, parent_session_id)
    with _LOCKS_GUARD:
        per_loop = _PARENT_LOCKS.get(loop)
        if per_loop is None:
            per_loop = {}
            _PARENT_LOCKS[loop] = per_loop
        lock = per_loop.get(key)
        if lock is None:
            lock = asyncio.Lock()
            per_loop[key] = lock
    return lock


class _Texts:
    """Normalized and Markdown-projected message text, computed on demand."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[str, str | None]] = {}

    def normalized(self, message: dict[str, Any]) -> str:
        key = str(message.get("id"))
        cached = self._cache.get(key)
        if cached is None:
            cached = (_normalized(str(message.get("content") or "")), None)
            self._cache[key] = cached
        return cached[0]

    def projected(self, message: dict[str, Any]) -> str:
        key = str(message.get("id"))
        normalized = self.normalized(message)
        cached = self._cache[key]
        if cached[1] is None:
            try:
                projected = rendered_markdown_text(str(message.get("content") or ""))
            except Exception:
                # An unreadable message simply does not ground this passage.
                logger.debug("tutor thread backfill: unreadable message", exc_info=True)
                projected = normalized
            self._cache[key] = (normalized, projected)
            return projected
        return cached[1]


def _candidates(
    messages: Sequence[dict[str, Any]], role: str | None
) -> list[dict[str, Any]]:
    return [
        message
        for message in messages
        if message.get("id") is not None
        and str(message.get("role") or "") in _TRANSCRIPT_ROLES
        and (role is None or str(message.get("role") or "") == role)
    ]


def _single(matches: list[dict[str, Any]]) -> int | str | None:
    return matches[0].get("id") if len(matches) == 1 else None


def locate_selected_message(
    messages: Sequence[dict[str, Any]],
    selection: str,
    *,
    role: str | None = None,
    containing: str = "",
    texts: _Texts | None = None,
) -> int | str | None:
    """The one parent message the evidence points at, or None.

    With ``containing`` (the prompt's record of the containing message) the
    candidate's text must equal it, or contain it when it was excerpted. The
    selection itself is then not required to appear literally: it was already
    grounded against that message when the thread was opened, including
    LaTeX/Markdown variants a literal search would miss. Without
    ``containing`` the selection must occur in exactly one candidate.
    Ambiguity is never resolved by preference for one role or position.
    """
    texts = texts or _Texts()
    candidates = _candidates(messages, role)
    raw_containing = str(containing or "").strip()
    if raw_containing:
        excerpted = bool(_OMITTED_MARKER.search(raw_containing))
        if not excerpted:
            wanted = _normalized(raw_containing)
            return _single(
                [
                    message
                    for message in candidates
                    if texts.normalized(message) == wanted or texts.projected(message) == wanted
                ]
            )
        fragments = [
            _normalized(fragment)
            for fragment in _OMITTED_MARKER.split(raw_containing)
            if _normalized(fragment)
        ]
        if not fragments:
            return None
        return _single(
            [
                message
                for message in candidates
                if all(
                    fragment in texts.normalized(message) or fragment in texts.projected(message)
                    for fragment in fragments
                )
            ]
        )
    needle = _normalized(_TRUNCATED_SUFFIX.sub("", str(selection or "")))
    if len(needle) < 8:
        return None
    return _single(
        [
            message
            for message in candidates
            if needle in texts.normalized(message) or needle in texts.projected(message)
        ]
    )


@dataclass(frozen=True)
class _PromptEvidence:
    selection: str
    containing: str
    role: str
    source_session_id: str


def _parse_prompt(system: str) -> _PromptEvidence | None:
    for pattern in (_EN_PROMPT, _ZH_PROMPT):
        match = pattern.search(system)
        if match is None:
            continue
        role = str(match.group("role") or "").strip()
        if role not in _TRANSCRIPT_ROLES:
            return None
        containing = str(match.group("containing") or "").strip()
        if not containing:
            return None
        return _PromptEvidence(
            selection=_TRUNCATED_SUFFIX.sub("", str(match.group("selection") or "")).strip(),
            containing=containing,
            role=role,
            source_session_id=str(match.group("source") or "").strip(),
        )
    return None


def _prompt_evidence(messages: Sequence[dict[str, Any]]) -> _PromptEvidence | None:
    """The thread's first stored sidebar prompt.

    Every turn of a legacy thread was sent with the same context, so the first
    readable prompt is representative.
    """
    for message in messages:
        if str(message.get("role") or "") != "assistant":
            continue
        metadata = message.get("metadata")
        model_turn = metadata.get("model_turn") if isinstance(metadata, dict) else None
        system = model_turn.get("system") if isinstance(model_turn, dict) else None
        if not isinstance(system, str) or not system.strip():
            continue
        evidence = _parse_prompt(system)
        if evidence is not None:
            return evidence
    return None


def _preferences(row: dict[str, Any] | None) -> dict[str, Any]:
    preferences = (row or {}).get("preferences")
    return preferences if isinstance(preferences, dict) else {}


def _is_pending(row: dict[str, Any], parent_session_id: str, scope_key: str) -> bool:
    preferences = _preferences(row)
    if preferences.get("session_kind") != "selection_tutor":
        return False
    if str(preferences.get("parent_session_id") or "") != parent_session_id:
        return False
    # Present (even as "") means opened with attribution, moved, detached or
    # orphaned by a deletion: never a legacy thread.
    if preferences.get(TUTOR_SOURCE_KEY) is not None:
        return False
    if preferences.get(TUTOR_SOURCE_DELETED_KEY):
        return False
    failed_at = preferences.get(BACKFILL_FAILED_AT_KEY)
    if isinstance(failed_at, (int, float)) and not isinstance(failed_at, bool):
        if 0 <= _wall_clock() - float(failed_at) < BACKFILL_RETRY_SECONDS:
            return False
    thread_id = str(row.get("session_id") or row.get("id") or "")
    return _FAILED_UNTIL.get((scope_key, thread_id), 0.0) <= _monotonic()


def _remember_failure(scope_key: str, thread_session_id: str) -> None:
    now = _monotonic()
    if len(_FAILED_UNTIL) > 1000:
        for key, until in list(_FAILED_UNTIL.items()):
            if until <= now:
                _FAILED_UNTIL.pop(key, None)
    _FAILED_UNTIL[(scope_key, thread_session_id)] = now + BACKFILL_RETRY_SECONDS


async def _claim(
    store: Any,
    thread_session_id: str,
    parent_session_id: str,
    updates: dict[str, Any],
) -> bool:
    return bool(
        await store.update_session_preferences_if_absent(
            thread_session_id,
            updates,
            absent=(TUTOR_SOURCE_KEY, TUTOR_SOURCE_DELETED_KEY),
            expected={
                "parent_session_id": parent_session_id,
                "session_kind": "selection_tutor",
            },
        )
    )


async def backfill_thread_sources(
    store: Any,
    parent_session_id: str,
    threads: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attribute legacy threads of ``parent_session_id`` in place; return the rows.

    ``threads`` come from the caller's own store; rows of another parent are
    ignored. Stores without the conditional write are left untouched rather
    than written unconditionally.
    """
    parent_id = str(parent_session_id or "").strip()
    if not parent_id or not callable(getattr(store, "update_session_preferences_if_absent", None)):
        return threads
    scope_key = _scope_key(store)
    if not any(_is_pending(thread, parent_id, scope_key) for thread in threads):
        return threads

    async with _parent_lock(scope_key, parent_id):
        pending = [thread for thread in threads if _is_pending(thread, parent_id, scope_key)]
        parent_messages: list[dict[str, Any]] | None = None
        texts = _Texts()
        for thread in pending[:MAX_BACKFILLED_THREADS]:
            thread_session_id = str(thread.get("session_id") or thread.get("id") or "").strip()
            if not thread_session_id:
                continue
            try:
                # The snapshot may predate a turn, move or detach (or another
                # reader's scan); re-read before the expensive work.
                fresh = await store.get_session(thread_session_id)
                if fresh is None:
                    continue
                if not _is_pending(fresh, parent_id, scope_key):
                    thread["preferences"] = dict(_preferences(fresh))
                    continue
                evidence = _prompt_evidence(await store.get_messages(thread_session_id))
                message_id: int | str | None = None
                if evidence is not None and evidence.source_session_id == parent_id:
                    if parent_messages is None:
                        parent_messages = await store.get_messages(parent_id)
                    message_id = locate_selected_message(
                        parent_messages,
                        evidence.selection,
                        role=evidence.role,
                        containing=evidence.containing,
                        texts=texts,
                    )
                if message_id is not None and evidence is not None:
                    updates: dict[str, Any] = {
                        TUTOR_SOURCE_KEY: message_id,
                        "tutor_scope": "selection",
                        "tutor_selected_text": evidence.selection[:TUTOR_SELECTED_TEXT_LIMIT],
                        TUTOR_SOURCE_DELETED_KEY: False,
                    }
                else:
                    _remember_failure(scope_key, thread_session_id)
                    updates = {BACKFILL_FAILED_AT_KEY: _wall_clock()}
                if await _claim(store, thread_session_id, parent_id, updates):
                    if message_id is not None:
                        _FAILED_UNTIL.pop((scope_key, thread_session_id), None)
                    thread["preferences"] = {**_preferences(fresh), **updates}
                else:
                    latest = await store.get_session(thread_session_id)
                    if latest is not None:
                        thread["preferences"] = dict(_preferences(latest))
            except Exception:
                _remember_failure(scope_key, thread_session_id)
                logger.warning(
                    "could not recover the source of tutor thread %s",
                    thread_session_id,
                    exc_info=True,
                )
    return threads


__all__ = [
    "BACKFILL_FAILED_AT_KEY",
    "BACKFILL_RETRY_SECONDS",
    "MAX_BACKFILLED_THREADS",
    "TUTOR_SOURCE_KEY",
    "backfill_thread_sources",
    "locate_selected_message",
]
