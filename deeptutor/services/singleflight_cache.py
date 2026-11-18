"""Small in-process TTL cache with one async producer per key."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import time
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class AsyncSingleFlightTTLCache(Generic[K, V]):
    """Bounded insertion-ordered cache plus concurrent request coalescing."""

    def __init__(
        self,
        *,
        limit: int,
        ttl_seconds: float,
        value_timestamp: Callable[[V], float],
        now: Callable[[], float] = time.time,
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be positive")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.limit = limit
        self.ttl_seconds = ttl_seconds
        self.value_timestamp = value_timestamp
        self.now = now
        self.values: dict[K, V] = {}
        self.inflight: dict[K, asyncio.Task[V]] = {}

    def recall(self, key: K) -> V | None:
        value = self.values.get(key)
        if value is None:
            return None
        if self.now() - self.value_timestamp(value) > self.ttl_seconds:
            self.values.pop(key, None)
            return None
        return value

    def remember(self, key: K, value: V) -> None:
        self.values[key] = value
        overflow = len(self.values) - self.limit
        if overflow > 0:
            for stale in list(self.values)[:overflow]:
                self.values.pop(stale, None)

    async def get_or_create(
        self,
        key: K,
        factory: Callable[[], Awaitable[V]],
        *,
        cache_when: Callable[[V], bool] = lambda _value: True,
    ) -> V:
        cached = self.recall(key)
        if cached is not None:
            return cached

        pending = self.inflight.get(key)
        if pending is None or pending.done():
            pending = asyncio.ensure_future(factory())
            self.inflight[key] = pending

            def producer_done(completed: asyncio.Future[V]) -> None:
                # The producer, rather than a cancelled consumer, owns removal.
                if self.inflight.get(key) is completed:
                    self.inflight.pop(key, None)
                # All consumers may have gone away before a producer fails.
                # Retrieve its exception without changing what awaiters receive.
                if not completed.cancelled():
                    completed.exception()

            pending.add_done_callback(producer_done)
        value = await self._await_shielded(pending)

        if cache_when(value):
            self.remember(key, value)
        return value


    @staticmethod
    async def _await_shielded(pending: asyncio.Future[V]) -> V:
        """Await ``pending`` without letting this caller's cancellation reach it.

        Like ``asyncio.shield``, but a producer that fails after every caller
        left is not reported as an "exception in shielded future" (Python 3.14
        logs those); ``producer_done`` already retrieves its exception.
        """
        waiter: asyncio.Future[V] = asyncio.get_running_loop().create_future()

        def relay(done: asyncio.Future[V]) -> None:
            if waiter.done():
                return
            if done.cancelled():
                waiter.cancel()
                return
            exc = done.exception()
            if exc is not None:
                waiter.set_exception(exc)
            else:
                waiter.set_result(done.result())

        pending.add_done_callback(relay)
        try:
            return await waiter
        finally:
            pending.remove_done_callback(relay)


__all__ = ["AsyncSingleFlightTTLCache"]
