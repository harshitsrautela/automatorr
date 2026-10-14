"""Watches the changes (POST, PUT, PATCH, DELETE requests) a browser sends, so a record is only called done once its
save really reached the server, and a failed save is noticed without anyone configuring which request to watch."""

import asyncio
import time
from typing import Any

_MUTATING = ("POST", "PUT", "PATCH", "DELETE")


# Tracks the change requests of every page in one browser context.
class MutationTracker:
    def __init__(self, context: Any) -> None:
        self._pending: set[Any] = set()
        self.sent = 0  # change requests started
        self.results: list[tuple[str, str, str]] = []  # (method, url, "ok" or what went wrong), in finishing order
        context.on("request", self._started)
        context.on("requestfinished", self._finished)
        context.on("requestfailed", self._failed)

    def _started(self, request) -> None:
        if request.method in _MUTATING:
            self._pending.add(request)
            self.sent += 1

    def _finished(self, request) -> None:
        if request in self._pending:
            self._pending.discard(request)
            asyncio.ensure_future(self._record(request))

    def _failed(self, request) -> None:
        if request in self._pending:
            self._pending.discard(request)
            self.results.append((request.method, request.url, f"failed: {request.failure or 'network error'}"))

    # Looks at the response of a finished change request and notes its status.
    async def _record(self, request) -> None:
        try:
            response = await request.response()
            status = response.status if response else 0
        except Exception:  # noqa: BLE001
            status = 0
        self.results.append((request.method, request.url, "ok" if 0 < status < 400 else f"status {status}"))

    # Waits until no change request is still in flight (or the time runs out); returns True when none are left.
    async def settle(self, timeout: float = 8.0) -> bool:
        deadline = time.monotonic() + timeout
        while self._pending and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.05)  # let the last response be noted
        return not self._pending

    # A message when the most recent change request did not succeed, else None.
    def last_failure(self) -> str | None:
        if not self.results:
            return None
        method, url, outcome = self.results[-1]
        return None if outcome == "ok" else f"{method} {url.split('?')[0]} {outcome}"
