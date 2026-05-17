from __future__ import annotations

import io
import json
import os
import uuid
import zipfile
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.settings import get_settings
from app.repos.event_repo import EventRepo
from app.repos.item_repo import ItemRepo
from app.repos.task_repo import TaskRepo
from app.services.classification_profile import (
    DocumentTypeConfig,
    EntryFilterConfig,
    MatchingConfig,
    ProfileConfig,
    TargetConfig,
    TargetExtractionConfig,
)
from app.services.classifier import classify_zip
from app.services.delivery import (
    FileSpoolDeliveryPublisher,
    KafkaDeliveryPublisher,
    build_delivery_task_message,
)
from app.services.progress_bus import ProgressBus
from app.services.staging_source import stage_task_archive
from app.services.task_runner import run_task, set_progress_bus

router = APIRouter()

_task_repo = TaskRepo()
_item_repo = ItemRepo()
_event_repo = EventRepo()

_progress_bus: ProgressBus | None = None


def get_bus() -> ProgressBus:
    if _progress_bus is None:
        raise RuntimeError("ProgressBus not initialised")
    return _progress_bus


def init_progress_bus(bus: ProgressBus) -> None:
    global _progress_bus
    _progress_bus = bus
    set_progress_bus(bus)


def _load_profile(path: str) -> ProfileConfig:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    targets = [
        TargetConfig(
            key=t["key"],
            aliases=t.get("aliases", []),
            strip_number_prefix=t.get("strip_number_prefix", False),
        )
        for t in raw.get("targets", [])
    ]

    document_types: dict[str, DocumentTypeConfig] = {
        k: DocumentTypeConfig(category=v["category"])
        for k, v in raw.get("document_types", {}).items()
    }

    mc_raw = raw.get("matching_config", {})
    matching_config = MatchingConfig(
        enable_fuzzy_match=mc_raw.get("enable_fuzzy_match", True),
        fuzzy_threshold=mc_raw.get("fuzzy_threshold", 70),
        description_fuzzy_threshold=mc_raw.get("description_fuzzy_threshold", 70),
    )

    ef_raw = raw.get("entry_filters", {})
    entry_filters = EntryFilterConfig(
        ignored_filenames=ef_raw.get("ignored_filenames", []),
        ignored_prefixes=ef_raw.get("ignored_prefixes", []),
    )

    te_raw = raw.get("target_extraction", {})
    target_extraction = TargetExtractionConfig(
        strategy=te_raw.get("strategy", "directory_or_filename"),
        delimiters=te_raw.get("delimiters", ["-", "—", "–"]),
        broadcast_target=te_raw.get("broadcast_target"),
    )

    return ProfileConfig(
        version=raw.get("version", "1"),
        targets=targets,
        document_types=document_types,
        suffix_priority=raw.get("suffix_priority", {}),
        description_mapping=raw.get("description_mapping", {}),
        suffix_fallback=raw.get("suffix_fallback", {}),
        entry_filters=entry_filters,
        path_template=raw.get("path_template", "{category}/{document_type}/{filename}"),
        matching_config=matching_config,
        target_extraction=target_extraction,
    )


def _task_to_dict(task) -> dict:
    return {
        "task_id": task.id,
        "status": task.status,
        "idempotency_key": task.idempotency_key,
        "submission_label": task.submission_label,
        "temp_dir": task.temp_dir,
        "summary_json": task.summary_json,
        "created_by": task.created_by,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "confirmed_at": task.confirmed_at.isoformat() if task.confirmed_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
    }


def _item_to_dict(item) -> dict:
    return {
        "id": item.id,
        "task_id": item.task_id,
        "src_path": item.src_path,
        "filename": item.filename,
        "ext": item.ext,
        "file_size": item.file_size,
        "target_name_raw": item.target_name_raw,
        "target_name_matched": item.target_name_matched,
        "document_type": item.document_type,
        "category_name": item.category_name,
        "dst_dir": item.dst_dir,
        "dst_path": item.dst_path,
        "severity": item.severity,
        "error_code": item.error_code,
        "error_message": item.error_message,
        "warning_message": item.warning_message,
        "upload_status": item.upload_status,
        "upload_error": item.upload_error,
        "uploaded_at": item.uploaded_at.isoformat() if item.uploaded_at else None,
    }


@router.post("/tasks", status_code=201)
async def create_task(
    file: UploadFile,
    idempotency_key: str | None = Form(default=None),
    session: AsyncSession = Depends(get_session),
):
    settings = get_settings()
    zip_bytes = await file.read()

    if len(zip_bytes) > settings.max_zip_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {len(zip_bytes)} bytes exceeds {settings.max_zip_bytes}",
        )

    key = idempotency_key or str(uuid.uuid4())

    existing = await _task_repo.get_by_idempotency_key(session, key)
    if existing is not None:
        from fastapi.responses import JSONResponse as _JSONResponse
        return _JSONResponse(
            status_code=200,
            content={"task_id": existing.id, "status": existing.status},
        )

    task = await _task_repo.create(
        session,
        idempotency_key=key,
        submission_label=file.filename or "",
        status="draft",
    )
    task_id = task.id

    extract_dir = os.path.join(settings.task_dir_base, task_id)
    os.makedirs(extract_dir, exist_ok=True)

    original_zip_path = os.path.join(extract_dir, "original.zip")
    with open(original_zip_path, "wb") as f:
        f.write(zip_bytes)

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            for entry in zf.infolist():
                if entry.filename.endswith("/"):
                    continue
                if entry.filename.startswith("/") or ".." in entry.filename.split("/"):
                    continue
                safe_path = os.path.join(extract_dir, entry.filename)
                safe_path = os.path.normpath(safe_path)
                if not safe_path.startswith(os.path.normpath(extract_dir)):
                    continue
                os.makedirs(os.path.dirname(safe_path), exist_ok=True)
                with open(safe_path, "wb") as out:
                    out.write(zf.read(entry.filename))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail=f"Invalid zip file: {exc}") from exc

    task_obj = await _task_repo.get(session, task_id)
    task_obj.temp_dir = extract_dir
    await session.flush()
    await session.commit()

    final = await _task_repo.get(session, task_id)
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=201,
        content={"task_id": final.id, "status": final.status},
    )


@router.post("/tasks/{task_id}/classify")
async def classify_task(
    task_id: str,
    session: AsyncSession = Depends(get_session),
):
    settings = get_settings()

    task = await _task_repo.get(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")

    original_zip = os.path.join(task.temp_dir, "original.zip")
    if not os.path.exists(original_zip):
        raise HTTPException(status_code=422, detail="original.zip not found in task temp_dir")

    try:
        profile = _load_profile(settings.classification_profile_path)
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail=f"Profile not found: {settings.classification_profile_path}",
        )

    with open(original_zip, "rb") as f:
        zip_bytes = f.read()

    classified_items, summary = classify_zip(zip_bytes, profile)

    summary_dict = summary.to_dict()

    _TASK_ITEM_COLS = {
        "src_path", "filename", "ext", "file_size", "target_name_raw",
        "target_name_matched", "document_type", "category_name", "dst_dir",
        "dst_path", "severity", "error_code", "error_message", "warning_message",
    }
    item_dicts = []
    for item in classified_items:
        d = {k: v for k, v in item.to_dict().items() if k in _TASK_ITEM_COLS}
        if d.get("target_name_matched") is None:
            d["target_name_matched"] = ""
        item_dicts.append(d)

    await _item_repo.bulk_insert(session, task_id, item_dicts)

    task_obj = await _task_repo.get(session, task_id)
    task_obj.summary_json = summary_dict
    await session.flush()

    await _task_repo.update_status(session, task_id, "classified")
    await session.commit()

    return {"task_id": task_id, "summary": summary_dict}


@router.get("/tasks/{task_id}/preview")
async def preview_task(
    task_id: str,
    session: AsyncSession = Depends(get_session),
):
    task = await _task_repo.get(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")

    items = await _item_repo.list_by_task(session, task_id)
    return {
        "task_id": task_id,
        "summary": task.summary_json,
        "items": [_item_to_dict(i) for i in items],
    }


@router.post("/tasks/{task_id}/confirm")
async def confirm_task(
    task_id: str,
    session: AsyncSession = Depends(get_session),
):
    task = await _task_repo.get(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")

    updated = await _task_repo.update_status(
        session,
        task_id,
        "confirmed",
        confirmed_at=datetime.now(UTC),
    )
    await session.commit()
    return {"task_id": task_id, "status": updated.status}


@router.post("/tasks/{task_id}/upload")
async def upload_task(
    task_id: str,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    task = await _task_repo.get(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")

    if task.status != "confirmed":
        raise HTTPException(
            status_code=422,
            detail=f"Task must be in 'confirmed' status to upload, got {task.status!r}",
        )

    settings = get_settings()
    delivery_backend = getattr(settings, "delivery_backend", "python")
    if delivery_backend == "go-worker":
        # Phase 2 bridge: the control plane owns classification and task state,
        # while the data plane only receives uploadable item specs.
        items = await _item_repo.list_by_task(session, task_id)
        upload_items = [
            item
            for item in items
            if item.severity in ("ok", "warning") and item.upload_status == "pending"
        ]
        source_ref = None
        if getattr(settings, "delivery_source_mode", "file") == "object":
            source_ref = await stage_task_archive(
                task,
                bucket_name=getattr(settings, "staging_bucket_name", None),
            )

        message = build_delivery_task_message(
            task=task,
            upload_items=upload_items,
            bucket_name=settings.s3_bucket_name,
            source=source_ref,
        )
        delivery_transport = getattr(settings, "delivery_transport", "file")
        if delivery_transport == "file":
            publisher = FileSpoolDeliveryPublisher(
                getattr(settings, "delivery_outbox_base", settings.task_dir_base),
            )
        elif delivery_transport == "kafka":
            publisher = KafkaDeliveryPublisher(
                bootstrap_servers=settings.kafka_bootstrap_servers,
                topic=settings.kafka_task_topic,
            )
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Unsupported delivery transport: {delivery_transport!r}",
            )
        await publisher.publish(message)

        await _task_repo.update_status(session, task_id, "queued")
        await _event_repo.append(session, task_id, "task_queued", {
            "topic": message.topic,
            "transport": delivery_transport,
            "upload_items": len(upload_items),
        })
        await session.commit()
        return {"task_id": task_id, "status": "queued"}

    background_tasks.add_task(run_task, task_id)
    return {"task_id": task_id, "status": "uploading"}


@router.post("/tasks/{task_id}/retry")
async def retry_task(
    task_id: str,
    session: AsyncSession = Depends(get_session),
):
    task = await _task_repo.get(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")

    reset_count = await _item_repo.batch_reset_failed(session, task_id)
    await session.commit()
    return {"task_id": task_id, "reset_count": reset_count}


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: str,
    session: AsyncSession = Depends(get_session),
):
    task = await _task_repo.get(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")
    return _task_to_dict(task)


@router.get("/tasks/{task_id}/progress")
async def task_progress(
    task_id: str,
    bus: ProgressBus = Depends(get_bus),
):
    async def event_stream():
        async with bus.subscribe(task_id) as queue:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/tasks")
async def list_tasks(
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    tasks = await _task_repo.list(session, limit=limit, offset=offset)
    return {"tasks": [_task_to_dict(t) for t in tasks], "limit": limit, "offset": offset}
