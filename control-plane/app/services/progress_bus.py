"""ProgressBus — asyncio-native pub/sub for per-task progress events."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class ProgressBus:
    """Fan-out message bus that routes events by task_id.

    Each subscriber gets its own unbounded asyncio.Queue.  All queues for a
    given task_id receive every published event (fan-out).  A ``None``
    sentinel is used to signal task completion via ``close_task()``.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[dict | None]]] = {}

    @asynccontextmanager
    async def subscribe(self, task_id: str) -> AsyncIterator[asyncio.Queue[dict | None]]:
        """Yield an unbounded Queue that receives events for *task_id*.

        The queue is registered on ``__aenter__`` and removed on ``__aexit__``
        so no events are delivered to it after the block exits.
        """
        queue: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=0)
        self._subscribers.setdefault(task_id, []).append(queue)
        try:
            yield queue
        finally:
            subs = self._subscribers.get(task_id)
            if subs is not None:
                try:
                    subs.remove(queue)
                except ValueError:
                    pass
                if not subs:
                    del self._subscribers[task_id]

    async def publish(self, task_id: str, event: dict) -> None:
        """Put *event* into every subscriber queue registered for *task_id*.

        If there are no subscribers the call returns immediately without
        blocking or raising.
        """
        for queue in list(self._subscribers.get(task_id, [])):
            await queue.put(event)

    async def close_task(self, task_id: str) -> None:
        """Signal completion by sending ``None`` to every subscriber.

        Safe to call when there are no subscribers.
        """
        for queue in list(self._subscribers.get(task_id, [])):
            await queue.put(None)
