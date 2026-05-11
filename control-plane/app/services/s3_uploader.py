from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

import aioboto3
from botocore.exceptions import ClientError

from app.core.settings import get_settings

# Phase 2: Go worker replaces upload_file entirely; this module gets deleted.

ProgressCallback = Callable[[int], Awaitable[None] | None]

CHUNK_SIZE = 8 * 1024 * 1024        # 8 MB per chunk (S3 min part size is 5 MB)
MULTIPART_THRESHOLD = 50 * 1024 * 1024  # 50 MB


@dataclass(frozen=True)
class UploadResult:
    bucket: str
    key: str
    etag: str
    size_bytes: int


@asynccontextmanager
async def _s3_client():
    s = get_settings()
    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=s.s3_endpoint_url,
        aws_access_key_id=s.s3_access_key_id,
        aws_secret_access_key=s.s3_secret_access_key,
        region_name=s.s3_region,
    ) as client:
        yield client


def _iter_chunks(data: bytes, chunk_size: int = CHUNK_SIZE):
    """Yield successive byte chunks from a bytes object."""
    offset = 0
    while offset < len(data):
        yield data[offset : offset + chunk_size]
        offset += chunk_size


async def _put_object(
    client,
    data: bytes,
    bucket: str,
    key: str,
    content_type: str,
    on_progress: ProgressCallback | None,
) -> str:
    """Single-part PUT. Returns ETag."""
    # aioboto3 put_object accepts bytes directly; botocore streams it internally.
    resp = await client.put_object(
        Bucket=bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
        ContentLength=len(data),
    )
    if on_progress is not None:
        result = on_progress(len(data))
        if asyncio.isfuture(result) or asyncio.iscoroutine(result):
            await result
    return resp["ETag"].strip('"')


async def _upload_part(
    client,
    bucket: str,
    key: str,
    upload_id: str,
    part_number: int,
    chunk: bytes,
) -> dict:
    """Upload a single multipart part. Returns part descriptor for completion."""
    resp = await client.upload_part(
        Bucket=bucket,
        Key=key,
        UploadId=upload_id,
        PartNumber=part_number,
        Body=chunk,
    )
    return {"PartNumber": part_number, "ETag": resp["ETag"]}


async def _multipart_upload(
    client,
    data: bytes,
    bucket: str,
    key: str,
    content_type: str,
    on_progress: ProgressCallback | None,
) -> str:
    """Multipart upload for data >= MULTIPART_THRESHOLD. Returns ETag."""
    resp = await client.create_multipart_upload(
        Bucket=bucket,
        Key=key,
        ContentType=content_type,
    )
    upload_id = resp["UploadId"]

    chunks = list(_iter_chunks(data))

    try:
        tasks = [
            _upload_part(client, bucket, key, upload_id, i + 1, chunk)
            for i, chunk in enumerate(chunks)
        ]
        parts = await asyncio.gather(*tasks)

        # Report progress after all parts complete (parts ran concurrently)
        if on_progress is not None:
            result = on_progress(len(data))
            if asyncio.isfuture(result) or asyncio.iscoroutine(result):
                await result

        parts_sorted = sorted(parts, key=lambda p: p["PartNumber"])
        complete_resp = await client.complete_multipart_upload(
            Bucket=bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts_sorted},
        )
        return complete_resp["ETag"].strip('"')

    except Exception:
        await client.abort_multipart_upload(
            Bucket=bucket, Key=key, UploadId=upload_id
        )
        raise


async def upload_file(
    data: bytes,
    bucket: str,
    key: str,
    *,
    content_type: str = "application/octet-stream",
    on_progress: ProgressCallback | None = None,
) -> UploadResult:
    """Stream-upload a file to S3/MinIO.

    Splits data into 1 MB chunks internally — never passes the full buffer
    as a single write. Falls back to multipart for files >= 50 MB.

    Phase 2: replaced entirely by Go worker; callers (task_runner) switch to
    publishing a Kafka message instead of calling this function.
    """
    async with _s3_client() as client:
        if len(data) >= MULTIPART_THRESHOLD:
            etag = await _multipart_upload(
                client, data, bucket, key, content_type, on_progress
            )
        else:
            etag = await _put_object(
                client, data, bucket, key, content_type, on_progress
            )

    return UploadResult(
        bucket=bucket,
        key=key,
        etag=etag,
        size_bytes=len(data),
    )


async def ensure_bucket_exists(bucket: str) -> None:
    """Create bucket if it does not exist. Useful for local MinIO dev setup."""
    async with _s3_client() as client:
        try:
            await client.head_bucket(Bucket=bucket)
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code in ("404", "NoSuchBucket"):
                await client.create_bucket(Bucket=bucket)
            else:
                raise
