from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.services.presign import create_presigned_get_url


async def test_create_presigned_get_url_uses_s3_get_object():
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.generate_presigned_url = AsyncMock(return_value="http://signed")

    session = MagicMock()
    session.client.return_value = client

    with patch("app.services.presign.aioboto3.Session", return_value=session):
        url = await create_presigned_get_url(
            bucket="bucket",
            key="path/report.xlsx",
            expires_in_seconds=300,
        )

    assert url == "http://signed"
    client.generate_presigned_url.assert_awaited_once_with(
        "get_object",
        Params={"Bucket": "bucket", "Key": "path/report.xlsx"},
        ExpiresIn=300,
    )
