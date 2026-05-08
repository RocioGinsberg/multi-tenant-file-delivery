"""
CosDrive upload router — 注册表驱动的企业网盘上传服务。

接口分组:
  注册表:   /api/upload/cosdrive/registry/*
  团队:     /api/upload/cosdrive/teams/*
  Task:     /api/upload/cosdrive/tasks/*

旧接口（presign / complete / relay / batch-relay / cors-probe）已移除。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File

from jobs.cosdrive import smh

from ..models.cosdrive_job_schemas import (
    ClassifyRequest,
    ClassifiedItemResponse,
    ClassifyResponse,
    ClassifySummary,
    ConfirmResponse,
    PreviewResponse,
    RegistrySaveDraftRequest,
    RegistryValidateResponse,
    RegistryVersionResponse,
    RetryRequest,
    TaskCreateResponse,
    TaskDetailWithTimelineResponse,
    TaskListResponse,
    TaskItemListResponse,
    TeamRefreshResponse,
    UploadProgressResponse,
)
from ..services.authz_service import get_user
from ..services.audit_service import audit_log
from ..services.prefect_links import build_prefect_flow_run_url
from ..services import cosdrive_registry_service as reg_svc
from ..services import cosdrive_team_service as team_svc
from ..services import cosdrive_job_service as task_svc
from ..settings import settings

router = APIRouter(prefix="/api/upload/cosdrive", tags=["upload-cosdrive"])
logger = logging.getLogger("portal.upload_cosdrive")

_MAX_ZIP = 500 * 1024 * 1024


def _missing_smh_fields(*, include_upload_fields: bool) -> list[str]:
    required = {
        "SMH_APP_ID": settings.smh_app_id,
        "SMH_ORG_ID": settings.smh_org_id,
        "SMH_PHONE_NUMBER": settings.smh_phone_number,
        "SMH_PRIVATE_KEY_FILE": settings.smh_private_key_file,
        "SMH_API_BASE_PUBLIC": settings.smh_api_base_public,
    }
    if include_upload_fields:
        required["SMH_LIBRARY_ID"] = settings.smh_library_id
        required["SMH_API_BASE_APP"] = settings.smh_api_base_app
    return [name for name, value in required.items() if not str(value).strip()]


def _smh_runtime_errors() -> list[str]:
    if not settings.smh_enabled:
        return []

    key_file = (settings.smh_private_key_file or "").strip()
    if not key_file:
        return []

    key_path = Path(key_file)
    if not key_path.exists():
        return [f"SMH 私钥文件不存在: {key_file}"]
    if key_path.is_dir():
        return [f"SMH 私钥路径当前是目录，不是文件: {key_file}"]
    if not key_path.is_file():
        return [f"SMH 私钥路径不是常规文件: {key_file}"]
    try:
        if key_path.stat().st_size == 0:
            return [f"SMH 私钥文件为空: {key_file}"]
    except OSError as exc:
        return [f"SMH 私钥文件不可访问: {key_file} ({exc})"]
    return []


# ═══════════════════════════════════════════════════════════════
# 注册表
# ═══════════════════════════════════════════════════════════════

@router.get("/registry/current")
async def registry_current(request: Request):
    """获取当前已发布的注册表版本"""
    get_user(request)
    version = reg_svc.get_current_published()
    if not version:
        raise HTTPException(status_code=404, detail="尚无已发布的注册表版本")
    return _serialize_version(version)


@router.get("/runtime-status")
async def runtime_status(request: Request):
    """Expose lightweight CosDrive runtime status for the Portal UI."""
    get_user(request)
    team_sync_missing = _missing_smh_fields(include_upload_fields=False)
    upload_missing = _missing_smh_fields(include_upload_fields=True)
    runtime_errors = _smh_runtime_errors()
    return {
        "safe_mode": not settings.smh_enabled,
        "team_refresh_enabled": settings.smh_enabled and not team_sync_missing and not runtime_errors,
        "upload_ready": settings.smh_enabled and not upload_missing and not runtime_errors,
        "cosdrive_enabled": settings.cosdrive_enabled,
        "smh_api_configured": bool(
            settings.smh_api_base_public and settings.smh_api_base_app
        ),
        "smh_missing_fields": upload_missing,
        "smh_runtime_errors": runtime_errors,
        "allowed_prefixes": settings.cosdrive_allowed_prefixes,
    }


@router.get("/registry/versions")
async def registry_versions(request: Request, limit: int = Query(50, ge=1, le=200)):
    """列出所有注册表版本"""
    get_user(request)
    versions = reg_svc.list_versions(limit)
    return [_serialize_version(v) for v in versions]


@router.get("/registry/{version_id}")
async def registry_get(version_id: str, request: Request):
    """获取指定版本"""
    get_user(request)
    version = reg_svc.get_version(version_id)
    if not version:
        raise HTTPException(status_code=404, detail="版本不存在")
    return _serialize_version(version)


@router.post("/registry/draft")
async def registry_save_draft(body: RegistrySaveDraftRequest, request: Request):
    """保存注册表草稿"""
    user = get_user(request)
    try:
        result = reg_svc.save_draft(body.config.model_dump(), user["user"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    audit_log({
        "request_id": request.state.request_id,
        "user": user["user"],
        "event": "cosdrive_registry_draft_saved",
        "version_id": result["id"],
    })
    return _serialize_version(result)


@router.post("/registry/{version_id}/publish")
async def registry_publish(version_id: str, request: Request):
    """发布指定版本"""
    user = get_user(request)
    try:
        result = reg_svc.publish(version_id, user["user"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    audit_log({
        "request_id": request.state.request_id,
        "user": user["user"],
        "event": "cosdrive_registry_published",
        "version_id": result["id"],
    })
    return _serialize_version(result)


@router.post("/registry/{version_id}/rollback")
async def registry_rollback(version_id: str, request: Request):
    """回滚到指定版本"""
    user = get_user(request)
    try:
        result = reg_svc.rollback(version_id, user["user"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    audit_log({
        "request_id": request.state.request_id,
        "user": user["user"],
        "event": "cosdrive_registry_rollback",
        "version_id": result["id"],
    })
    return _serialize_version(result)


@router.post("/registry/validate")
async def registry_validate(body: RegistrySaveDraftRequest, request: Request):
    """校验注册表配置（不保存）"""
    get_user(request)
    valid, errors, warnings = reg_svc.validate_config(body.config.model_dump())
    return RegistryValidateResponse(valid=valid, errors=errors, warnings=warnings)


# ═══════════════════════════════════════════════════════════════
# 团队
# ═══════════════════════════════════════════════════════════════

@router.post("/teams/refresh")
async def teams_refresh(request: Request):
    """实时从 SMH API 拉取团队列表"""
    user = get_user(request)
    if not settings.smh_enabled:
        raise HTTPException(status_code=409, detail="当前为 safe-mode，未启用在线团队同步")

    missing = _missing_smh_fields(include_upload_fields=False)
    if missing:
        raise HTTPException(
            status_code=503,
            detail=f"SMH 在线团队同步缺失配置: {', '.join(missing)}",
        )

    runtime_errors = _smh_runtime_errors()
    if runtime_errors:
        raise HTTPException(status_code=503, detail="；".join(runtime_errors))

    try:
        flat_teams = await team_svc.refresh_teams()
    except smh.SmhConfigError as exc:
        logger.warning("cosdrive team refresh blocked by SMH config: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except smh.SmhApiError as exc:
        logger.warning("cosdrive team refresh upstream failure: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    audit_log({
        "request_id": request.state.request_id,
        "user": user["user"],
        "event": "cosdrive_teams_refreshed",
        "count": len(flat_teams),
    })
    return {"teams": flat_teams, "count": len(flat_teams)}


# ═══════════════════════════════════════════════════════════════
# Task — 创建
# ═══════════════════════════════════════════════════════════════

@router.post("/tasks")
async def task_create(request: Request, zip_file: UploadFile = File(...)):
    """上传 zip，创建 CosDrive task。"""
    user = get_user(request)
    zip_bytes = await zip_file.read()
    if len(zip_bytes) > _MAX_ZIP:
        raise HTTPException(
            status_code=413,
            detail=f"Zip 不能超过 {_MAX_ZIP // (1024*1024)} MB",
        )
    result = await task_svc.create_task(
        zip_bytes, user["user"], request.state.request_id,
    )
    return result


# ═══════════════════════════════════════════════════════════════
# Task — 分类 / 重分类
# ═══════════════════════════════════════════════════════════════

@router.post("/tasks/{task_id}/classify")
async def task_classify(task_id: str, body: ClassifyRequest, request: Request):
    """执行自动分类"""
    user = get_user(request)
    result = await task_svc.classify_task(
        task_id, body.registry_version_id,
        user["user"], request.state.request_id,
    )
    return result


@router.post("/tasks/{task_id}/reclassify")
async def task_reclassify(task_id: str, body: ClassifyRequest, request: Request):
    """修改注册表后重新分类（复用同一批 zip）"""
    user = get_user(request)
    result = await task_svc.classify_task(
        task_id, body.registry_version_id,
        user["user"], request.state.request_id,
    )
    return result


# ═══════════════════════════════════════════════════════════════
# Task — 预览
# ═══════════════════════════════════════════════════════════════

@router.get("/tasks/{task_id}/preview")
async def task_preview(task_id: str, request: Request):
    """获取分类预览结果"""
    get_user(request)
    return task_svc.get_task_preview(task_id)


# ═══════════════════════════════════════════════════════════════
# Task — 确认
# ═══════════════════════════════════════════════════════════════

@router.post("/tasks/{task_id}/confirm")
async def task_confirm(task_id: str, request: Request):
    """上传者确认预览结果"""
    user = get_user(request)
    return task_svc.confirm_task(task_id, user["user"], request.state.request_id)


# ═══════════════════════════════════════════════════════════════
# Task — 执行上传
# ═══════════════════════════════════════════════════════════════

@router.post("/tasks/{task_id}/upload")
async def task_upload(task_id: str, request: Request):
    """执行批量上传"""
    user = get_user(request)
    return await task_svc.execute_task_upload(
        task_id, user["user"], request.state.request_id,
    )


@router.post("/tasks/{task_id}/retry")
async def task_retry(task_id: str, body: RetryRequest, request: Request):
    """重试失败项"""
    user = get_user(request)
    return await task_svc.retry_failed_task_items(
        task_id, body.item_ids, user["user"], request.state.request_id,
    )


# ═══════════════════════════════════════════════════════════════
# Task — 查询
# ═══════════════════════════════════════════════════════════════

@router.get("/tasks", response_model=TaskListResponse)
async def task_list(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    user = get_user(request)
    return task_svc.list_tasks(user["user"], page=page, page_size=page_size)

@router.get("/tasks/{task_id}", response_model=TaskDetailWithTimelineResponse)
async def task_detail(task_id: str, request: Request):
    """查询 Task 详情。"""
    get_user(request)
    result = task_svc.get_task_detail(task_id)
    prefect_flow_run_id = _extract_prefect_flow_run_id(result)
    return {
        **result,
        "prefect_flow_run_id": prefect_flow_run_id,
        "prefect_ui_url": build_prefect_flow_run_url(prefect_flow_run_id),
    }


@router.get("/tasks/{task_id}/items")
async def task_items(
    task_id: str,
    request: Request,
    severity: str = Query(""),
    upload_status: str = Query(""),
):
    """查询 Task 下所有 item。"""
    get_user(request)
    items = task_svc.get_task_items(task_id, severity=severity, upload_status=upload_status)
    return {"task_id": task_id, "items": items, "total": len(items)}


@router.get("/tasks/{task_id}/progress")
async def task_progress(task_id: str, request: Request):
    """查询上传进度"""
    get_user(request)
    return task_svc.get_task_upload_progress(task_id)


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def _serialize_version(v: dict) -> dict:
    """序列化注册表版本为 API 响应"""
    config = v.get("config_json", {})
    if isinstance(config, str):
        config = json.loads(config)
    return {
        "id": v["id"],
        "version_no": v["version_no"],
        "status": v["status"],
        "config_json": config,
        "created_by": v.get("created_by", ""),
        "created_at": str(v.get("created_at", "")),
        "published_by": v.get("published_by", ""),
        "published_at": str(v.get("published_at", "")) if v.get("published_at") else None,
    }


def _extract_prefect_flow_run_id(task_detail: dict) -> str | None:
    for candidate in (
        (task_detail or {}).get("prefect_flow_run_id"),
        *((item or {}).get("prefect_flow_run_id") for item in (task_detail or {}).get("attempts", [])),
    ):
        if candidate:
            return str(candidate)
    for item in (task_detail or {}).get("events", []):
        payload = (item or {}).get("payload_json") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        candidate = payload.get("prefect_flow_run_id")
        if candidate:
            return str(candidate)
    return None
