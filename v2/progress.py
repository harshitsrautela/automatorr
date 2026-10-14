"""In-process pub/sub that carries batch progress events to the SSE stream."""

import asyncio
from typing import Any


# Delivers progress events from the batch thread to every connected SSE client.
# Keeps one asyncio queue per subscriber.
class ProgressBroadcaster:
    # Starts with no subscribers and no event loop bound.
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    # Remembers the event loop serving SSE so publish() can hand events to it.
    # Takes the running asyncio loop.
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # Creates and registers a queue for one SSE connection.
    # Returns the new asyncio.Queue that will receive events.
    def subscribe(self) -> "asyncio.Queue[dict[str, Any]]":
        q: "asyncio.Queue[dict[str, Any]]" = asyncio.Queue()
        self._subscribers.add(q)
        return q

    # Removes a queue once its SSE connection has dropped.
    # Takes the queue returned by subscribe().
    def unsubscribe(self, q: "asyncio.Queue[dict[str, Any]]") -> None:
        self._subscribers.discard(q)

    # Sends an event to all subscribers from any thread; does nothing if no loop is bound.
    # Takes the event dict and returns nothing.
    def publish(self, event: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None:
            return
        for q in list(self._subscribers):
            try:
                loop.call_soon_threadsafe(q.put_nowait, event)
            except RuntimeError:
                pass  # loop already closed (e.g. server shutting down)


broadcaster = ProgressBroadcaster()
