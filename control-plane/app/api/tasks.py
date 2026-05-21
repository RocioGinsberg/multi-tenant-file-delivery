from __future__ import annotations

import asyncio
import io
import json
import os
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
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
from app.services.idempotency_guard import IdempotencyClaim, create_idempotency_guard
from app.services.progress_bus import ProgressBus
from app.services.staging_source import (
    INTERNAL_ARCHIVE_DIR,
    delete_staged_archive,
    find_task_archive_path,
    stage_task_archive,
    task_archive_path,
)
from app.services.task_runner import run_task, set_progress_bus

router = APIRouter()

_task_repo = TaskRepo()
_item_repo = ItemRepo()
_event_repo = EventRepo()

_progress_bus: ProgressBus | None = None

SessionDep = Annotated[AsyncSession, Depends(get_session)]
FormString = Annotated[str | None, Form()]
UploadedFiles = Annotated[list[UploadFile] | None, File()]


@dataclass(frozen=True, slots=True)
class PreparedArchive:
    label: str
    zip_bytes: bytes


def get_bus() -> ProgressBus:
    if _progress_bus is None:
        raise RuntimeError("ProgressBus not initialised")
    return _progress_bus


ProgressBusDep = Annotated[ProgressBus, Depends(get_bus)]


def init_progress_bus(bus: ProgressBus) -> None:
    global _progress_bus
    _progress_bus = bus
    set_progress_bus(bus)


async def _acquire_idempotency_claim(settings, operation: str, identity: str) -> IdempotencyClaim:
    guard = create_idempotency_guard(settings)
    try:
        claim = await guard.acquire(operation, identity)
    finally:
        await guard.aclose()
    if not claim.acquired:
        raise HTTPException(
            status_code=409,
            detail=f"{operation} for {identity!r} is already in progress",
        )
    return claim


async def _release_idempotency_claim(settings, claim: IdempotencyClaim) -> None:
    guard = create_idempotency_guard(settings)
    try:
        await guard.release(claim)
    finally:
        await guard.aclose()


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


def _safe_archive_name(raw_name: str) -> str | None:
    name = raw_name.replace("\\", "/").strip("/")
    parts = [part for part in name.split("/") if part]
    if not parts or any(part == ".." for part in parts):
        return None
    if parts[0] == INTERNAL_ARCHIVE_DIR:
        return None
    return "/".join(parts)


def _raise_payload_too_large(detail: str) -> None:
    raise HTTPException(status_code=413, detail=detail)


def _setting_int(settings, name: str, default: int) -> int:
    value = getattr(settings, name, default)
    return value if isinstance(value, int) else default


def _validate_archive_limits(zip_bytes: bytes, settings) -> None:
    max_file_count = _setting_int(settings, "max_file_count", 5000)
    max_unzipped_bytes = _setting_int(settings, "max_unzipped_bytes", 1_073_741_824)
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            total_size = 0
            file_count = 0
            for entry in zf.infolist():
                if entry.filename.endswith("/"):
                    continue
                if _safe_archive_name(entry.filename) is None:
                    continue
                file_count += 1
                total_size += entry.file_size
                if file_count > max_file_count:
                    _raise_payload_too_large(
                        f"Too many files: {file_count} exceeds {max_file_count}"
                    )
                if total_size > max_unzipped_bytes:
                    _raise_payload_too_large(
                        f"Unzipped payload too large: {total_size} bytes exceeds "
                        f"{max_unzipped_bytes}"
                    )
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail=f"Invalid zip file: {exc}") from exc


def _extract_archive(zip_bytes: bytes, extract_dir: str, settings) -> None:
    _validate_archive_limits(zip_bytes, settings)
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        for entry in zf.infolist():
            if entry.filename.endswith("/"):
                continue
            archive_name = _safe_archive_name(entry.filename)
            if archive_name is None:
                continue
            safe_path = os.path.normpath(os.path.join(extract_dir, archive_name))
            if not safe_path.startswith(os.path.normpath(extract_dir) + os.sep):
                continue
            os.makedirs(os.path.dirname(safe_path), exist_ok=True)
            with open(safe_path, "wb") as out:
                out.write(zf.read(entry.filename))


def _folder_label(file_names: list[str]) -> str:
    first_parts = [
        parts[0]
        for name in file_names
        if (parts := [part for part in name.replace("\\", "/").split("/") if part])
    ]
    if first_parts and all(part == first_parts[0] for part in first_parts):
        return first_parts[0]
    return "folder-upload"


async def _prepare_folder_upload(
    uploads: list[UploadFile],
    settings,
    *,
    submission_label: str | None,
) -> PreparedArchive:
    if not uploads:
        raise HTTPException(status_code=400, detail="Folder upload contains no files")

    max_file_count = _setting_int(settings, "max_file_count", 5000)
    max_unzipped_bytes = _setting_int(settings, "max_unzipped_bytes", 1_073_741_824)
    max_zip_bytes = _setting_int(settings, "max_zip_bytes", 524_288_000)
    total_size = 0
    file_count = 0
    safe_entries: list[tuple[str, bytes]] = []
    raw_names: list[str] = []
    for upload in uploads:
        raw_name = upload.filename or ""
        archive_name = _safe_archive_name(raw_name)
        if archive_name is None:
            raise HTTPException(status_code=400, detail=f"Unsafe folder path: {raw_name!r}")
        data = await upload.read()
        file_count += 1
        total_size += len(data)
        if file_count > max_file_count:
            _raise_payload_too_large(
                f"Too many files: {file_count} exceeds {max_file_count}"
            )
        if total_size > max_unzipped_bytes:
            _raise_payload_too_large(
                f"Folder payload too large: {total_size} bytes exceeds "
                f"{max_unzipped_bytes}"
            )
        safe_entries.append((archive_name, data))
        raw_names.append(archive_name)

    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for archive_name, data in safe_entries:
            zf.writestr(archive_name, data)
    zip_bytes = archive_buffer.getvalue()
    if len(zip_bytes) > max_zip_bytes:
        _raise_payload_too_large(
            f"Generated archive too large: {len(zip_bytes)} bytes exceeds "
            f"{max_zip_bytes}"
        )
    label = submission_label or _folder_label(raw_names)
    return PreparedArchive(label=label, zip_bytes=zip_bytes)


@router.post("/tasks", status_code=201)
async def create_task(
    session: SessionDep,
    files: UploadedFiles = None,
    idempotency_key: FormString = None,
    submission_label: FormString = None,
):
    settings = get_settings()

    key = idempotency_key or str(uuid.uuid4())
    claim = await _acquire_idempotency_claim(settings, "create_task", key)

    try:
        existing = await _task_repo.get_by_idempotency_key(session, key)
        if existing is not None:
            from fastapi.responses import JSONResponse as _JSONResponse
            return _JSONResponse(
                status_code=200,
                content={"task_id": existing.id, "status": existing.status},
            )

        prepared = await _prepare_folder_upload(
            [upload for upload in files or [] if upload.filename],
            settings,
            submission_label=submission_label,
        )
        task = await _task_repo.create(
            session,
            idempotency_key=key,
            submission_label=prepared.label,
            status="draft",
        )
        task_id = task.id

        extract_dir = os.path.join(settings.task_dir_base, task_id)
        os.makedirs(extract_dir, exist_ok=True)

        original_zip_path = task_archive_path(extract_dir)
        os.makedirs(os.path.dirname(original_zip_path), exist_ok=True)
        with open(original_zip_path, "wb") as f:
            f.write(prepared.zip_bytes)

        _extract_archive(prepared.zip_bytes, extract_dir, settings)

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
    finally:
        await _release_idempotency_claim(settings, claim)


@router.post("/tasks/{task_id}/classify")
async def classify_task(
    task_id: str,
    session: SessionDep,
):
    settings = get_settings()

    task = await _task_repo.get(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")

    original_zip = find_task_archive_path(task.temp_dir)
    if not os.path.exists(original_zip):
        raise HTTPException(status_code=422, detail="original.zip not found in task temp_dir")

    try:
        profile = _load_profile(settings.classification_profile_path)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Profile not found: {settings.classification_profile_path}",
        ) from exc

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
    session: SessionDep,
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
    session: SessionDep,
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
    session: SessionDep,
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
    upload_claim = await _acquire_idempotency_claim(settings, "upload_task", task_id)
    delivery_backend = getattr(settings, "delivery_backend", "python")
    try:
        if delivery_backend == "go-worker":
            # Phase 2 bridge: the control plane owns classification and task state,
            # while the data plane only receives uploadable item specs.
            items = await _item_repo.list_by_task(session, task_id)
            upload_items = [
                item
                for item in items
                if item.severity in ("ok", "warning") and item.upload_status == "pending"
            ]
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
            source_ref = None
            try:
                if getattr(settings, "delivery_source_mode", "file") == "object":
                    source_ref = await stage_task_archive(
                        task,
                        bucket_name=getattr(settings, "staging_bucket_name", None),
                    )
                    await _event_repo.append(session, task_id, "task_staged_source", {
                        "type": source_ref.type,
                        "bucket": source_ref.bucket,
                        "key": source_ref.key,
                        "sha256": source_ref.sha256,
                        "size": source_ref.size,
                    })
                    await session.commit()

                message = build_delivery_task_message(
                    task=task,
                    upload_items=upload_items,
                    bucket_name=settings.s3_bucket_name,
                    source=source_ref,
                )
                await publisher.publish(message)
            except Exception:
                if source_ref is not None:
                    await delete_staged_archive(source_ref)
                    await _event_repo.append(session, task_id, "task_staged_source_deleted", {
                        "bucket": source_ref.bucket,
                        "key": source_ref.key,
                        "reason": "publish_failed",
                        "deleted_at": datetime.now(UTC).isoformat(),
                    })
                    await session.commit()
                raise

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
    finally:
        if delivery_backend == "go-worker":
            await _release_idempotency_claim(settings, upload_claim)
        elif not getattr(settings, "redis_idempotency_enabled", False):
            await _release_idempotency_claim(settings, upload_claim)


@router.post("/tasks/{task_id}/retry")
async def retry_task(
    task_id: str,
    session: SessionDep,
):
    task = await _task_repo.get(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")

    reset_count = await _item_repo.batch_reset_failed(session, task_id)
    if reset_count > 0 and task.status in {"failed", "partial_failed"}:
        task.status = "confirmed"
        task.finished_at = None
        await session.flush()
    await session.commit()
    return {"task_id": task_id, "reset_count": reset_count, "status": task.status}


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: str,
    session: SessionDep,
):
    task = await _task_repo.get(session, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found")
    return _task_to_dict(task)


@router.get("/tasks/{task_id}/progress")
async def task_progress(
    request: Request,
    task_id: str,
    bus: ProgressBusDep,
):
    async def event_stream():
        async with bus.subscribe(task_id) as queue:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if event is None:
                    break
                yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/tasks")
async def list_tasks(
    session: SessionDep,
    limit: int = 50,
    offset: int = 0,
):
    tasks = await _task_repo.list(session, limit=limit, offset=offset)
    return {"tasks": [_task_to_dict(t) for t in tasks], "limit": limit, "offset": offset}
