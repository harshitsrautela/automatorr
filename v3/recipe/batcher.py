"""Answers several records' questions with one model call.

Records run at the same time in their own browsers. When two or more of them reach the same question (copy these values
from my pages; decide my form) within a moment of each other, one call can answer all of them. A Batcher collects the
questions and sends them together once enough have arrived, everyone that is still on the way has arrived, or a short wait is
over. Whoever asked gets only their own answer back."""

import asyncio
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


# Collects questions from concurrent records and answers them in groups.
# `handler` takes a list of (key, payload) and returns {key: (answer, calls)} where `calls` is the number of model calls to
# charge to that record (the first record of a group is charged for the whole group, the others for none, so the totals stay
# honest). A key missing from the result, or an exception, is passed on to that record as "no answer" so it can ask alone.
class Batcher:
    def __init__(self, handler: Callable[[list[tuple[str, Any]]], Awaitable[dict[str, tuple[Any, int]]]], *, size: int = 5, wait: float = 4.0) -> None:
        self.handler = handler
        self.size = size
        self.wait = wait
        self.active = 0  # records that may still ask: a group is sent as soon as every one of them has asked
        self._pending: list[tuple[str, Any, asyncio.Future]] = []
        self._timer: asyncio.TimerHandle | None = None
        self._tasks: set[asyncio.Task] = set()
        self.groups: list[int] = []  # the size of each group sent (for the log)

    # A record that may ask a question of this batcher has started.
    def enter(self) -> None:
        self.active += 1

    # A record will not ask (any more): it asked and was answered, or it ended without asking.
    def leave(self) -> None:
        self.active = max(0, self.active - 1)
        self._maybe_send()

    # Asks one question. Returns (answer, calls charged); raises whatever the handler raised for the whole group.
    async def ask(self, key: str, payload: Any) -> tuple[Any, int] | None:
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending.append((key, payload, future))
        self._maybe_send()
        if self._pending and self._timer is None:
            self._timer = loop.call_later(self.wait, self._send)
        return await future

    # Sends the waiting questions now when the group is full or nobody else is still on the way.
    def _maybe_send(self) -> None:
        if self._pending and (len(self._pending) >= self.size or len(self._pending) >= max(self.active, 1)):
            self._send()

    def _send(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if not self._pending:
            return
        group, self._pending = self._pending[: self.size], self._pending[self.size:]
        task = asyncio.ensure_future(self._run(group))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        if self._pending:
            self._maybe_send()
            if self._pending and self._timer is None:
                self._timer = asyncio.get_running_loop().call_later(self.wait, self._send)

    async def _run(self, group: list[tuple[str, Any, asyncio.Future]]) -> None:
        self.groups.append(len(group))
        try:
            answers = await self.handler([(k, p) for k, p, _f in group])
        except BaseException as exc:  # noqa: BLE001 - every waiting record must be released
            for _k, _p, fut in group:
                if not fut.done():
                    if isinstance(exc, asyncio.CancelledError):
                        fut.cancel()
                    else:
                        fut.set_exception(exc)
            if isinstance(exc, asyncio.CancelledError):
                raise
            return
        for key, _p, fut in group:
            if not fut.done():
                fut.set_result(answers.get(key))

    # Stops groups still being answered (when the batch of records is over).
    async def close(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        for _k, _p, fut in self._pending:
            if not fut.done():
                fut.cancel()
        self._pending = []
