from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.settings import get_settings
from app.repos.event_repo import EventRepo
from app.repos.workspace_repo import WorkspaceObjectRecord, WorkspaceRepo
from app.services.auth import CurrentActor, get_current_actor
from app.services.presign import create_presigned_get_url

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ActorDep = Annotated[CurrentActor, Depends(get_current_actor)]

_workspace_repo = WorkspaceRepo()
_event_repo = EventRepo()


def _workspace_to_dict(workspace) -> dict:
    return {
        "id": workspace.id,
        "name": workspace.name,
        "owner_tenant_id": workspace.owner_tenant_id,
        "target_tenant_id": workspace.target_tenant_id,
        "target_key": workspace.target_key,
        "created_at": workspace.created_at.isoformat() if workspace.created_at else None,
    }


def _object_record_to_dict(record: WorkspaceObjectRecord) -> dict:
    workspace_object = record.workspace_object
    physical_object = record.physical_object
    workspace = record.workspace
    return {
        "id": workspace_object.id,
        "workspace_id": workspace_object.workspace_id,
        "workspace_name": workspace.name,
        "target_tenant_id": workspace.target_tenant_id,
        "display_name": workspace_object.display_name,
        "dst_path": workspace_object.dst_path,
        "task_id": workspace_object.task_id,
        "task_item_id": workspace_object.task_item_id,
        "uploaded_by_user_id": workspace_object.uploaded_by_user_id,
        "uploaded_at": (
            workspace_object.uploaded_at.isoformat() if workspace_object.uploaded_at else None
        ),
        "physical_object": {
            "id": physical_object.id,
            "sink_type": physical_object.sink_type,
            "bucket": physical_object.bucket,
            "object_key": physical_object.object_key,
            "size_bytes": physical_object.size_bytes,
            "sha256": physical_object.sha256,
        },
    }


@router.get("/workspaces")
async def list_workspaces(
    session: SessionDep,
    actor: ActorDep,
):
    workspaces = await _workspace_repo.list_workspaces(
        session,
        tenant_id=actor.tenant_id,
        access_scope=actor.workspace_access_scope,
    )
    return {"workspaces": [_workspace_to_dict(workspace) for workspace in workspaces]}


@router.get("/workspaces/{workspace_id}/objects")
async def list_workspace_objects(
    workspace_id: str,
    session: SessionDep,
    actor: ActorDep,
):
    records = await _workspace_repo.list_objects(
        session,
        workspace_id,
        tenant_id=actor.tenant_id,
        access_scope=actor.workspace_access_scope,
    )
    if records is None:
        raise HTTPException(status_code=404, detail=f"Workspace {workspace_id!r} not found")
    return {
        "workspace_id": workspace_id,
        "objects": [_object_record_to_dict(record) for record in records],
    }


@router.get("/workspace-objects/{object_id}")
async def get_workspace_object(
    object_id: str,
    session: SessionDep,
    actor: ActorDep,
):
    record = await _workspace_repo.get_object(
        session,
        object_id,
        tenant_id=actor.tenant_id,
        access_scope=actor.workspace_access_scope,
    )
    if record is None:
        raise HTTPException(status_code=404, detail=f"Workspace object {object_id!r} not found")
    return _object_record_to_dict(record)


@router.post("/workspace-objects/{object_id}/download-url")
async def create_workspace_object_download_url(
    object_id: str,
    session: SessionDep,
    actor: ActorDep,
):
    record = await _workspace_repo.get_object(
        session,
        object_id,
        tenant_id=actor.tenant_id,
        access_scope=actor.workspace_access_scope,
    )
    if record is None:
        raise HTTPException(status_code=404, detail=f"Workspace object {object_id!r} not found")

    settings = get_settings()
    expires_in = settings.workspace_download_url_ttl_seconds
    physical = record.physical_object
    url = await create_presigned_get_url(
        bucket=physical.bucket,
        key=physical.object_key,
        expires_in_seconds=expires_in,
    )
    event_payload = {
        **actor.to_event_payload(),
        "workspace_id": record.workspace.id,
        "workspace_object_id": record.workspace_object.id,
        "physical_object_id": physical.id,
        "expires_in_seconds": expires_in,
    }
    await _event_repo.append(
        session,
        record.workspace_object.task_id,
        "workspace_object_download_url_issued",
        event_payload,
    )
    await session.commit()
    return {
        "object_id": object_id,
        "url": url,
        "expires_in_seconds": expires_in,
    }
