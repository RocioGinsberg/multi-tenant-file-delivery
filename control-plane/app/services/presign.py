from __future__ import annotations

from contextlib import asynccontextmanager

import aioboto3

from app.core.settings import get_settings


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


async def create_presigned_get_url(
    *,
    bucket: str,
    key: str,
    expires_in_seconds: int,
) -> str:
    """Create a short-lived S3/MinIO GET URL for an authorized object."""
    async with _s3_client() as client:
        return await client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in_seconds,
        )
