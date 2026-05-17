from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import aioboto3
from botocore.exceptions import ClientError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
from app.models import Task, TaskEvent
from app.repos.event_repo import EventRepo

TERMINAL_TASK_STATUSES = {"uploaded", "partial_failed", "failed"}


@dataclass(frozen=True, slots=True)
class StagingCleanupSummary:
    scanned: int
    deleted: int
    failed: int
    skipped: int


@asynccontextmanager
async def _s3_client():
    settings = get_settings()
    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        region_name=settings.s3_region,
    ) as client:
        yield client


async def cleanup_staging_sources(
    session: AsyncSession,
    *,
    retention: timedelta,
    now: datetime | None = None,
    bucket_name: str | None = None,
) -> StagingCleanupSummary:
    """Delete expired staged source objects for terminal tasks.

    Transaction ownership stays with the caller. The function appends a
    ``task_staged_source_deleted`` event after successful delete or missing key.
    """
    cutoff = (now or datetime.now(UTC)) - retention
    candidates = await _list_cleanup_candidates(session, cutoff=cutoff)
    deleted_keys = await _list_deleted_source_keys(session)

    scanned = len(candidates)
    deleted = 0
    failed = 0
    skipped = 0
    event_repo = EventRepo()

    async with _s3_client() as client:
        for event in candidates:
            source = event.payload_json or {}
            bucket = source.get("bucket")
            key = source.get("key")
            if not bucket or not key:
                skipped += 1
                continue
            if bucket_name is not None and bucket != bucket_name:
                skipped += 1
                continue
            if (bucket, key) in deleted_keys:
                skipped += 1
                continue

            try:
                await client.delete_object(Bucket=bucket, Key=key)
            except ClientError as exc:
                code = exc.response["Error"].get("Code")
                if code not in ("404", "NoSuchKey", "NotFound"):
                    failed += 1
                    continue

            await event_repo.append(session, event.task_id, "task_staged_source_deleted", {
                "bucket": bucket,
                "key": key,
                "source_event_id": event.id,
                "deleted_at": (now or datetime.now(UTC)).isoformat(),
            })
            deleted += 1

    return StagingCleanupSummary(
        scanned=scanned,
        deleted=deleted,
        failed=failed,
        skipped=skipped,
    )


async def _list_cleanup_candidates(
    session: AsyncSession,
    *,
    cutoff: datetime,
) -> list[TaskEvent]:
    result = await session.execute(
        select(TaskEvent)
        .join(Task, Task.id == TaskEvent.task_id)
        .where(TaskEvent.event_type == "task_staged_source")
        .where(Task.status.in_(TERMINAL_TASK_STATUSES))
        .where(TaskEvent.created_at <= cutoff)
        .order_by(TaskEvent.created_at.asc(), TaskEvent.id.asc())
    )
    return list(result.scalars().all())


async def _list_deleted_source_keys(session: AsyncSession) -> set[tuple[str, str]]:
    result = await session.execute(
        select(TaskEvent.payload_json)
        .where(TaskEvent.event_type == "task_staged_source_deleted")
    )
    keys: set[tuple[str, str]] = set()
    for payload in result.scalars().all():
        source: dict[str, Any] = payload or {}
        bucket = source.get("bucket")
        key = source.get("key")
        if bucket and key:
            keys.add((bucket, key))
    return keys
