from __future__ import annotations

import asyncio
import inspect
import os
import uuid

import pytest

from app.core.settings import Settings


async def _maybe_await(result: object) -> object:
    if inspect.isawaitable(result):
        return await result
    return result


async def _close_lease(lease: object) -> None:
    close = getattr(lease, "aclose", None) or getattr(lease, "close", None)
    if close is None:
        return

    await _maybe_await(close())


def _is_acquired(claim: object) -> bool:
    if isinstance(claim, bool):
        return claim
    if not hasattr(claim, "acquired"):
        return False
    return bool(claim.acquired)


async def _release_claim(lease: object, resource: str, claim: object) -> None:
    if not _is_acquired(claim):
        return

    release = getattr(lease, "release", None)
    if release is None:
        return

    try:
        await _maybe_await(release(claim))
    except TypeError:
        await _maybe_await(release(resource, claim))


@pytest.mark.docker
@pytest.mark.integration
@pytest.mark.asyncio
async def test_redis_lease_claims_exclude_other_instances_until_release():
    if os.getenv("RUN_DOCKER_TESTS") != "1":
        pytest.skip("set RUN_DOCKER_TESTS=1 with deploy/docker-compose.yml redis running")

    from app.services.redis_lease import create_redis_lease

    settings = Settings(
        redis_lease_enabled=True,
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        redis_socket_timeout_seconds=1.0,
        redis_lease_ttl_seconds=2,
    )
    lease1 = create_redis_lease(settings)
    lease2 = create_redis_lease(settings)
    resource = f"phase4.5.redis-lease-docker:{uuid.uuid4().hex}"
    claim1 = None
    claim2_after_release = None

    try:
        claim1 = await _maybe_await(lease1.acquire(resource))
        assert _is_acquired(claim1) is True

        refresh = getattr(lease1, "refresh", None)
        if refresh is not None:
            refreshed = await _maybe_await(refresh(claim1))
            if refreshed is not None:
                assert _is_acquired(refreshed) is True

        duplicate_claim = await _maybe_await(lease2.acquire(resource))
        assert _is_acquired(duplicate_claim) is False

        await _release_claim(lease1, resource, claim1)
        claim1 = None

        claim2_after_release = await _maybe_await(lease2.acquire(resource))
        assert _is_acquired(claim2_after_release) is True
    finally:
        if claim1 is not None:
            await _release_claim(lease1, resource, claim1)
        if claim2_after_release is not None:
            await _release_claim(lease2, resource, claim2_after_release)
        await _close_lease(lease1)
        await _close_lease(lease2)


@pytest.mark.docker
@pytest.mark.integration
@pytest.mark.asyncio
async def test_redis_lease_can_be_reacquired_after_ttl_expires():
    if os.getenv("RUN_DOCKER_TESTS") != "1":
        pytest.skip("set RUN_DOCKER_TESTS=1 with deploy/docker-compose.yml redis running")

    from app.services.redis_lease import create_redis_lease

    settings = Settings(
        redis_lease_enabled=True,
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        redis_socket_timeout_seconds=1.0,
        redis_lease_ttl_seconds=1,
    )
    lease1 = create_redis_lease(settings)
    lease2 = create_redis_lease(settings)
    resource = f"phase4.5.redis-lease-expiry:{uuid.uuid4().hex}"
    claim2 = None

    try:
        claim1 = await _maybe_await(lease1.acquire(resource))
        assert _is_acquired(claim1) is True

        duplicate_claim = await _maybe_await(lease2.acquire(resource))
        assert _is_acquired(duplicate_claim) is False

        await asyncio.sleep(1.2)
        claim2 = await _maybe_await(lease2.acquire(resource))
        assert _is_acquired(claim2) is True
    finally:
        if claim2 is not None:
            await _release_claim(lease2, resource, claim2)
        await _close_lease(lease1)
        await _close_lease(lease2)
