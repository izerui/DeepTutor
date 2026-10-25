"""
Book event hub
==============

One long-lived event stream **per book**, owned by the process rather than by
whichever request happened to start the work.

Why this exists
---------------

The Book Engine's unit of work is a *book*, not a request: ``confirm_spine``
returns as soon as the page shells exist, while the compilation it queued keeps
running for minutes afterwards. A bus that is created per request and closed in
the router's ``finally`` therefore drops every background event on the floor —
``StreamBus.emit()`` silently no-ops once the bus is closed, so the whole
"watch your book being written" experience degrades into a frozen page.

The hub inverts the ownership:

- **Producers** (engine, compiler, background worker) publish into
  ``get_book_bus(book_id, storage=engine.storage)``. They never close a bus.
- **Consumers** (WebSocket clients) subscribe to that same bus and may come and
  go freely. ``StreamBus.subscribe()`` replays recent history, so a client that
  reconnects mid-compilation catches up instead of waiting for the next event.
- A bus is closed exactly once — when the book is deleted.

Because REST handlers publish into the same place as WebSocket handlers, a book
compiled via REST still streams to anyone watching over WebSocket.
"""

from __future__ import annotations

from deeptutor.runtime.stream_bus import StreamBus

from .registry import strip_ref
from .storage import BookStorage, get_book_storage
from .streaming import BookStream

# Enough to replay the tail of an in-flight stage to a reconnecting client
# (a page emits ~2 events per block), without pinning a whole book's event
# log in memory for the lifetime of the process.
BOOK_EVENT_HISTORY_LIMIT = 400

_buses: dict[tuple[str, str], StreamBus] = {}


def _key(book_id: str, storage: BookStorage | None) -> tuple[str, str]:
    store = storage if storage is not None else get_book_storage()
    return str(store.path_service.get_book_dir().resolve()), strip_ref(book_id)


def get_book_bus(book_id: str, *, storage: BookStorage | None = None) -> StreamBus:
    """Return a bus keyed by physical book directory and normalized Book.id.

    Safe to call from any coroutine: there is no ``await`` between the lookup
    and the insert, so concurrent callers cannot race into two buses.
    Omitted storage means the current local scope, even for ``book:<id>``.
    This helper does not authorize or resolve registry references.
    """
    # Cross-account callers must pass the authorized engine's actual storage.
    key = _key(book_id, storage)
    bus = _buses.get(key)
    if bus is None:
        bus = StreamBus(max_history=BOOK_EVENT_HISTORY_LIMIT, assign_seq=True)
        _buses[key] = bus
    return bus


def get_book_stream(book_id: str, *, storage: BookStorage | None = None) -> BookStream:
    """``get_book_bus`` wrapped in the Book-specific emit helpers."""
    return BookStream(get_book_bus(book_id, storage=storage))


def close_book_bus(book_id: str, *, storage: BookStorage | None = None) -> None:
    """Close only this location's bus, after successful physical deletion.

    Synchronous on purpose — book deletion runs from sync engine and CLI paths.
    Registry-aware deletion must pass the authorized engine's actual storage.
    """
    bus = _buses.pop(_key(book_id, storage), None)
    if bus is not None:
        bus.mark_closed()


__all__ = [
    "BOOK_EVENT_HISTORY_LIMIT",
    "get_book_bus",
    "get_book_stream",
    "close_book_bus",
]
