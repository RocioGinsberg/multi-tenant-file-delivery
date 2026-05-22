from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PhysicalObject, Workspace, WorkspaceObject


@dataclass(frozen=True, slots=True)
class WorkspaceObjectRecord:
    workspace_object: WorkspaceObject
    physical_object: PhysicalObject
    workspace: Workspace


@dataclass(frozen=True, slots=True)
class WorkspaceObjectWriteResult:
    record: WorkspaceObjectRecord
    created: bool


class WorkspaceRepo:
    """Repository for Phase 6.5 workspace metadata and read access."""

    async def list_workspaces(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        is_hq: bool,
    ) -> list[Workspace]:
        query = select(Workspace)
        if is_hq:
            query = query.where(Workspace.owner_tenant_id == tenant_id)
        else:
            query = query.where(Workspace.target_tenant_id == tenant_id)
        result = await session.execute(query.order_by(Workspace.name.asc(), Workspace.id.asc()))
        return list(result.scalars().all())

    async def get_workspace(
        self,
        session: AsyncSession,
        workspace_id: str,
        *,
        tenant_id: str,
        is_hq: bool,
    ) -> Workspace | None:
        query = select(Workspace).where(Workspace.id == workspace_id)
        if is_hq:
            query = query.where(Workspace.owner_tenant_id == tenant_id)
        else:
            query = query.where(Workspace.target_tenant_id == tenant_id)
        result = await session.execute(query)
        return result.scalar_one_or_none()

    async def get_workspace_by_target_key(
        self,
        session: AsyncSession,
        *,
        owner_tenant_id: str,
        target_key: str,
    ) -> Workspace | None:
        result = await session.execute(
            select(Workspace).where(
                Workspace.owner_tenant_id == owner_tenant_id,
                Workspace.target_key == target_key,
            )
        )
        return result.scalar_one_or_none()

    async def list_objects(
        self,
        session: AsyncSession,
        workspace_id: str,
        *,
        tenant_id: str,
        is_hq: bool,
    ) -> list[WorkspaceObjectRecord] | None:
        workspace = await self.get_workspace(
            session,
            workspace_id,
            tenant_id=tenant_id,
            is_hq=is_hq,
        )
        if workspace is None:
            return None

        result = await session.execute(
            select(WorkspaceObject, PhysicalObject, Workspace)
            .join(PhysicalObject, PhysicalObject.id == WorkspaceObject.physical_object_id)
            .join(Workspace, Workspace.id == WorkspaceObject.workspace_id)
            .where(WorkspaceObject.workspace_id == workspace_id)
            .order_by(WorkspaceObject.uploaded_at.desc(), WorkspaceObject.id.desc())
        )
        return [
            WorkspaceObjectRecord(
                workspace_object=row[0],
                physical_object=row[1],
                workspace=row[2],
            )
            for row in result.all()
        ]

    async def get_object(
        self,
        session: AsyncSession,
        object_id: str,
        *,
        tenant_id: str,
        is_hq: bool,
    ) -> WorkspaceObjectRecord | None:
        query = (
            select(WorkspaceObject, PhysicalObject, Workspace)
            .join(PhysicalObject, PhysicalObject.id == WorkspaceObject.physical_object_id)
            .join(Workspace, Workspace.id == WorkspaceObject.workspace_id)
            .where(WorkspaceObject.id == object_id)
        )
        if is_hq:
            query = query.where(Workspace.owner_tenant_id == tenant_id)
        else:
            query = query.where(Workspace.target_tenant_id == tenant_id)
        result = await session.execute(query)
        row = result.first()
        if row is None:
            return None
        return WorkspaceObjectRecord(
            workspace_object=row[0],
            physical_object=row[1],
            workspace=row[2],
        )

    async def get_object_by_task_item(
        self,
        session: AsyncSession,
        task_item_id: str,
    ) -> WorkspaceObjectRecord | None:
        result = await session.execute(
            select(WorkspaceObject, PhysicalObject, Workspace)
            .join(PhysicalObject, PhysicalObject.id == WorkspaceObject.physical_object_id)
            .join(Workspace, Workspace.id == WorkspaceObject.workspace_id)
            .where(WorkspaceObject.task_item_id == task_item_id)
        )
        row = result.first()
        if row is None:
            return None
        return WorkspaceObjectRecord(
            workspace_object=row[0],
            physical_object=row[1],
            workspace=row[2],
        )

    async def record_uploaded_item(
        self,
        session: AsyncSession,
        *,
        task: Any,
        item: Any,
        result_item: Any,
        bucket_name: str,
        uploaded_at: datetime | None,
    ) -> WorkspaceObjectWriteResult | None:
        """Create workspace metadata for one uploaded task item.

        Returns None when the item cannot map to a workspace or lacks a sink key.
        Duplicate delivery results are idempotent via workspace_object.task_item_id.
        """
        if not result_item.key:
            return None

        existing = await self.get_object_by_task_item(session, item.id)
        if existing is not None:
            return WorkspaceObjectWriteResult(record=existing, created=False)

        target_key = (item.target_name_matched or "").strip()
        if not target_key:
            return None

        workspace = await self.get_workspace_by_target_key(
            session,
            owner_tenant_id=task.owner_tenant_id,
            target_key=target_key,
        )
        if workspace is None:
            return None

        physical = PhysicalObject(
            owner_tenant_id=task.owner_tenant_id,
            sink_type="s3",
            bucket=bucket_name,
            object_key=result_item.key,
            size_bytes=result_item.size or item.file_size or 0,
            sha256=result_item.sha256 or "",
        )
        session.add(physical)
        await session.flush()
        await session.refresh(physical)

        workspace_object = WorkspaceObject(
            workspace_id=workspace.id,
            physical_object_id=physical.id,
            task_id=task.id,
            task_item_id=item.id,
            display_name=item.filename,
            dst_path=item.dst_path or result_item.key,
            uploaded_by_user_id=task.owner_user_id,
            uploaded_at=uploaded_at or item.uploaded_at or datetime.now(UTC),
        )
        session.add(workspace_object)
        await session.flush()
        await session.refresh(workspace_object)
        record = WorkspaceObjectRecord(
            workspace_object=workspace_object,
            physical_object=physical,
            workspace=workspace,
        )
        return WorkspaceObjectWriteResult(record=record, created=True)
