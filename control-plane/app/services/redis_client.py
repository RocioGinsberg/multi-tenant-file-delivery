from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis

from app.core.settings import Settings, get_settings


class RedisLike(Protocol):
    async def ping(self) -> bool: ...

    async def aclose(self) -> None: ...


@dataclass(slots=True)
class RedisClient:
    client: RedisLike

    async def ping(self) -> bool:
        return bool(await self.client.ping())

    async def close(self) -> None:
        await self.client.aclose()


def create_redis_client(settings: Settings | None = None) -> RedisClient:
    settings = settings or get_settings()
    client = Redis.from_url(
        settings.redis_url,
        socket_timeout=settings.redis_socket_timeout_seconds,
        socket_connect_timeout=settings.redis_socket_timeout_seconds,
        decode_responses=True,
    )
    return RedisClient(client=client)
