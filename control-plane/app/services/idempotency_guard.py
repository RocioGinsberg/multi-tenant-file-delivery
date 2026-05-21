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


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    key: str
    token: str
    acquired: bool


class IdempotencyGuard(Protocol):
    async def acquire(self, operation: str, identity: str) -> IdempotencyClaim:
        ...

    async def release(self, claim: IdempotencyClaim) -> None:
        ...

    async def aclose(self) -> None:
        ...


class DisabledIdempotencyGuard:
    async def acquire(self, operation: str, identity: str) -> IdempotencyClaim:
        return IdempotencyClaim(
            key=_guard_key(operation, identity),
            token="disabled",
            acquired=True,
        )

    async def release(self, claim: IdempotencyClaim) -> None:
        return None

    async def aclose(self) -> None:
        return None


class RedisIdempotencyGuard:
    def __init__(self, settings: Settings) -> None:
        self._ttl_seconds = getattr(settings, "redis_idempotency_ttl_seconds", 60)
        self._client = Redis.from_url(
            settings.redis_url,
            socket_timeout=settings.redis_socket_timeout_seconds,
            socket_connect_timeout=settings.redis_socket_timeout_seconds,
            decode_responses=True,
        )

    async def acquire(self, operation: str, identity: str) -> IdempotencyClaim:
        key = _guard_key(operation, identity)
        token = uuid.uuid4().hex
        acquired = await self._client.set(
            key,
            token,
            nx=True,
            ex=self._ttl_seconds,
        )
        return IdempotencyClaim(key=key, token=token, acquired=bool(acquired))

    async def release(self, claim: IdempotencyClaim) -> None:
        if not claim.acquired:
            return
        await self._client.eval(_RELEASE_SCRIPT, 1, claim.key, claim.token)

    async def aclose(self) -> None:
        await self._client.aclose()


def create_idempotency_guard(settings: Settings | None = None) -> IdempotencyGuard:
    settings = settings or get_settings()
    if getattr(settings, "redis_idempotency_enabled", False) is True:
        return RedisIdempotencyGuard(settings)
    return DisabledIdempotencyGuard()


def _guard_key(operation: str, identity: str) -> str:
    safe_operation = _safe_key_part(operation)
    safe_identity = _safe_key_part(identity)
    return f"idempotency:v1:{safe_operation}:{safe_identity}"


def _safe_key_part(value: str) -> str:
    compact = _KEY_PART_RE.sub("_", value.strip())
    return compact[:128] or "_"
