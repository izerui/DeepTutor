"""Administrators withdraw or delete other people's published content, nothing more.

Spec 2026-10-29 D10 (P1a leftover, closed in P4): an administrator who does
not own a public book or knowledge base cannot edit it. The routes that make
an item private or delete it run inside :func:`admin_retiring`; only there do
the registries' write checks keep the administrator fallback.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
import functools
import inspect
from typing import Any

_retiring: ContextVar[bool] = ContextVar("admin_retiring", default=False)


def retiring() -> bool:
    return _retiring.get()


@contextmanager
def admin_retiring(active: bool = True) -> Iterator[None]:
    """Enter retiring mode when ``active``; never leaves an enclosing one."""
    if not active:
        yield
        return
    token = _retiring.set(True)
    try:
        yield
    finally:
        _retiring.reset(token)


def retiring_route(when: Callable[[dict[str, Any]], bool] | None = None):
    """Run an async route inside :func:`admin_retiring` (worker threads inherit it)."""

    def decorate(route):
        signature = inspect.signature(route)

        @functools.wraps(route)
        async def wrapper(*args: Any, **kwargs: Any):
            arguments = signature.bind_partial(*args, **kwargs).arguments
            with admin_retiring(when is None or when(arguments)):
                return await route(*args, **kwargs)

        return wrapper

    return decorate


__all__ = ["admin_retiring", "retiring", "retiring_route"]
