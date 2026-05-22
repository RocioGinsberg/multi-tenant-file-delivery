from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import AppUser, Base, Tenant, Workspace
from app.repos.task_repo import TaskRepo
from app.repos.workspace_repo import WorkspaceRepo


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as db_session:
        db_session.add_all([
            Tenant(id="hq", name="HQ", tenant_type="hq"),
            Tenant(id="subsidiary-a", name="Subsidiary A", tenant_type="subsidiary"),
            Tenant(id="subsidiary-b", name="Subsidiary B", tenant_type="subsidiary"),
            AppUser(id="local-user", tenant_id="hq", role="hq_uploader"),
            AppUser(id="sub-a-user", tenant_id="subsidiary-a", role="subsidiary_viewer"),
        ])
        await db_session.flush()
        yield db_session

    await engine.dispose()


async def test_workspace_repo_filters_hq_owner_and_subsidiary_target(
    session: AsyncSession,
):
    repo = WorkspaceRepo()
    session.add_all([
        Workspace(
            id="ws-a",
            name="A",
            owner_tenant_id="hq",
            target_tenant_id="subsidiary-a",
            target_key="aishide",
        ),
        Workspace(
            id="ws-b",
            name="B",
            owner_tenant_id="hq",
            target_tenant_id="subsidiary-b",
            target_key="xinyanhaijia",
        ),
    ])
    await session.flush()

    hq_workspaces = await repo.list_workspaces(session, tenant_id="hq", is_hq=True)
    sub_workspaces = await repo.list_workspaces(
        session,
        tenant_id="subsidiary-a",
        is_hq=False,
    )
    hidden = await repo.get_workspace(
        session,
        "ws-b",
        tenant_id="subsidiary-a",
        is_hq=False,
    )

    assert [workspace.id for workspace in hq_workspaces] == ["ws-a", "ws-b"]
    assert [workspace.id for workspace in sub_workspaces] == ["ws-a"]
    assert hidden is None


async def test_workspace_repo_records_uploaded_item_idempotently(
    session: AsyncSession,
):
    repo = WorkspaceRepo()
    session.add(
        Workspace(
            id="ws-a",
            name="A",
            owner_tenant_id="hq",
            target_tenant_id="subsidiary-a",
            target_key="aishide",
        )
    )
    task = await TaskRepo().create(
        session,
        idempotency_key="idem-workspace",
        owner_tenant_id="hq",
        owner_user_id="local-user",
    )
    item = SimpleNamespace(
        id="item-1",
        filename="report.xlsx",
        file_size=123,
        target_name_matched="aishide",
        dst_path="reports/report.xlsx",
        uploaded_at=None,
    )
    result_item = SimpleNamespace(
        key="reports/report.xlsx",
        size=123,
        sha256="abc",
    )

    first = await repo.record_uploaded_item(
        session,
        task=task,
        item=item,
        result_item=result_item,
        bucket_name="auto-upload-dev",
        uploaded_at=datetime(2026, 5, 22, 9, 0, tzinfo=UTC),
    )
    second = await repo.record_uploaded_item(
        session,
        task=task,
        item=item,
        result_item=result_item,
        bucket_name="auto-upload-dev",
        uploaded_at=datetime(2026, 5, 22, 9, 0, tzinfo=UTC),
    )
    visible = await repo.list_objects(session, "ws-a", tenant_id="subsidiary-a", is_hq=False)

    assert first is not None
    assert second is not None
    assert first.created is True
    assert second.created is False
    assert second.record.workspace_object.id == first.record.workspace_object.id
    assert visible is not None
    assert len(visible) == 1
    assert visible[0].physical_object.object_key == "reports/report.xlsx"
