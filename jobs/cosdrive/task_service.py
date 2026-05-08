from __future__ import annotations

import json
import logging
import os
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List

from fastapi import HTTPException

from app.repos import cosdrive_repo
from app.services import cosdrive_registry_service as reg_svc
from app.services import worker_dispatcher
from app.services.audit_service import audit_log
from jobs.cosdrive import classifier
from jobs.cosdrive import smh
from jobs.cosdrive import team_service as team_svc

logger = logging.getLogger("jobs.cosdrive.task_service")

TASK_DIR_BASE = os.getenv("COSDRIVE_JOB_DIR", "/tmp/cosdrive_jobs")
MAX_ZIP_SIZE = int(os.getenv("COSDRIVE_MAX_ZIP_BYTES", str(500 * 1024 * 1024)))
MAX_UNZIPPED_SIZE = int(os.getenv("COSDRIVE_MAX_UNZIPPED_BYTES", str(1024 * 1024 * 1024)))
MAX_FILE_COUNT = int(os.getenv("COSDRIVE_MAX_FILE_COUNT", "5000"))


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _decode_zip_entry_name(info: zipfile.ZipInfo) -> str:
    name = info.filename
    if not name:
        return name
    # ZIP UTF-8 flag set: Python already decoded the member name correctly.
    if info.flag_bits & 0x800:
        return name
    # Legacy Windows ZIP tools often store Chinese filenames in GBK/GB18030
    # without the UTF-8 flag. Python decodes those bytes as CP437, producing
    # mojibake like "Θà╖σè¿". Recover the original bytes and retry GB18030.
    if _contains_cjk(name) or name.isascii():
        return name
    try:
        raw_name = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    for encoding in ("utf-8", "gb18030", "gbk"):
        try:
            decoded = raw_name.decode(encoding)
        except UnicodeDecodeError:
            continue
        if decoded and (_contains_cjk(decoded) or decoded.isascii()):
            return decoded
    return name


def _smh_enabled() -> bool:
    return os.getenv("SMH_ENABLED", "true").strip().lower() == "true"


def _build_safe_mode_teams(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    aliases = config.get("team_aliases", {}) or {}
    names: List[str] = []
    seen: set[str] = set()
    for target in aliases.values():
        name = str(target).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)

    teams: List[Dict[str, Any]] = []
    for idx, name in enumerate(sorted(names), start=1):
        teams.append({
            "id": f"local-team-{idx}",
            "name": name,
            "original_name": name,
            "spaceId": f"local-safe-space-{idx}",
            "orgId": "local-safe-org",
        })
    return teams


async def create_task(zip_bytes: bytes, user: str, request_id: str) -> Dict[str, Any]:
    if len(zip_bytes) > MAX_ZIP_SIZE:
        raise HTTPException(status_code=400, detail=f"Zip 大小超限: {len(zip_bytes)} > {MAX_ZIP_SIZE}")

    task_id = uuid.uuid4().hex[:12]
    task_dir = Path(TASK_DIR_BASE) / task_id
    unzipped_dir = task_dir / "unzipped"
    extracted = _safe_extract_zip(zip_bytes, unzipped_dir)

    audit_log({
        "request_id": request_id,
        "user": user,
        "event": "cosdrive_task_created",
        "task_id": task_id,
        "file_count": len(extracted),
    })

    cosdrive_repo.create_task(task_id, str(unzipped_dir), user)
    return {
        "task_id": task_id,
        "status": "draft",
        "file_count": len(extracted),
        "files": extracted,
    }


async def classify_task(task_id: str, registry_version_id: str, user: str, request_id: str) -> Dict[str, Any]:
    task = cosdrive_repo.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} 不存在")
    if task["status"] not in ("draft", "classified", "classification_failed"):
        raise HTTPException(status_code=400, detail=f"Task 状态 '{task['status']}' 不允许分类")
    if not str(registry_version_id).strip():
        raise HTTPException(status_code=400, detail="registry_version_id 不能为空")

    version = reg_svc.get_version(registry_version_id)
    if not version:
        raise HTTPException(status_code=404, detail=f"注册表版本 {registry_version_id} 不存在")

    config = version["config_json"]
    if isinstance(config, str):
        config = json.loads(config)

    if _smh_enabled():
        try:
            flat_teams = await team_svc.refresh_teams()
        except smh.SmhConfigError as exc:
            logger.warning("cosdrive classify blocked by SMH config: task_id=%s error=%s", task_id, exc)
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except smh.SmhApiError as exc:
            logger.warning("cosdrive classify upstream failure: task_id=%s error=%s", task_id, exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    else:
        flat_teams = _build_safe_mode_teams(config)
        logger.info(
            "SMH disabled; use registry synthetic teams for safe-mode classification: task_id=%s team_count=%d",
            task_id,
            len(flat_teams),
        )
    unzipped_dir = Path(task["temp_dir"])
    if not unzipped_dir.exists():
        raise HTTPException(status_code=410, detail="Task 临时文件已过期，请重新上传 zip")

    file_entries = _scan_unzipped_dir(unzipped_dir)
    items, summary = classifier.classify_files(file_entries, config, flat_teams, task_id)

    cosdrive_repo.delete_items_by_task(task_id)
    item_dicts = [item.to_dict() for item in items]
    for item in item_dicts:
        item["task_id"] = task_id
    cosdrive_repo.bulk_insert_items(item_dicts)

    classification_status = "classified" if not summary.has_blocking_errors else "failed"
    new_status = "classified"
    legacy_status = "classified" if classification_status == "classified" else "classification_failed"
    cosdrive_repo.update_task(
        task_id,
        status=new_status,
        classification_status=classification_status,
        registry_version_id=registry_version_id,
        team_snapshot_json=flat_teams,
        summary_json=summary.to_dict(),
    )

    audit_log({
        "request_id": request_id,
        "user": user,
        "event": "cosdrive_task_classified",
        "task_id": task_id,
        "status": legacy_status,
        "classification_status": classification_status,
        "total": summary.total,
        "ok": summary.ok,
        "error": summary.error,
        "warning": summary.warning,
    })

    return {
        "task_id": task_id,
        "status": legacy_status,
        "summary": summary.to_dict(),
        "items": item_dicts,
    }


def get_task_preview(task_id: str) -> Dict[str, Any]:
    task = cosdrive_repo.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} 不存在")
    items = cosdrive_repo.list_items(task_id)
    summary = task.get("summary_json", {})
    if isinstance(summary, str):
        summary = json.loads(summary)
    return {
        "task_id": task_id,
        "status": task["status"],
        "registry_version_id": task.get("registry_version_id", ""),
        "summary": summary,
        "items": items,
    }


def confirm_task(task_id: str, user: str, request_id: str) -> Dict[str, Any]:
    task = cosdrive_repo.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} 不存在")
    if task["status"] != "classified":
        raise HTTPException(status_code=400, detail=f"只有 classified 状态的 Task 才能确认，当前: '{task['status']}'")

    summary = task.get("summary_json", {})
    if isinstance(summary, str):
        summary = json.loads(summary)
    if summary.get("has_blocking_errors"):
        raise HTTPException(status_code=400, detail="存在阻断错误（未匹配团队/任务），无法确认上传")

    updated = cosdrive_repo.update_task(task_id, status="confirmed", confirmed_at="now()")
    audit_log({
        "request_id": request_id,
        "user": user,
        "event": "cosdrive_task_confirmed",
        "task_id": task_id,
    })
    return {
        "task_id": task_id,
        "status": "confirmed",
        "confirmed_at": str(updated.get("confirmed_at", "")),
    }


async def execute_task_upload(task_id: str, user: str, request_id: str) -> Dict[str, Any]:
    task = cosdrive_repo.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} 不存在")
    if task["status"] not in ("confirmed", "partial_failed"):
        raise HTTPException(status_code=400, detail=f"Task 状态 '{task['status']}' 不允许上传")

    cosdrive_repo.update_task(task_id, status="queued")
    items = cosdrive_repo.list_items(task_id, upload_status="pending")
    items = [item for item in items if item["severity"] in ("ok", "warning")]
    if not items:
        cosdrive_repo.update_task(task_id, status="uploaded", finished_at="now()")
        return {"task_id": task_id, "status": "uploaded", "uploaded": 0, "failed": 0}

    unzipped_dir = Path(task["temp_dir"])
    if not unzipped_dir.exists():
        raise HTTPException(status_code=410, detail="Task 临时文件已过期")

    worker_dispatcher.dispatch_cosdrive_upload(task_id)
    audit_log({
        "request_id": request_id,
        "user": user,
        "event": "cosdrive_task_upload_queued",
        "task_id": task_id,
        "status": "queued",
        "queued": len(items),
    })
    return {
        "task_id": task_id,
        "status": "queued",
        "queued": len(items),
        "failed": 0,
    }


async def retry_failed_task_items(task_id: str, item_ids: List[str], user: str, request_id: str) -> Dict[str, Any]:
    task = cosdrive_repo.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} 不存在")
    if task["status"] not in ("partial_failed", "uploaded"):
        raise HTTPException(status_code=400, detail=f"Task 状态 '{task['status']}' 不允许重试")

    reset_count = cosdrive_repo.batch_reset_failed_items(task_id, item_ids or None)
    if reset_count == 0:
        return {"task_id": task_id, "status": task["status"], "reset": 0}
    return await execute_task_upload(task_id, user, request_id)


def get_task_detail(task_id: str) -> Dict[str, Any]:
    task = cosdrive_repo.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} 不存在")
    summary = task.get("summary_json", {})
    if isinstance(summary, str):
        summary = json.loads(summary)
    attempts = cosdrive_repo.list_attempts(task_id)
    events = cosdrive_repo.list_events(task_id)
    return {
        **task,
        "task_id": task_id,
        "summary_json": summary,
        "attempts": [_serialize_attempt(item) for item in attempts],
        "events": [_serialize_event(item) for item in events],
    }


def get_task_items(task_id: str, severity: str = "", upload_status: str = "") -> list:
    return cosdrive_repo.list_items(task_id, severity=severity, upload_status=upload_status)


def get_task_upload_progress(task_id: str) -> Dict[str, Any]:
    task = cosdrive_repo.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} 不存在")
    counts = cosdrive_repo.count_items_by_status(task_id)
    return {"task_id": task_id, "status": task["status"], **counts}


def list_tasks(user: str, page: int = 1, page_size: int = 50) -> Dict[str, Any]:
    offset = (page - 1) * page_size
    rows, total = cosdrive_repo.list_tasks(user=user, limit=page_size, offset=offset)
    tasks = []
    for row in rows:
        summary = row.get("summary_json", {})
        if isinstance(summary, str):
            try:
                summary = json.loads(summary)
            except Exception:
                summary = {}
        tasks.append({
            "task_id": row.get("task_id", ""),
            "status": row.get("status", ""),
            "registry_version_id": row.get("registry_version_id", ""),
            "summary": summary,
            "created_by": row.get("created_by", ""),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else "",
            "confirmed_at": row["confirmed_at"].isoformat() if row.get("confirmed_at") else None,
            "finished_at": row["finished_at"].isoformat() if row.get("finished_at") else None,
            "latest_attempt_status": row.get("latest_attempt_status"),
            "latest_attempt_started_at": row["latest_attempt_started_at"].isoformat() if row.get("latest_attempt_started_at") else None,
            "latest_attempt_finished_at": row["latest_attempt_finished_at"].isoformat() if row.get("latest_attempt_finished_at") else None,
            "latest_event_type": row.get("latest_event_type"),
            "latest_event_status": row.get("latest_event_status"),
            "latest_event_at": row["latest_event_at"].isoformat() if row.get("latest_event_at") else None,
        })
    return {"tasks": tasks, "total": total, "page": page, "page_size": page_size}


def _serialize_attempt(item: Dict[str, Any]) -> Dict[str, Any]:
    metrics = item.get("metrics_json") or {}
    if isinstance(metrics, str):
        try:
            metrics = json.loads(metrics)
        except Exception:
            metrics = {}
    return {
        "attempt_id": item.get("attempt_id", ""),
        "attempt_no": item.get("attempt_no", 0),
        "attempt_status": item.get("attempt_status", ""),
        "worker_key": item.get("worker_key", ""),
        "request_id": item.get("request_id", ""),
        "trace_id": item.get("trace_id", ""),
        "error_code": item.get("error_code", ""),
        "error_message": item.get("error_message", ""),
        "metrics_json": metrics,
        "started_at": item["started_at"].isoformat() if item.get("started_at") else None,
        "finished_at": item["finished_at"].isoformat() if item.get("finished_at") else None,
    }


def _serialize_event(item: Dict[str, Any]) -> Dict[str, Any]:
    payload = item.get("payload_json") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    return {
        "event_id": item.get("event_id", ""),
        "attempt_id": item.get("attempt_id", ""),
        "sequence_no": item.get("sequence_no", 0),
        "event_type": item.get("event_type", ""),
        "from_status": item.get("from_status", ""),
        "to_status": item.get("to_status", ""),
        "request_id": item.get("request_id", ""),
        "trace_id": item.get("trace_id", ""),
        "payload_json": payload,
        "created_at": item["created_at"].isoformat() if item.get("created_at") else None,
    }


def _safe_extract_zip(zip_bytes: bytes, dest: Path) -> List[str]:
    dest.mkdir(parents=True, exist_ok=True)
    extracted: List[str] = []
    total_size = 0

    with zipfile.ZipFile(zipfile.io.BytesIO(zip_bytes)) as zf:
        if len(zf.infolist()) > MAX_FILE_COUNT:
            raise HTTPException(status_code=400, detail=f"Zip 文件数超限: {len(zf.infolist())} > {MAX_FILE_COUNT}")

        for info in zf.infolist():
            if info.is_dir():
                continue
            name = _decode_zip_entry_name(info)
            if name.startswith("__MACOSX") or name.split("/")[-1].startswith("."):
                continue
            target = (dest / name).resolve()
            if not str(target).startswith(str(dest.resolve())):
                raise HTTPException(status_code=400, detail=f"Zip Slip 攻击: {name}")
            total_size += info.file_size
            if total_size > MAX_UNZIPPED_SIZE:
                raise HTTPException(status_code=400, detail=f"解压后总大小超限: > {MAX_UNZIPPED_SIZE}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted.append(name)

    return extracted


def _scan_unzipped_dir(unzipped_dir: Path) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for fp in sorted(unzipped_dir.rglob("*")):
        if not fp.is_file():
            continue
        if fp.name.startswith("."):
            continue
        try:
            rel = str(fp.relative_to(unzipped_dir))
        except ValueError:
            rel = fp.name
        entries.append({
            "filename": fp.name,
            "relative_path": rel,
            "ext": fp.suffix.lower(),
            "file_size": fp.stat().st_size,
        })
    return entries
