"""ProgressBus — asyncio-native pub/sub for per-task progress events."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.settings import Settings, get_settings

logger = logging.getLogger(__name__)

_CLOSE_MESSAGE = "__progress_bus_close__"


class ProgressBackend(Protocol):
    def subscribe(
        self, task_id: str
    ) -> AbstractAsyncContextManager[asyncio.Queue[dict | None]]:
        ...

    async def publish(self, task_id: str, event: dict) -> None:
        ...

    async def close_task(self, task_id: str) -> None:
        ...

    async def aclose(self) -> None:
        ...


class MemoryProgressBackend:
    """In-process fan-out backend used by the default ProgressBus."""

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
        """Put *event* into every subscriber queue registered for *task_id*."""
        for queue in list(self._subscribers.get(task_id, [])):
            await queue.put(event)

    async def close_task(self, task_id: str) -> None:
        """Signal completion by sending ``None`` to every subscriber."""
        for queue in list(self._subscribers.get(task_id, [])):
            await queue.put(None)

    async def aclose(self) -> None:
        self._subscribers.clear()


class RedisProgressBackend:
    """Redis pub/sub backend that fans out progress events across processes."""

    def __init__(self, settings: Settings) -> None:
        self._fallback = MemoryProgressBackend()
        self._subscribe_timeout_seconds = settings.redis_socket_timeout_seconds
        self._client = Redis.from_url(
            settings.redis_url,
            socket_timeout=settings.redis_socket_timeout_seconds,
            socket_connect_timeout=settings.redis_socket_timeout_seconds,
            decode_responses=True,
        )

    @asynccontextmanager
    async def subscribe(self, task_id: str) -> AsyncIterator[asyncio.Queue[dict | None]]:
        queue: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=0)
        channel = self._channel(task_id)
        pubsub = None
        try:
            pubsub = self._client.pubsub(ignore_subscribe_messages=False)
            await pubsub.subscribe(channel)
            await asyncio.wait_for(
                self._wait_subscribed(pubsub, channel),
                timeout=self._subscribe_timeout_seconds,
            )
        except (RedisError, TimeoutError):
            if pubsub is not None:
                with suppress(RedisError):
                    await pubsub.aclose()
            logger.warning("Redis progress subscribe failed; falling back to memory", exc_info=True)
            async with self._fallback.subscribe(task_id) as fallback_queue:
                yield fallback_queue
            return

        pump = asyncio.create_task(self._pump(pubsub, queue))
        try:
            yield queue
        finally:
            pump.cancel()
            try:
                await pump
            except asyncio.CancelledError:
                pass
            finally:
                with suppress(RedisError):
                    await pubsub.unsubscribe(channel)
                with suppress(RedisError):
                    await pubsub.aclose()

    async def publish(self, task_id: str, event: dict) -> None:
        try:
            await self._client.publish(self._channel(task_id), json.dumps(event))
        except RedisError:
            logger.warning("Redis progress publish failed; falling back to memory", exc_info=True)
        await self._fallback.publish(task_id, event)

    async def close_task(self, task_id: str) -> None:
        try:
            await self._client.publish(self._channel(task_id), _CLOSE_MESSAGE)
        except RedisError:
            logger.warning("Redis progress close failed; falling back to memory", exc_info=True)
        await self._fallback.close_task(task_id)

    async def aclose(self) -> None:
        await self._fallback.aclose()
        with suppress(RedisError):
            await self._client.aclose()

    async def _pump(self, pubsub, queue: asyncio.Queue[dict | None]) -> None:
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is None:
                    await asyncio.sleep(0)
                    continue
                if message.get("type") != "message":
                    continue

                data = message.get("data")
                if data == _CLOSE_MESSAGE:
                    await queue.put(None)
                    continue

                try:
                    decoded = json.loads(data)
                except (TypeError, json.JSONDecodeError):
                    logger.warning("Dropped malformed Redis progress message")
                    continue
                if isinstance(decoded, dict):
                    await queue.put(decoded)
                else:
                    logger.warning("Dropped non-object Redis progress message")
        except RedisError:
            logger.warning("Redis progress subscription failed; closing subscriber", exc_info=True)
            await queue.put(None)

    async def _wait_subscribed(self, pubsub, channel: str) -> None:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=False, timeout=1.0)
            if message is None:
                await asyncio.sleep(0)
                continue
            if message.get("type") == "subscribe" and message.get("channel") == channel:
                return

    @staticmethod
    def _channel(task_id: str) -> str:
        return f"progress:{task_id}"


class ProgressBus:
    """Fan-out message bus that routes events by task_id.

    Each subscriber gets its own unbounded asyncio.Queue.  All queues for a
    given task_id receive every published event (fan-out).  A ``None``
    sentinel is used to signal task completion via ``close_task()``.
    """

    def __init__(self, backend: ProgressBackend | None = None) -> None:
        self._backend = backend or MemoryProgressBackend()

    @asynccontextmanager
    async def subscribe(self, task_id: str) -> AsyncIterator[asyncio.Queue[dict | None]]:
        async with self._backend.subscribe(task_id) as queue:
            yield queue

    async def publish(self, task_id: str, event: dict) -> None:
        """Put *event* into every subscriber queue registered for *task_id*.

        If there are no subscribers the call returns immediately without
        blocking or raising.
        """
        await self._backend.publish(task_id, event)

    async def close_task(self, task_id: str) -> None:
        """Signal completion by sending ``None`` to every subscriber.

        Safe to call when there are no subscribers.
        """
        await self._backend.close_task(task_id)

    async def aclose(self) -> None:
        await self._backend.aclose()


def create_progress_bus(settings: Settings | None = None) -> ProgressBus:
    settings = settings or get_settings()
    if settings.progress_backend == "memory":
        return ProgressBus()
    if settings.progress_backend == "redis":
        return ProgressBus(RedisProgressBackend(settings))
    raise ValueError(f"Unsupported progress_backend: {settings.progress_backend!r}")
