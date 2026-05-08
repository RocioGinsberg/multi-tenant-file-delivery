"""Portal adapter for CosDrive task-domain orchestration."""

from __future__ import annotations

from typing import Any, Dict, List

from jobs.cosdrive import task_service as task_impl

cosdrive_repo = task_impl.cosdrive_repo
reg_svc = task_impl.reg_svc
worker_dispatcher = task_impl.worker_dispatcher
classifier = task_impl.classifier
team_svc = task_impl.team_svc


def _sync_impl_dependencies() -> None:
    task_impl.cosdrive_repo = cosdrive_repo
    task_impl.reg_svc = reg_svc
    task_impl.worker_dispatcher = worker_dispatcher
    task_impl.classifier = classifier
    task_impl.team_svc = team_svc


async def create_task(zip_bytes: bytes, user: str, request_id: str) -> Dict[str, Any]:
    _sync_impl_dependencies()
    return await task_impl.create_task(zip_bytes, user, request_id)


async def classify_task(task_id: str, registry_version_id: str, user: str, request_id: str) -> Dict[str, Any]:
    _sync_impl_dependencies()
    return await task_impl.classify_task(task_id, registry_version_id, user, request_id)


def get_task_preview(task_id: str) -> Dict[str, Any]:
    _sync_impl_dependencies()
    return task_impl.get_task_preview(task_id)


def confirm_task(task_id: str, user: str, request_id: str) -> Dict[str, Any]:
    _sync_impl_dependencies()
    return task_impl.confirm_task(task_id, user, request_id)


async def execute_task_upload(task_id: str, user: str, request_id: str) -> Dict[str, Any]:
    _sync_impl_dependencies()
    return await task_impl.execute_task_upload(task_id, user, request_id)


async def retry_failed_task_items(task_id: str, item_ids: List[str], user: str, request_id: str) -> Dict[str, Any]:
    _sync_impl_dependencies()
    return await task_impl.retry_failed_task_items(task_id, item_ids, user, request_id)


def get_task_detail(task_id: str) -> Dict[str, Any]:
    _sync_impl_dependencies()
    return task_impl.get_task_detail(task_id)


def get_task_items(task_id: str, severity: str = "", upload_status: str = "") -> list:
    _sync_impl_dependencies()
    return task_impl.get_task_items(task_id, severity=severity, upload_status=upload_status)


def get_task_upload_progress(task_id: str) -> Dict[str, Any]:
    _sync_impl_dependencies()
    return task_impl.get_task_upload_progress(task_id)


def list_tasks(user: str, page: int = 1, page_size: int = 50) -> Dict[str, Any]:
    _sync_impl_dependencies()
    return task_impl.list_tasks(user, page=page, page_size=page_size)
