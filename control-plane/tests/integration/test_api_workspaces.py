from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import get_session
from app.main import app
from app.models import (
    AppUser,
    Base,
    Task,
    TaskItem,
    Tenant,
    Workspace,
)
from app.repos.event_repo import EventRepo
from app.services.delivery import DeliveryResultMessage, apply_delivery_result


@pytest.fixture
def async_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        yield session

    await engine.dispose()


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


def _subsidiary_actor_headers() -> dict[str, str]:
    return {
        "X-Actor-Tenant": "subsidiary-a",
        "X-Actor-User": "sub-a-user",
        "X-Actor-Role": "subsidiary_viewer",
    }


def _seed_workspace_tenants(session: AsyncSession) -> None:
    session.add_all([
        Tenant(id="hq", name="HQ", tenant_type="hq"),
        Tenant(id="subsidiary-a", name="Subsidiary A", tenant_type="subsidiary"),
        AppUser(id="local-user", tenant_id="hq", role="hq_uploader"),
        AppUser(id="sub-a-user", tenant_id="subsidiary-a", role="subsidiary_viewer"),
        Workspace(
            id="ws-a",
            name="Subsidiary A",
            owner_tenant_id="hq",
            target_tenant_id="subsidiary-a",
            target_key="aishide",
        ),
    ])


async def test_list_workspaces_uses_subsidiary_target_scope(async_client):
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
        "access_scope": "target",
    }


async def test_workspace_objects_cross_tenant_hidden(async_client):
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


async def test_workspace_read_view_smoke_result_apply_to_subsidiary_download(
    async_client,
    db_session: AsyncSession,
):
    _seed_workspace_tenants(db_session)
    task = Task(
        idempotency_key="idem-workspace-api-smoke",
        status="queued",
        owner_tenant_id="hq",
        owner_user_id="local-user",
    )
    db_session.add(task)
    await db_session.flush()
    item = TaskItem(
        task_id=task.id,
        src_path="aishide/report.xlsx",
        filename="report.xlsx",
        ext=".xlsx",
        file_size=5,
        target_name_raw="aishide",
        target_name_matched="aishide",
        document_type="report",
        category_name="reports",
        dst_dir="reports",
        dst_path="reports/report.xlsx",
        severity="ok",
    )
    db_session.add(item)
    await db_session.flush()
    await apply_delivery_result(
        db_session,
        DeliveryResultMessage(
            task_id=task.id,
            status="uploaded",
            uploaded=1,
            failed=0,
            processed=1,
            started_at=datetime(2026, 5, 22, 9, 0, tzinfo=UTC),
            ended_at=datetime(2026, 5, 22, 9, 1, tzinfo=UTC),
            items=[{
                "item_id": item.id,
                "status": "uploaded",
                "key": "reports/report.xlsx",
                "size": 5,
                "sha256": "abc",
            }],
        ),
    )
    await db_session.commit()

    async def override_session():
        yield db_session

    settings = MagicMock()
    settings.workspace_download_url_ttl_seconds = 300

    with (
        patch("app.api.workspaces.get_settings", return_value=settings),
        patch("app.api.workspaces.create_presigned_get_url", AsyncMock(return_value="http://url")),
    ):
        app.dependency_overrides[get_session] = override_session
        async with async_client as client:
            workspaces_resp = await client.get(
                "/api/v1/workspaces",
                headers=_subsidiary_actor_headers(),
            )
            objects_resp = await client.get(
                "/api/v1/workspaces/ws-a/objects",
                headers=_subsidiary_actor_headers(),
            )
            object_id = objects_resp.json()["objects"][0]["id"]
            download_resp = await client.post(
                f"/api/v1/workspace-objects/{object_id}/download-url",
                headers=_subsidiary_actor_headers(),
            )
        app.dependency_overrides.clear()

    assert workspaces_resp.status_code == 200
    assert [workspace["id"] for workspace in workspaces_resp.json()["workspaces"]] == ["ws-a"]
    assert objects_resp.status_code == 200
    assert objects_resp.json()["objects"][0]["display_name"] == "report.xlsx"
    assert download_resp.status_code == 200
    assert download_resp.json()["url"] == "http://url"
    events = await EventRepo().list_by_task(db_session, task.id)
    assert any(
        event.event_type == "workspace_object_download_url_issued"
        for event in events
    )
