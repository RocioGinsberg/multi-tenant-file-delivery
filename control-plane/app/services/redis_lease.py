from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis

from app.core.settings import Settings, get_settings

_KEY_PART_RE = re.compile(r"[^A-Za-z0-9_.:-]+")
_RELEASE_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
  return redis.call("DEL", KEYS[1])
end
return 0
"""
_REFRESH_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
  return redis.call("EXPIRE", KEYS[1], ARGV[2])
end
return 0
"""


@dataclass(frozen=True, slots=True)
class LeaseClaim:
    key: str
    token: str
    acquired: bool


class LeaseClient(Protocol):
    async def acquire(self, resource: str) -> LeaseClaim:
        ...

    async def refresh(self, claim: LeaseClaim) -> bool:
        ...

    async def release(self, claim: LeaseClaim) -> None:
        ...

    async def aclose(self) -> None:
        ...


class DisabledLeaseClient:
    async def acquire(self, resource: str) -> LeaseClaim:
        return LeaseClaim(key=_lease_key(resource), token="disabled", acquired=True)

    async def refresh(self, claim: LeaseClaim) -> bool:
        return claim.acquired

    async def release(self, claim: LeaseClaim) -> None:
        return None

    async def aclose(self) -> None:
        return None


class RedisLeaseClient:
    def __init__(self, settings: Settings) -> None:
        self._ttl_seconds = getattr(settings, "redis_lease_ttl_seconds", 30)
        self._client = Redis.from_url(
            settings.redis_url,
            socket_timeout=settings.redis_socket_timeout_seconds,
            socket_connect_timeout=settings.redis_socket_timeout_seconds,
            decode_responses=True,
        )

    async def acquire(self, resource: str) -> LeaseClaim:
        key = _lease_key(resource)
        token = uuid.uuid4().hex
        acquired = await self._client.set(
            key,
            token,
            nx=True,
            ex=self._ttl_seconds,
        )
        return LeaseClaim(key=key, token=token, acquired=bool(acquired))

    async def refresh(self, claim: LeaseClaim) -> bool:
        if not claim.acquired:
            return False
        refreshed = await self._client.eval(
            _REFRESH_SCRIPT,
            1,
            claim.key,
            claim.token,
            self._ttl_seconds,
        )
        return bool(refreshed)

    async def release(self, claim: LeaseClaim) -> None:
        if not claim.acquired:
            return
        await self._client.eval(_RELEASE_SCRIPT, 1, claim.key, claim.token)

    async def aclose(self) -> None:
        await self._client.aclose()


def create_redis_lease(settings: Settings | None = None) -> LeaseClient:
    settings = settings or get_settings()
    if getattr(settings, "redis_lease_enabled", False) is True:
        return RedisLeaseClient(settings)
    return DisabledLeaseClient()


def _lease_key(resource: str) -> str:
    safe_resource = _KEY_PART_RE.sub("_", resource.strip())[:160] or "_"
    return f"lease:v1:{safe_resource}"
