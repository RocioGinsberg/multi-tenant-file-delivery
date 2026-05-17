from __future__ import annotations

import hashlib
import os
from contextlib import asynccontextmanager

import aioboto3
from botocore.exceptions import ClientError

from app.core.settings import get_settings
from app.services.delivery import DeliverySourceReference


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


async def stage_task_archive(task, *, bucket_name: str | None = None) -> DeliverySourceReference:
    """Upload a task's original archive to durable staging storage."""
    settings = get_settings()
    bucket = bucket_name or settings.staging_bucket_name
    archive_path = os.path.join(task.temp_dir, "original.zip")
    key = f"staged/tasks/{task.id}/archive.zip"

    with open(archive_path, "rb") as f:
        data = f.read()

    sha256 = hashlib.sha256(data).hexdigest()
    async with _s3_client() as client:
        await _ensure_bucket(client, bucket)
        await client.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType="application/zip",
            ContentLength=len(data),
        )

    return DeliverySourceReference(
        bucket=bucket,
        key=key,
        sha256=sha256,
        size=len(data),
    )


async def _ensure_bucket(client, bucket: str) -> None:
    try:
        await client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("404", "NoSuchBucket"):
            await client.create_bucket(Bucket=bucket)
            return
        raise
