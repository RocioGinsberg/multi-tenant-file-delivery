from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.s3_uploader import MULTIPART_THRESHOLD, upload_file


def _make_client_mock(*, bucket_exists: bool = True) -> MagicMock:
    """Build a mock aioboto3 S3 client suitable for use as an async context manager."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    if bucket_exists:
        client.put_object = AsyncMock(return_value={"ETag": '"abc123"'})
        client.create_multipart_upload = AsyncMock(return_value={"UploadId": "upload-1"})
        client.upload_part = AsyncMock(side_effect=lambda **kw: {"ETag": f'"part{kw["PartNumber"]}"'})
        client.complete_multipart_upload = AsyncMock(return_value={"ETag": '"multipart-etag"'})
        client.abort_multipart_upload = AsyncMock()
    else:
        from botocore.exceptions import ClientError
        error_response = {"Error": {"Code": "NoSuchBucket", "Message": "no bucket"}}
        client.put_object = AsyncMock(side_effect=ClientError(error_response, "PutObject"))

    return client


def _patch_s3_client(client_mock: MagicMock):
    session_mock = MagicMock()
    session_mock.client.return_value = client_mock
    return patch("app.services.s3_uploader.aioboto3.Session", return_value=session_mock)


async def test_single_put_upload():
    data = b"hello world"
    client = _make_client_mock()

    with _patch_s3_client(client):
        result = await upload_file(data, "test-bucket", "small.txt")

    assert result.bucket == "test-bucket"
    assert result.key == "small.txt"
    assert result.size_bytes == len(data)
    assert result.etag == "abc123"
    client.put_object.assert_awaited_once()


async def test_multipart_upload():
    data = b"x" * (MULTIPART_THRESHOLD + 1)
    client = _make_client_mock()

    with _patch_s3_client(client):
        result = await upload_file(data, "test-bucket", "large.bin")

    assert result.bucket == "test-bucket"
    assert result.key == "large.bin"
    assert result.size_bytes == len(data)
    client.create_multipart_upload.assert_awaited_once()
    client.complete_multipart_upload.assert_awaited_once()
    client.put_object.assert_not_called()


async def test_progress_callback_called():
    data = b"progress test data"
    progress_bytes: list[int] = []

    async def on_progress(n: int) -> None:
        progress_bytes.append(n)

    client = _make_client_mock()
    with _patch_s3_client(client):
        await upload_file(data, "test-bucket", "progress.txt", on_progress=on_progress)

    assert progress_bytes, "on_progress was never called"
    assert progress_bytes[-1] == len(data)


async def test_upload_to_nonexistent_bucket_raises():
    from botocore.exceptions import ClientError

    client = _make_client_mock(bucket_exists=False)
    with _patch_s3_client(client):
        with pytest.raises(ClientError):
            await upload_file(b"data", "no-bucket", "fail.txt")
