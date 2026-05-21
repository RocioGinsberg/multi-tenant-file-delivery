from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.services import staging_source


class FakeS3Client:
    def __init__(self) -> None:
        self.created_buckets: list[str] = []
        self.puts: list[dict] = []

    async def head_bucket(self, *, Bucket: str) -> None:
        return None

    async def create_bucket(self, *, Bucket: str) -> None:
        self.created_buckets.append(Bucket)

    async def put_object(self, **kwargs) -> None:
        self.puts.append(kwargs)


@pytest.mark.asyncio
async def test_stage_task_archive_uploads_internal_archive(monkeypatch, tmp_path):
    task_dir = tmp_path / "task-1"
    internal_dir = task_dir / ".auto_upload_internal"
    internal_dir.mkdir(parents=True)
    archive_bytes = b"zip-bytes"
    (internal_dir / "original.zip").write_bytes(archive_bytes)
    (task_dir / "original.zip").write_bytes(b"user-file")
    fake_client = FakeS3Client()

    @asynccontextmanager
    async def fake_s3_client():
        yield fake_client

    monkeypatch.setattr(staging_source, "_s3_client", fake_s3_client)

    source = await staging_source.stage_task_archive(
        SimpleNamespace(id="task-1", temp_dir=str(task_dir)),
        bucket_name="auto-upload-staging",
    )

    assert source.bucket == "auto-upload-staging"
    assert source.key == "staged/tasks/task-1/archive.zip"
    assert source.size == len(archive_bytes)
    assert source.sha256 == "4b9a4ac59f3c3aa32273260df6cf4bf358d1c46f8415126aa35b6380d0abb8f7"
    assert fake_client.puts == [
        {
            "Bucket": "auto-upload-staging",
            "Key": "staged/tasks/task-1/archive.zip",
            "Body": archive_bytes,
            "ContentType": "application/zip",
            "ContentLength": len(archive_bytes),
        }
    ]
