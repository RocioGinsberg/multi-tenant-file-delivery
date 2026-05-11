"""Task Runner — orchestrates classify → upload state machine for a single task.

Phase 2 note: the upload step (upload_file calls) will be replaced by Kafka
publish + Go worker consumption. The state machine structure and DB writes stay.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime

from app.core.db import async_session_maker
from app.core.settings import get_settings
from app.models import TaskItem
from app.repos.event_repo import EventRepo
from app.repos.item_repo import ItemRepo
from app.repos.task_repo import TaskRepo
from app.services.progress_bus import ProgressBus
from app.services.s3_uploader import upload_file

logger = logging.getLogger(__name__)

_task_repo = TaskRepo()
_item_repo = ItemRepo()
_event_repo = EventRepo()

# Module-level singleton; FastAPI lifespan wires this in via set_progress_bus().
_progress_bus: ProgressBus | None = None


def set_progress_bus(bus: ProgressBus) -> None:
    global _progress_bus
    _progress_bus = bus


def get_progress_bus() -> ProgressBus:
    if _progress_bus is None:
        raise RuntimeError("ProgressBus not initialised — call set_progress_bus() in lifespan")
    return _progress_bus


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _uploadable(item: TaskItem) -> bool:
    """Return True if the item should be uploaded (not error/ignored, pending)."""
    return item.severity in ("ok", "warning") and item.upload_status == "pending"


async def _upload_item(
    item: TaskItem,
    bucket: str,
    bus: ProgressBus,
    target_sem: asyncio.Semaphore,
    file_sem: asyncio.Semaphore,
) -> bool:
    """Upload a single item. Returns True on success, False on failure."""
    async with target_sem:
        async with file_sem:
            # Mark uploading
            async with async_session_maker() as write_session:
                await _item_repo.update_upload_status(
                    write_session, item.id, "uploading"
                )
                await write_session.commit()

            try:
                # Read file bytes from extracted temp dir
                import aiofiles  # noqa: PLC0415
                async with aiofiles.open(item.src_path, "rb") as f:
                    data = await f.read()

                settings = get_settings()
                bytes_uploaded = 0

                async def on_progress(n: int) -> None:
                    nonlocal bytes_uploaded
                    bytes_uploaded = n
                    await bus.publish(item.task_id, {
                        "type": "item_progress",
                        "item_id": item.id,
                        "filename": item.filename,
                        "bytes_uploaded": n,
                        "file_size": item.file_size,
                    })

                await upload_file(
                    data,
                    settings.s3_bucket_name,
                    item.dst_path,
                    content_type=_guess_content_type(item.ext),
                    on_progress=on_progress,
                )

                # Mark uploaded
                async with async_session_maker() as write_session:
                    await _item_repo.update_upload_status(
                        write_session,
                        item.id,
                        "uploaded",
                        uploaded_at=datetime.now(UTC),
                    )
                    await write_session.commit()

                await bus.publish(item.task_id, {
                    "type": "item_done",
                    "item_id": item.id,
                    "filename": item.filename,
                    "dst_path": item.dst_path,
                })
                return True

            except Exception as exc:
                logger.exception("Upload failed for item %s (%s)", item.id, item.src_path)
                async with async_session_maker() as write_session:
                    await _item_repo.update_upload_status(
                        write_session,
                        item.id,
                        "failed",
                        upload_error=str(exc)[:1000],
                    )
                    await write_session.commit()

                await bus.publish(item.task_id, {
                    "type": "item_failed",
                    "item_id": item.id,
                    "filename": item.filename,
                    "error": str(exc)[:200],
                })
                return False


def _guess_content_type(ext: str) -> str:
    return {
        ".pdf": "application/pdf",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls": "application/vnd.ms-excel",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".zip": "application/zip",
        ".csv": "text/csv",
        ".txt": "text/plain",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }.get(ext.lower(), "application/octet-stream")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run_task(task_id: str) -> None:
    """Orchestrate the upload state machine for a confirmed task.

    Called via FastAPI BackgroundTasks after user confirms classification.
    Any unhandled exception writes a task_failed event and sets task.status=failed
    so the UI can surface it.
    """
    bus = get_progress_bus()
    settings = get_settings()

    try:
        await _run_task_inner(task_id, bus, settings)
    except Exception as exc:
        logger.exception("run_task %s hit unhandled exception", task_id)
        try:
            async with async_session_maker() as session:
                await _task_repo.update_status(
                    session, task_id, "failed",
                    finished_at=datetime.now(UTC),
                )
                await _event_repo.append(session, task_id, "task_failed", {
                    "error": str(exc)[:500],
                })
                await session.commit()
        except Exception:
            logger.exception("run_task %s: failed to persist failure state", task_id)
        await bus.publish(task_id, {"type": "task_failed", "error": str(exc)[:200]})
        await bus.close_task(task_id)


async def _run_task_inner(task_id: str, bus: ProgressBus, settings) -> None:
    # ── Step 1: transition task → uploading ──────────────────────────────────
    async with async_session_maker() as session:
        task = await _task_repo.get(session, task_id)
        if task is None:
            raise ValueError(f"Task {task_id!r} not found")
        if task.status != "confirmed":
            raise ValueError(
                f"Task {task_id!r} is in status {task.status!r}, expected 'confirmed'"
            )

        await _task_repo.update_status(session, task_id, "uploading")
        await _event_repo.append(session, task_id, "upload_started", {
            "task_id": task_id,
        })
        await session.commit()

    await bus.publish(task_id, {"type": "task_started", "task_id": task_id})

    # ── Step 2: fetch uploadable items and group by target ───────────────────
    async with async_session_maker() as session:
        all_items = await _item_repo.list_by_task(session, task_id)

    uploadable = [i for i in all_items if _uploadable(i)]

    if not uploadable:
        # Nothing to upload (all errors/ignored) — mark done immediately
        async with async_session_maker() as session:
            await _task_repo.update_status(
                session, task_id, "uploaded",
                finished_at=datetime.now(UTC),
            )
            await _event_repo.append(session, task_id, "task_completed", {
                "uploaded": 0, "failed": 0, "skipped": len(all_items),
            })
            await session.commit()
        await bus.publish(task_id, {
            "type": "task_done",
            "uploaded": 0,
            "failed": 0,
            "total": len(all_items),
        })
        await bus.close_task(task_id)
        return

    grouped: dict[str, list[TaskItem]] = defaultdict(list)
    for item in uploadable:
        grouped[item.target_name_matched or "__unmatched__"].append(item)

    await bus.publish(task_id, {
        "type": "task_plan",
        "total_items": len(uploadable),
        "targets": list(grouped.keys()),
    })

    # ── Step 3: nested concurrent upload ─────────────────────────────────────
    target_sem = asyncio.Semaphore(settings.worker_max_target_concurrent)
    file_sem = asyncio.Semaphore(settings.worker_max_file_concurrent)

    async def upload_target_group(target: str, items: list[TaskItem]) -> list[bool]:
        tasks = [
            _upload_item(item, settings.s3_bucket_name, bus, target_sem, file_sem)
            for item in items
        ]
        return list(await asyncio.gather(*tasks, return_exceptions=False))

    all_results: list[bool] = []
    target_tasks = [
        upload_target_group(target, items)
        for target, items in grouped.items()
    ]
    for group_results in await asyncio.gather(*target_tasks, return_exceptions=True):
        if isinstance(group_results, Exception):
            logger.error("Target group upload raised: %s", group_results)
            # Count each item in the failed group as failed
            all_results.append(False)
        else:
            all_results.extend(group_results)

    # ── Step 4: finalize task status ─────────────────────────────────────────
    uploaded_count = sum(1 for r in all_results if r)
    failed_count = sum(1 for r in all_results if not r)

    final_status = "uploaded" if failed_count == 0 else "partial_failed"

    async with async_session_maker() as session:
        await _task_repo.update_status(
            session, task_id, final_status,
            finished_at=datetime.now(UTC),
        )
        await _event_repo.append(session, task_id, "task_completed", {
            "uploaded": uploaded_count,
            "failed": failed_count,
            "total": len(uploadable),
        })
        await session.commit()

    await bus.publish(task_id, {
        "type": "task_done",
        "status": final_status,
        "uploaded": uploaded_count,
        "failed": failed_count,
        "total": len(uploadable),
    })
    await bus.close_task(task_id)
