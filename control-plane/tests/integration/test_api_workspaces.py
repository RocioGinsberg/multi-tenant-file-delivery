from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
def async_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _workspace(workspace_id: str = "ws-a", target_tenant_id: str = "subsidiary-a"):
    workspace = MagicMock()
    workspace.id = workspace_id
    workspace.name = "Subsidiary A"
    workspace.owner_tenant_id = "hq"
    workspace.target_tenant_id = target_tenant_id
    workspace.target_key = "aishide"
    workspace.created_at = datetime(2026, 5, 22, 9, 0, tzinfo=UTC)
    return workspace


def _record(object_id: str = "wo-a"):
    workspace = _workspace()
    physical = MagicMock()
    physical.id = "po-a"
    physical.sink_type = "s3"
    physical.bucket = "auto-upload-dev"
    physical.object_key = "reports/report.xlsx"
    physical.size_bytes = 123
    physical.sha256 = "abc"
    workspace_object = MagicMock()
    workspace_object.id = object_id
    workspace_object.workspace_id = workspace.id
    workspace_object.display_name = "report.xlsx"
    workspace_object.dst_path = "reports/report.xlsx"
    workspace_object.task_id = "task-1"
    workspace_object.task_item_id = "item-1"
    workspace_object.uploaded_by_user_id = "local-user"
    workspace_object.uploaded_at = datetime(2026, 5, 22, 9, 1, tzinfo=UTC)
    return MagicMock(
        workspace=workspace,
        physical_object=physical,
        workspace_object=workspace_object,
    )


async def test_list_workspaces_uses_subsidiary_target_scope(async_client):
    from app.core.db import get_session

    async def override_session():
        yield AsyncMock()

    with patch("app.api.workspaces._workspace_repo") as repo:
        repo.list_workspaces = AsyncMock(return_value=[_workspace()])
        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.get(
                "/api/v1/workspaces",
                headers={
                    "X-Actor-Tenant": "subsidiary-a",
                    "X-Actor-User": "sub-a-user",
                    "X-Actor-Role": "subsidiary_viewer",
                },
            )

        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["workspaces"][0]["id"] == "ws-a"
    assert repo.list_workspaces.await_args.kwargs == {
        "tenant_id": "subsidiary-a",
        "is_hq": False,
    }


async def test_workspace_objects_cross_tenant_hidden(async_client):
    from app.core.db import get_session

    async def override_session():
        yield AsyncMock()

    with patch("app.api.workspaces._workspace_repo") as repo:
        repo.list_objects = AsyncMock(return_value=None)
        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.get(
                "/api/v1/workspaces/ws-b/objects",
                headers={
                    "X-Actor-Tenant": "subsidiary-a",
                    "X-Actor-User": "sub-a-user",
                    "X-Actor-Role": "subsidiary_viewer",
                },
            )

        app.dependency_overrides.clear()

    assert resp.status_code == 404


async def test_download_url_presigns_after_authorization(async_client):
    from app.core.db import get_session

    session = AsyncMock()
    settings = MagicMock()
    settings.workspace_download_url_ttl_seconds = 300

    async def override_session():
        yield session

    with (
        patch("app.api.workspaces._workspace_repo") as repo,
        patch("app.api.workspaces._event_repo") as event_repo,
        patch("app.api.workspaces.get_settings", return_value=settings),
        patch("app.api.workspaces.create_presigned_get_url", AsyncMock(return_value="http://url"))
        as presign,
    ):
        repo.get_object = AsyncMock(return_value=_record())
        event_repo.append = AsyncMock()
        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.post(
                "/api/v1/workspace-objects/wo-a/download-url",
                headers={
                    "X-Actor-Tenant": "subsidiary-a",
                    "X-Actor-User": "sub-a-user",
                    "X-Actor-Role": "subsidiary_viewer",
                },
            )

        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["url"] == "http://url"
    presign.assert_awaited_once_with(
        bucket="auto-upload-dev",
        key="reports/report.xlsx",
        expires_in_seconds=300,
    )
    event_repo.append.assert_awaited_once()
    session.commit.assert_awaited_once()


async def test_download_url_does_not_presign_when_unauthorized(async_client):
    from app.core.db import get_session

    async def override_session():
        yield AsyncMock()

    with (
        patch("app.api.workspaces._workspace_repo") as repo,
        patch("app.api.workspaces.create_presigned_get_url", AsyncMock()) as presign,
    ):
        repo.get_object = AsyncMock(return_value=None)
        app.dependency_overrides[get_session] = override_session

        async with async_client as client:
            resp = await client.post(
                "/api/v1/workspace-objects/wo-a/download-url",
                headers={
                    "X-Actor-Tenant": "subsidiary-b",
                    "X-Actor-User": "sub-b-user",
                    "X-Actor-Role": "subsidiary_viewer",
                },
            )

        app.dependency_overrides.clear()

    assert resp.status_code == 404
    presign.assert_not_awaited()
