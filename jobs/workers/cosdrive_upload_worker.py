from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import OrderedDict
from pathlib import Path

from runner_shared.worker_state import portal_state

from jobs.cosdrive import smh as smh_svc
from jobs.workers.retry_utils import is_retryable_error


def _cfg_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _cfg_grouped_int(primary: str, fallback: str, default: int) -> int:
    if os.getenv(primary, "").strip():
        return _cfg_int(primary, default)
    if os.getenv(fallback, "").strip():
        return _cfg_int(fallback, default)
    return default


def run_cosdrive_task(
    task_id: str,
    *,
    worker_key: str = "cosdrive-upload-worker",
    queue_name: str = "cosdrive-upload",
    request_id: str = "",
    trace_id: str = "",
    prefect_flow_run_id: str = "",
    prefect_deployment_id: str = "",
) -> dict:
    task = portal_state.get_cosdrive_task(task_id)
    if not task:
        raise ValueError(f"cosdrive task not found: {task_id}")

    attempt = portal_state.start_cosdrive_attempt(
        task_id,
        worker_key=worker_key,
        queue_name=queue_name,
        request_id=request_id or task.get("request_id", ""),
        trace_id=trace_id or task.get("trace_id", ""),
        prefect_flow_run_id=prefect_flow_run_id,
        prefect_deployment_id=prefect_deployment_id,
    )
    attempt_id = attempt["attempt_id"]

    try:
        portal_state.append_cosdrive_event(
            task_id,
            attempt_id,
            event_type="attempt_heartbeat",
            request_id=request_id or task.get("request_id", ""),
            trace_id=trace_id or task.get("trace_id", ""),
            payload={"phase": "worker_started", "worker_key": worker_key},
        )
        pending = portal_state.list_cosdrive_delivery_records(task_id, delivery_status="pending")
        task_dir = Path(task.get("temp_dir", ""))
        task_dir_missing = not task_dir.exists()
        uploaded = 0
        failed = 0
        access_tokens: dict[str, str] = {}
        batch_size = max(1, _cfg_int("COSDRIVE_UPLOAD_BATCH_SIZE", 10))
        breaker_threshold = max(1, _cfg_int("COSDRIVE_BREAKER_FAILURE_THRESHOLD", 3))
        breaker_cooldown_ms = max(0, _cfg_int("COSDRIVE_BREAKER_COOLDOWN_MS", 1000))
        space_batch_size = max(1, _cfg_grouped_int("COSDRIVE_SPACE_BATCH_SIZE", "COSDRIVE_UPLOAD_BATCH_SIZE", 10))
        space_breaker_threshold = max(
            1,
            _cfg_grouped_int(
                "COSDRIVE_SPACE_BREAKER_FAILURE_THRESHOLD",
                "COSDRIVE_BREAKER_FAILURE_THRESHOLD",
                3,
            ),
        )
        space_breaker_cooldown_ms = max(
            0,
            _cfg_grouped_int(
                "COSDRIVE_SPACE_BREAKER_COOLDOWN_MS",
                "COSDRIVE_BREAKER_COOLDOWN_MS",
                1000,
            ),
        )
        space_probe_limit = max(1, _cfg_int("COSDRIVE_SPACE_BREAKER_PROBE_LIMIT", 1))
        consecutive_failures = 0
        breaker_opened = False
        per_space_breakers_opened = 0
        per_space_recoveries = 0
        space_groups: OrderedDict[str, list[dict]] = OrderedDict()
        for row in pending:
            space_groups.setdefault(row.get("team_space_id", "") or "__missing_space__", []).append(row)

        async def _upload_one(row: dict) -> None:
            nonlocal uploaded, failed, consecutive_failures, breaker_opened
            record_id = row["record_id"]
            relative_path = row.get("relative_path", "")
            file_path = task_dir / relative_path
            if task_dir_missing or not file_path.exists():
                portal_state.mark_cosdrive_delivery_record(
                    record_id,
                    delivery_status="failed",
                    error_code="local_file_missing",
                    error_message=(
                        f"local task temp_dir missing: {task_dir}"
                        if task_dir_missing
                        else f"local file missing: {relative_path}"
                    ),
                    external_receipt_json={"retryable": False, "phase": "local_cache"},
                )
                failed += 1
                consecutive_failures += 1
                return

            portal_state.mark_cosdrive_delivery_record(record_id, delivery_status="uploading")
            space_id = row.get("team_space_id", "")
            file_bytes = file_path.read_bytes()

            try:
                retry_no = 0
                while True:
                    retry_no += 1
                    try:
                        if space_id not in access_tokens:
                            access_tokens[space_id] = await smh_svc.get_access_token(space_id)
                        result = await smh_svc.upload_single_file(
                            space_id=space_id,
                            remote_path=row.get("drive_path", ""),
                            file_bytes=file_bytes,
                            filename=row.get("filename", file_path.name),
                            access_token=access_tokens[space_id],
                            request_id=request_id or task.get("request_id", ""),
                        )
                        break
                    except Exception as exc:
                        if retry_no >= 2 or not is_retryable_error(exc):
                            raise
                        portal_state.append_cosdrive_event(
                            task_id,
                            attempt_id,
                            event_type="attempt_retry_scheduled",
                            request_id=request_id or task.get("request_id", ""),
                            trace_id=trace_id or task.get("trace_id", ""),
                            payload={
                                "retry_no": retry_no,
                                "retryable": True,
                                "record_id": record_id,
                                "error_type": type(exc).__name__,
                                "error_message": str(exc),
                            },
                        )
            except Exception as exc:
                portal_state.mark_cosdrive_delivery_record(
                    record_id,
                    delivery_status="failed",
                    error_code=(
                        "smh_upload_retry_exhausted"
                        if is_retryable_error(exc)
                        else "smh_upload_failed"
                    ),
                    error_message=str(exc),
                    external_receipt_json={"retryable": is_retryable_error(exc)},
                )
                failed += 1
                consecutive_failures += 1
                return
            if result.get("ok"):
                portal_state.mark_cosdrive_delivery_record(
                    record_id,
                    delivery_status="delivered",
                    external_request_id=f"cosreq-{record_id}",
                    external_file_id=f"cosfile-{record_id}",
                    external_receipt_json={
                        "drive_path": row.get("drive_path", ""),
                        "team_name_matched": row.get("team_name_matched", ""),
                        "worker_key": worker_key,
                        "instant": bool(result.get("instant", False)),
                    },
                )
                uploaded += 1
                consecutive_failures = 0
            else:
                portal_state.mark_cosdrive_delivery_record(
                    record_id,
                    delivery_status="failed",
                    error_code=result.get("error_code", "smh_upload_failed"),
                    error_message=result.get("error", "unknown error"),
                    external_receipt_json={
                        "phase": result.get("phase", ""),
                        "retryable": bool(result.get("retryable", False)),
                        "status_code": int(result.get("status_code", 0) or 0),
                    },
                )
                failed += 1
                consecutive_failures += 1

        async def _run_batches_for_rows(rows: list[dict], *, batch_size_limit: int) -> bool:
            nonlocal breaker_opened, consecutive_failures
            for start in range(0, len(rows), batch_size_limit):
                batch = rows[start:start + batch_size_limit]
                await asyncio.gather(*[_upload_one(row) for row in batch])
                if consecutive_failures >= breaker_threshold:
                    breaker_opened = True
                    portal_state.append_cosdrive_event(
                        task_id,
                        attempt_id,
                        event_type="circuit_breaker_opened",
                        request_id=request_id or task.get("request_id", ""),
                        trace_id=trace_id or task.get("trace_id", ""),
                        payload={
                            "batch_start": start,
                            "batch_size": len(batch),
                            "consecutive_failures": consecutive_failures,
                            "cooldown_ms": breaker_cooldown_ms,
                        },
                    )
                    if breaker_cooldown_ms > 0:
                        await asyncio.sleep(breaker_cooldown_ms / 1000.0)
                    return False
            return True

        async def _run_by_space() -> None:
            nonlocal per_space_breakers_opened, per_space_recoveries, consecutive_failures, failed
            for space_id, rows in space_groups.items():
                if not rows:
                    continue
                idx = 0
                space_failures = 0
                while idx < len(rows):
                    batch = rows[idx:idx + space_batch_size]
                    if not await _run_batches_for_rows(batch, batch_size_limit=space_batch_size):
                        return
                    breaker_row_idx = None
                    for offset, row in enumerate(batch):
                        record = portal_state.get_cosdrive_delivery_record(row["record_id"])
                        if (record or {}).get("delivery_status") == "failed":
                            space_failures += 1
                        else:
                            space_failures = 0
                        if space_failures >= space_breaker_threshold:
                            breaker_row_idx = idx + offset
                            break
                    if breaker_row_idx is None:
                        idx += len(batch)
                        continue
                    if space_failures >= space_breaker_threshold:
                        per_space_breakers_opened += 1
                        normalized_space_id = "" if space_id == "__missing_space__" else space_id
                        portal_state.append_cosdrive_event(
                            task_id,
                            attempt_id,
                            event_type="space_circuit_breaker_opened",
                            request_id=request_id or task.get("request_id", ""),
                            trace_id=trace_id or task.get("trace_id", ""),
                            payload={
                                "team_space_id": normalized_space_id,
                                "consecutive_failures": space_failures,
                                "space_batch_size": space_batch_size,
                                "cooldown_ms": space_breaker_cooldown_ms,
                                "probe_limit": space_probe_limit,
                            },
                        )
                        tail_rows = []
                        for tail_row in rows[breaker_row_idx + 1:]:
                            record = portal_state.get_cosdrive_delivery_record(tail_row["record_id"])
                            if (record or {}).get("delivery_status") == "pending":
                                tail_rows.append(tail_row)
                        if not tail_rows:
                            break
                        if space_breaker_cooldown_ms > 0:
                            await asyncio.sleep(space_breaker_cooldown_ms / 1000.0)
                        probe_rows = tail_rows[:space_probe_limit]
                        portal_state.append_cosdrive_event(
                            task_id,
                            attempt_id,
                            event_type="space_circuit_breaker_half_open",
                            request_id=request_id or task.get("request_id", ""),
                            trace_id=trace_id or task.get("trace_id", ""),
                            payload={
                                "team_space_id": normalized_space_id,
                                "probe_count": len(probe_rows),
                            },
                        )
                        probe_uploaded_before = uploaded
                        probe_failed_before = failed
                        for probe_row in probe_rows:
                            await _upload_one(probe_row)
                        probe_success = uploaded > probe_uploaded_before and failed == probe_failed_before
                        if probe_success:
                            per_space_recoveries += 1
                            space_failures = 0
                            portal_state.append_cosdrive_event(
                                task_id,
                                attempt_id,
                                event_type="space_circuit_breaker_closed",
                                request_id=request_id or task.get("request_id", ""),
                                trace_id=trace_id or task.get("trace_id", ""),
                                payload={
                                    "team_space_id": normalized_space_id,
                                    "recovered_after_probe": True,
                                },
                            )
                            idx = breaker_row_idx + 1 + len(probe_rows)
                            continue
                        portal_state.append_cosdrive_event(
                            task_id,
                            attempt_id,
                            event_type="space_circuit_breaker_probe_failed",
                            request_id=request_id or task.get("request_id", ""),
                            trace_id=trace_id or task.get("trace_id", ""),
                            payload={
                                "team_space_id": normalized_space_id,
                                "probe_count": len(probe_rows),
                            },
                        )
                        for tail_row in tail_rows[space_probe_limit:]:
                            record = portal_state.get_cosdrive_delivery_record(tail_row["record_id"])
                            if (record or {}).get("delivery_status") != "pending":
                                continue
                            portal_state.mark_cosdrive_delivery_record(
                                tail_row["record_id"],
                                delivery_status="failed",
                                error_code="cosdrive_space_circuit_breaker_open",
                                error_message="per-space circuit breaker remained open after probe failure",
                                external_receipt_json={
                                    "retryable": True,
                                    "phase": "space_breaker",
                                    "team_space_id": normalized_space_id,
                                },
                            )
                            failed += 1
                        break
                    idx += len(batch)

        asyncio.run(_run_by_space())

        if breaker_opened:
            for row in pending:
                record = portal_state.get_cosdrive_delivery_record(row["record_id"])
                if (record or {}).get("delivery_status") != "pending":
                    continue
                portal_state.mark_cosdrive_delivery_record(
                    row["record_id"],
                    delivery_status="failed",
                    error_code="cosdrive_circuit_breaker_open",
                    error_message="circuit breaker opened after consecutive failures",
                    external_receipt_json={"retryable": True, "phase": "breaker"},
                )
                failed += 1

        task_status = "completed" if failed == 0 else "partial_failed"
        delivery_status = "completed" if failed == 0 else "partial_failed"
        attempt_status = "succeeded" if failed == 0 else "failed"
        summary = {
            "uploaded": uploaded,
            "failed": failed,
            "total": len(pending),
            "batch_size": batch_size,
            "space_batch_size": space_batch_size,
            "circuit_breaker_opened": breaker_opened,
            "space_circuit_breakers_opened": per_space_breakers_opened,
            "space_circuit_breaker_recoveries": per_space_recoveries,
            "space_count": len(space_groups),
        }
        portal_state.finish_cosdrive_attempt(
            task_id,
            attempt_id,
            final_status=attempt_status,
            task_status=task_status,
            delivery_status=delivery_status,
            request_id=request_id or task.get("request_id", ""),
            trace_id=trace_id or task.get("trace_id", ""),
            prefect_flow_run_id=prefect_flow_run_id,
            prefect_deployment_id=prefect_deployment_id,
            result_summary=summary,
        )
        return summary
    except Exception as exc:
        portal_state.finish_cosdrive_attempt(
            task_id,
            attempt_id,
            final_status="failed",
            task_status="failed",
            delivery_status="failed",
            request_id=request_id or task.get("request_id", ""),
            trace_id=trace_id or task.get("trace_id", ""),
            prefect_flow_run_id=prefect_flow_run_id,
            prefect_deployment_id=prefect_deployment_id,
            error_code=(
                "cosdrive_upload_retry_exhausted"
                if is_retryable_error(exc)
                else "cosdrive_upload_failed"
            ),
            error_message=str(exc),
            result_summary={"retryable": is_retryable_error(exc)},
        )
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobs.workers.cosdrive_upload_worker")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--worker-key", default="cosdrive-upload-worker")
    parser.add_argument("--queue-name", default="cosdrive-upload")
    parser.add_argument("--request-id", default="")
    parser.add_argument("--trace-id", default="")
    parser.add_argument("--prefect-flow-run-id", default="")
    parser.add_argument("--prefect-deployment-id", default="")
    args = parser.parse_args(argv or sys.argv[1:])

    run_cosdrive_task(
        args.task_id,
        worker_key=args.worker_key,
        queue_name=args.queue_name,
        request_id=args.request_id,
        trace_id=args.trace_id,
        prefect_flow_run_id=args.prefect_flow_run_id,
        prefect_deployment_id=args.prefect_deployment_id,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
