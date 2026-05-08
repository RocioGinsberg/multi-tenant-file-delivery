"""Portal state writers for execution-plane workers.

These helpers write business-truth task/attempt/event rows to portal_db.
Prefect remains orchestration only; worker-side state transitions must land in
execution/upload/cosdrive tables instead of relying on Prefect flow state.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import uuid
from typing import Any

from ..db import get_portal_connection


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _fetchone_dict(cur) -> dict | None:
    row = cur.fetchone()
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    columns = [desc[0] for desc in (cur.description or [])]
    return dict(zip(columns, row))


def _json(data: dict | list | None) -> str:
    return json.dumps(data or {}, ensure_ascii=False)


def _next_sequence(cur, table: str, task_id: str) -> int:
    cur.execute(f"SELECT COALESCE(MAX(sequence_no), 0) + 1 AS next_seq FROM {table} WHERE task_id = %s", (task_id,))
    row = _fetchone_dict(cur) or {}
    return int(row.get("next_seq", 1))


def cleanup_expired_artifacts(
    *,
    chat_ephemeral_days: int = 7,
    approval_snapshot_ephemeral_days: int = 7,
) -> dict[str, int]:
    chat_days = max(1, int(chat_ephemeral_days or 7))
    snapshot_days = max(1, int(approval_snapshot_ephemeral_days or 7))
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM chat_message
                WHERE retention_class = 'ephemeral'
                  AND created_at < now() - (%s || ' days')::interval
                """,
                (str(chat_days),),
            )
            deleted_chat_messages = int(getattr(cur, "rowcount", 0) or 0)

            cur.execute(
                """
                DELETE FROM approval_ai_snapshot
                WHERE retention_class = 'ephemeral'
                  AND created_at < now() - (%s || ' days')::interval
                """,
                (str(snapshot_days),),
            )
            deleted_approval_snapshots = int(getattr(cur, "rowcount", 0) or 0)

            cur.execute(
                """
                DELETE FROM chat_conversation
                WHERE retention_class = 'ephemeral'
                  AND updated_at < now() - (%s || ' days')::interval
                """,
                (str(chat_days),),
            )
            deleted_chat_conversations = int(getattr(cur, "rowcount", 0) or 0)
        conn.commit()
    return {
        "deleted_chat_messages": deleted_chat_messages,
        "deleted_approval_snapshots": deleted_approval_snapshots,
        "deleted_chat_conversations": deleted_chat_conversations,
    }


def _normalize_memory_row(row: dict | None) -> dict:
    if not row:
        return {}
    out = dict(row)
    for field, default in (
        ("detail_json", {}),
        ("evidence_refs_json", []),
        ("tags_json", []),
    ):
        value = out.get(field)
        if isinstance(value, str):
            try:
                out[field] = json.loads(value)
            except Exception:
                out[field] = default
        elif value is None:
            out[field] = default
    return out


def _as_utc(dt: Any) -> datetime | None:
    if not isinstance(dt, datetime):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _memory_cleanup_action(
    row: dict,
    *,
    standard_retention_days: int,
    extended_retention_days: int,
    archived_grace_days: int,
    now: datetime | None = None,
) -> str:
    current = _normalize_memory_row(row)
    memory_status = str(current.get("memory_status", "") or "")
    retention_class = str(current.get("retention_class", "") or "")
    current_now = now or datetime.now(timezone.utc)

    if retention_class not in {"standard", "extended"}:
        return ""
    if memory_status == "archived":
        archived_at = _as_utc(current.get("archived_at"))
        if archived_at and archived_at <= current_now - timedelta(days=int(archived_grace_days)):
            return "delete"
        return ""
    if memory_status != "active":
        return ""

    updated_at = _as_utc(current.get("updated_at"))
    if not updated_at:
        return ""
    threshold_days = int(standard_retention_days) if retention_class == "standard" else int(extended_retention_days)
    if updated_at <= current_now - timedelta(days=threshold_days):
        return "archive"
    return ""


def list_memory_retention_candidates(
    *,
    standard_retention_days: int = 30,
    extended_retention_days: int = 180,
    archived_grace_days: int = 30,
    limit: int = 200,
    scope_type: str = "",
    scope_key: str = "",
) -> list[dict]:
    standard_days = max(1, int(standard_retention_days or 30))
    extended_days = max(1, int(extended_retention_days or 180))
    grace_days = max(1, int(archived_grace_days or 30))
    scan_limit = max(1, min(int(limit or 200), 1000))
    normalized_scope_type = str(scope_type or "").strip().lower()
    normalized_scope_key = str(scope_key or "").strip()

    conditions = [
        "retention_class IN ('standard', 'extended')",
        """(
            (memory_status = 'active' AND (
                (retention_class = 'standard' AND updated_at < now() - (%s || ' days')::interval)
                OR
                (retention_class = 'extended' AND updated_at < now() - (%s || ' days')::interval)
            ))
            OR
            (memory_status = 'archived' AND archived_at IS NOT NULL AND archived_at < now() - (%s || ' days')::interval)
        )""",
    ]
    params: list[Any] = [str(standard_days), str(extended_days), str(grace_days)]
    if normalized_scope_type:
        conditions.append("scope_type = %s")
        params.append(normalized_scope_type)
    if normalized_scope_key:
        conditions.append("scope_key = %s")
        params.append(normalized_scope_key)
    params.append(scan_limit)

    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT *
                FROM chat_memory_projection
                WHERE {' AND '.join(conditions)}
                ORDER BY COALESCE(archived_at, updated_at) ASC, memory_id
                LIMIT %s
                """,
                tuple(params),
            )
            rows = cur.fetchall() or []
            if rows and not isinstance(rows[0], dict):
                columns = [desc[0] for desc in (cur.description or [])]
                rows = [dict(zip(columns, row)) for row in rows]

    now = datetime.now(timezone.utc)
    candidates: list[dict] = []
    for raw in rows:
        row = _normalize_memory_row(raw)
        cleanup_action = _memory_cleanup_action(
            row,
            standard_retention_days=standard_days,
            extended_retention_days=extended_days,
            archived_grace_days=grace_days,
            now=now,
        )
        if not cleanup_action:
            continue
        row["cleanup_action"] = cleanup_action
        row["threshold_days"] = (
            grace_days
            if cleanup_action == "delete"
            else (standard_days if str(row.get("retention_class", "")) == "standard" else extended_days)
        )
        candidates.append(row)
    return candidates


def cleanup_expired_memory_retention(
    *,
    standard_retention_days: int = 30,
    extended_retention_days: int = 180,
    archived_grace_days: int = 30,
    limit: int = 200,
    actor_user_id: str = "memory-retention-cleanup",
    request_id: str = "",
    trace_id: str = "",
) -> dict[str, Any]:
    standard_days = max(1, int(standard_retention_days or 30))
    extended_days = max(1, int(extended_retention_days or 180))
    grace_days = max(1, int(archived_grace_days or 30))
    scan_limit = max(1, min(int(limit or 200), 1000))

    preview = list_memory_retention_candidates(
        standard_retention_days=standard_days,
        extended_retention_days=extended_days,
        archived_grace_days=grace_days,
        limit=scan_limit,
    )
    if not preview:
        return {
            "cleanup_scope": "memory_retention",
            "standard_retention_days": standard_days,
            "extended_retention_days": extended_days,
            "archived_grace_days": grace_days,
            "limit": scan_limit,
            "candidates_count": 0,
            "processed_count": 0,
            "archived_count": 0,
            "deleted_count": 0,
            "memory_ids": [],
        }

    archived_count = 0
    deleted_count = 0
    processed_count = 0
    memory_ids: list[str] = []
    event_ids: list[str] = []
    now = datetime.now(timezone.utc)

    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            for candidate in preview:
                memory_id = str(candidate.get("memory_id", "") or "")
                if not memory_id:
                    continue
                cur.execute(
                    """
                    SELECT *
                    FROM chat_memory_projection
                    WHERE memory_id = %s
                    FOR UPDATE
                    """,
                    (memory_id,),
                )
                current = _normalize_memory_row(_fetchone_dict(cur))
                if not current:
                    continue
                cleanup_action = _memory_cleanup_action(
                    current,
                    standard_retention_days=standard_days,
                    extended_retention_days=extended_days,
                    archived_grace_days=grace_days,
                    now=now,
                )
                if not cleanup_action:
                    continue

                event_type = "deleted" if cleanup_action == "delete" else "archived"
                next_status = "deleted" if cleanup_action == "delete" else "archived"
                event_id = _gen_id("mev")
                event_note = f"retention_cleanup:{cleanup_action}"
                archived_at = current.get("archived_at")
                deleted_at = current.get("deleted_at")

                cur.execute(
                    """
                    INSERT INTO chat_memory_event (
                        event_id, memory_id, conversation_id, scope_type, scope_key,
                        memory_kind, event_type, memory_status, title, summary,
                        detail_json, evidence_refs_json, tags_json, retention_class,
                        request_id, trace_id, actor_user_id, event_note, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s::jsonb, %s::jsonb, %s::jsonb, %s,
                        %s, %s, %s, %s, now()
                    )
                    """,
                    (
                        event_id,
                        memory_id,
                        str(current.get("conversation_id", "") or ""),
                        str(current.get("scope_type", "") or ""),
                        str(current.get("scope_key", "") or ""),
                        str(current.get("memory_kind", "durable") or "durable"),
                        event_type,
                        next_status,
                        str(current.get("title", "") or ""),
                        str(current.get("summary", "") or ""),
                        _json(current.get("detail_json") or {}),
                        _json(current.get("evidence_refs_json") or []),
                        _json(current.get("tags_json") or []),
                        str(current.get("retention_class", "") or "standard"),
                        request_id,
                        trace_id,
                        actor_user_id,
                        event_note,
                    ),
                )
                cur.execute(
                    """
                    UPDATE chat_memory_projection
                    SET memory_status = %s,
                        latest_event_id = %s,
                        latest_event_type = %s,
                        latest_request_id = %s,
                        latest_trace_id = %s,
                        latest_actor_user_id = %s,
                        updated_by = %s,
                        updated_at = now(),
                        archived_at = CASE
                            WHEN %s = 'archived' THEN COALESCE(archived_at, now())
                            ELSE archived_at
                        END,
                        deleted_at = CASE
                            WHEN %s = 'deleted' THEN now()
                            ELSE deleted_at
                        END
                    WHERE memory_id = %s
                    """,
                    (
                        next_status,
                        event_id,
                        event_type,
                        request_id,
                        trace_id,
                        actor_user_id,
                        actor_user_id,
                        next_status,
                        next_status,
                        memory_id,
                    ),
                )
                processed_count += 1
                memory_ids.append(memory_id)
                event_ids.append(event_id)
                if cleanup_action == "delete":
                    deleted_count += 1
                else:
                    archived_count += 1
        conn.commit()

    return {
        "cleanup_scope": "memory_retention",
        "standard_retention_days": standard_days,
        "extended_retention_days": extended_days,
        "archived_grace_days": grace_days,
        "limit": scan_limit,
        "candidates_count": len(preview),
        "processed_count": processed_count,
        "archived_count": archived_count,
        "deleted_count": deleted_count,
        "memory_ids": memory_ids,
        "event_ids": event_ids,
    }


def _append_domain_event(
    *,
    event_table: str,
    event_prefix: str,
    task_id: str,
    attempt_id: str | None,
    event_type: str,
    from_status: str,
    to_status: str,
    request_id: str = "",
    trace_id: str = "",
    payload: dict | None = None,
) -> dict:
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            sequence_no = _next_sequence(cur, event_table, task_id)
            event_id = _gen_id(event_prefix)
            cur.execute(
                f"""
                INSERT INTO {event_table} (
                    event_id, task_id, attempt_id, sequence_no, event_type,
                    from_status, to_status, request_id, trace_id, payload_json
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                RETURNING *
                """,
                (
                    event_id,
                    task_id,
                    attempt_id or None,
                    sequence_no,
                    event_type,
                    from_status,
                    to_status,
                    request_id,
                    trace_id,
                    _json(payload),
                ),
            )
            row = _fetchone_dict(cur) or {}
        conn.commit()
    return row


# ---------------------------------------------------------------------------
# execution_*
# ---------------------------------------------------------------------------

def get_execution_task(task_id: str) -> dict | None:
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM execution_task WHERE task_id = %s", (task_id,))
            return _fetchone_dict(cur)


def get_latest_execution_attempt(task_id: str) -> dict | None:
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM execution_attempt
                WHERE task_id = %s
                ORDER BY attempt_no DESC, created_at DESC
                LIMIT 1
                """,
                (task_id,),
            )
            return _fetchone_dict(cur)


def get_latest_execution_heartbeat(task_id: str, attempt_id: str = "") -> dict | None:
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            if attempt_id:
                cur.execute(
                    """
                    SELECT *
                    FROM execution_event
                    WHERE task_id = %s
                      AND attempt_id = %s
                      AND event_type = 'attempt_heartbeat'
                    ORDER BY sequence_no DESC, created_at DESC
                    LIMIT 1
                    """,
                    (task_id, attempt_id),
                )
            else:
                cur.execute(
                    """
                    SELECT *
                    FROM execution_event
                    WHERE task_id = %s
                      AND event_type = 'attempt_heartbeat'
                    ORDER BY sequence_no DESC, created_at DESC
                    LIMIT 1
                    """,
                    (task_id,),
                )
            return _fetchone_dict(cur)


def list_execution_tasks_for_reconcile(
    *,
    task_domain: str = "",
    task_statuses: list[str] | None = None,
    limit: int = 100,
) -> list[dict]:
    task_statuses = [status for status in (task_statuses or []) if status]
    limit = max(1, min(int(limit or 100), 1000))
    where = ["1=1"]
    params: list[object] = []
    if task_domain:
        where.append("task_domain = %s")
        params.append(task_domain)
    if task_statuses:
        where.append("task_status = ANY(%s)")
        params.append(task_statuses)
    where_sql = " AND ".join(where)
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT task_id, task_domain, task_type, task_status, request_id, trace_id, updated_at
                FROM execution_task
                WHERE {where_sql}
                ORDER BY updated_at ASC, created_at ASC
                LIMIT %s
                """,
                tuple([*params, limit]),
            )
            return [dict(row) for row in (cur.fetchall() or [])]


def enqueue_execution_task(
    *,
    task_domain: str,
    task_type: str,
    idempotency_key: str,
    payload: dict | None = None,
    request_id: str = "",
    trace_id: str = "",
    source_event_id: str = "",
    source_system: str = "",
    created_by: str = "",
    subject_type: str = "",
    subject_id: str = "",
) -> dict:
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM execution_task
                WHERE task_domain = %s AND idempotency_key = %s
                LIMIT 1
                """,
                (task_domain, idempotency_key),
            )
            existing = _fetchone_dict(cur)
            if existing:
                if subject_type == "knowledge_asset" and subject_id:
                    _queue_knowledge_asset_projection(
                        cur,
                        asset_id=subject_id,
                        index_task_id=str(existing.get("task_id", "")),
                        source_object_id=str((payload or {}).get("object_id", "")),
                    )
                    conn.commit()
                return existing

            task_id = _gen_id("exec")
            cur.execute(
                """
                INSERT INTO execution_task (
                    task_id, task_domain, task_type, task_status,
                    idempotency_key, request_id, trace_id, source_event_id,
                    source_system, subject_type, subject_id, payload_json, created_by
                ) VALUES (%s,%s,%s,'queued',%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                RETURNING *
                """,
                (
                    task_id,
                    task_domain,
                    task_type,
                    idempotency_key,
                    request_id,
                    trace_id,
                    source_event_id,
                    source_system,
                    subject_type,
                    subject_id,
                    _json(payload),
                    created_by,
                ),
            )
            task = _fetchone_dict(cur) or {}

            sequence_no = _next_sequence(cur, "execution_event", task_id)
            event_id = _gen_id("exee")
            cur.execute(
                """
                INSERT INTO execution_event (
                    event_id, task_id, attempt_id, sequence_no, event_type,
                    from_status, to_status, request_id, trace_id, source_event_id, payload_json
                ) VALUES (%s,%s,NULL,%s,'task_queued','pending','queued',%s,%s,%s,%s::jsonb)
                """,
                (
                    event_id,
                    task_id,
                    sequence_no,
                    request_id,
                    trace_id,
                    source_event_id,
                    _json(payload),
                ),
            )

            cur.execute(
                """
                INSERT INTO execution_projection (
                    task_id, current_status, current_attempt_id, current_attempt_no,
                    last_event_id, last_event_type, last_worker_key, prefect_flow_run_id,
                    projection_version, updated_at
                ) VALUES (%s,'queued','',0,%s,'task_queued','','',1,now())
                ON CONFLICT (task_id) DO UPDATE SET
                    current_status = EXCLUDED.current_status,
                    last_event_id = EXCLUDED.last_event_id,
                    last_event_type = EXCLUDED.last_event_type,
                    projection_version = execution_projection.projection_version + 1,
                    updated_at = now()
                """,
                (task_id, event_id),
            )
            if task.get("subject_type") == "knowledge_asset" and task.get("subject_id"):
                _queue_knowledge_asset_projection(
                    cur,
                    asset_id=str(task.get("subject_id", "")),
                    index_task_id=task_id,
                    source_object_id=str((payload or {}).get("object_id", "")),
                )
        conn.commit()
    return task


def start_execution_attempt(
    task_id: str,
    *,
    worker_domain: str,
    worker_key: str,
    queue_name: str = "",
    request_id: str = "",
    trace_id: str = "",
    prefect_flow_run_id: str = "",
    prefect_task_run_id: str = "",
    prefect_deployment_id: str = "",
) -> dict:
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COALESCE(current_attempt_no, 0) + 1 AS next_attempt,
                    task_status,
                    subject_type,
                    subject_id
                FROM execution_task
                WHERE task_id = %s
                """,
                (task_id,),
            )
            base = _fetchone_dict(cur)
            if not base:
                raise ValueError(f"execution task not found: {task_id}")

            attempt_no = int(base["next_attempt"])
            attempt_id = _gen_id("exea")
            from_status = base.get("task_status", "")

            cur.execute(
                """
                INSERT INTO execution_attempt (
                    attempt_id, task_id, attempt_no, attempt_status,
                    worker_domain, worker_key, queue_name,
                    request_id, trace_id, prefect_flow_run_id, prefect_task_run_id, started_at
                ) VALUES (%s,%s,%s,'running',%s,%s,%s,%s,%s,%s,%s,now())
                RETURNING *
                """,
                (
                    attempt_id,
                    task_id,
                    attempt_no,
                    worker_domain,
                    worker_key,
                    queue_name,
                    request_id,
                    trace_id,
                    prefect_flow_run_id,
                    prefect_task_run_id,
                ),
            )
            attempt = _fetchone_dict(cur) or {}

            cur.execute(
                """
                UPDATE execution_task
                SET task_status = 'running',
                    current_attempt_no = %s,
                    prefect_deployment_id = %s,
                    prefect_flow_run_id = %s,
                    request_id = CASE WHEN request_id = '' THEN %s ELSE request_id END,
                    trace_id = CASE WHEN trace_id = '' THEN %s ELSE trace_id END,
                    updated_at = now()
                WHERE task_id = %s
                """,
                (attempt_no, prefect_deployment_id, prefect_flow_run_id, request_id, trace_id, task_id),
            )

            sequence_no = _next_sequence(cur, "execution_event", task_id)
            event_id = _gen_id("exee")
            cur.execute(
                """
                INSERT INTO execution_event (
                    event_id, task_id, attempt_id, sequence_no, event_type,
                    from_status, to_status, request_id, trace_id, payload_json
                ) VALUES (%s,%s,%s,%s,'attempt_started',%s,'running',%s,%s,%s::jsonb)
                """,
                (
                    event_id,
                    task_id,
                    attempt_id,
                    sequence_no,
                    from_status,
                    request_id,
                    trace_id,
                    _json({"worker_domain": worker_domain, "worker_key": worker_key}),
                ),
            )

            cur.execute(
                """
                INSERT INTO execution_projection (
                    task_id, current_status, current_attempt_id, current_attempt_no,
                    last_event_id, last_event_type, last_worker_key, prefect_flow_run_id,
                    projection_version, updated_at
                ) VALUES (%s,'running',%s,%s,%s,'attempt_started',%s,%s,1,now())
                ON CONFLICT (task_id) DO UPDATE SET
                    current_status = EXCLUDED.current_status,
                    current_attempt_id = EXCLUDED.current_attempt_id,
                    current_attempt_no = EXCLUDED.current_attempt_no,
                    last_event_id = EXCLUDED.last_event_id,
                    last_event_type = EXCLUDED.last_event_type,
                    last_worker_key = EXCLUDED.last_worker_key,
                    prefect_flow_run_id = EXCLUDED.prefect_flow_run_id,
                    projection_version = execution_projection.projection_version + 1,
                    updated_at = now()
                """,
                (task_id, attempt_id, attempt_no, event_id, worker_key, prefect_flow_run_id),
            )
            if base.get("subject_type") == "knowledge_asset" and base.get("subject_id"):
                _mark_knowledge_asset_projection_running(
                    cur,
                    asset_id=str(base.get("subject_id", "")),
                    index_task_id=task_id,
                )
        conn.commit()
    return attempt


def finish_execution_attempt(
    task_id: str,
    attempt_id: str,
    *,
    final_status: str,
    request_id: str = "",
    trace_id: str = "",
    result_summary: dict | None = None,
    error_code: str = "",
    error_message: str = "",
) -> dict:
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT task_status, current_attempt_no FROM execution_task WHERE task_id = %s", (task_id,))
            task = _fetchone_dict(cur)
            if not task:
                raise ValueError(f"execution task not found: {task_id}")
            cur.execute(
                """
                UPDATE execution_attempt
                SET attempt_status = %s,
                    finished_at = now(),
                    error_code = %s,
                    error_message = %s,
                    metrics_json = %s::jsonb
                WHERE attempt_id = %s
                RETURNING *
                """,
                (final_status, error_code, error_message, _json(result_summary), attempt_id),
            )
            attempt = _fetchone_dict(cur) or {}

            task_status = "succeeded" if final_status == "succeeded" else "failed"
            cur.execute(
                """
                UPDATE execution_task
                SET task_status = %s,
                    result_summary_json = %s::jsonb,
                    completed_at = CASE WHEN %s IN ('succeeded','failed') THEN now() ELSE completed_at END,
                    updated_at = now(),
                    last_error_code = %s,
                    last_error_message = %s
                WHERE task_id = %s
                RETURNING *
                """,
                (task_status, _json(result_summary), task_status, error_code, error_message, task_id),
            )
            task_row = _fetchone_dict(cur) or {}

            sequence_no = _next_sequence(cur, "execution_event", task_id)
            event_id = _gen_id("exee")
            cur.execute(
                """
                INSERT INTO execution_event (
                    event_id, task_id, attempt_id, sequence_no, event_type,
                    from_status, to_status, request_id, trace_id, payload_json
                ) VALUES (%s,%s,%s,%s,'attempt_finished',%s,%s,%s,%s,%s::jsonb)
                """,
                (
                    event_id,
                    task_id,
                    attempt_id,
                    sequence_no,
                    task.get("task_status", ""),
                    task_status,
                    request_id,
                    trace_id,
                    _json(result_summary if not error_message else {"error_code": error_code, "error_message": error_message, **(result_summary or {})}),
                ),
            )
            cur.execute(
                """
                UPDATE execution_projection
                SET current_status = %s,
                    last_event_id = %s,
                    last_event_type = 'attempt_finished',
                    last_error_code = %s,
                    last_error_message = %s,
                    projection_version = projection_version + 1,
                    updated_at = now()
                WHERE task_id = %s
                """,
                (task_status, event_id, error_code, error_message, task_id),
            )

            if task_row.get("subject_type") == "knowledge_asset" and task_row.get("subject_id"):
                _upsert_knowledge_asset_projection(
                    cur,
                    asset_id=str(task_row.get("subject_id", "")),
                    index_task_id=task_id,
                    execution_status=task_status,
                    result_summary=result_summary or {},
                    error_code=error_code,
                    error_message=error_message,
                )
        conn.commit()
    return {"task": task_row, "attempt": attempt}


def append_execution_event(
    task_id: str,
    attempt_id: str,
    *,
    event_type: str,
    from_status: str = "running",
    to_status: str = "running",
    request_id: str = "",
    trace_id: str = "",
    payload: dict | None = None,
) -> dict:
    return _append_domain_event(
        event_table="execution_event",
        event_prefix="exee",
        task_id=task_id,
        attempt_id=attempt_id,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        request_id=request_id,
        trace_id=trace_id,
        payload=payload,
    )


def _map_execution_to_knowledge_status(status: str) -> str:
    return {
        "pending": "pending",
        "queued": "queued",
        "dispatched": "queued",
        "running": "indexing",
        "waiting_retry": "indexing",
        "succeeded": "indexed",
        "reconciled": "indexed",
        "partial_failed": "partial_failed",
        "failed": "failed",
        "cancelled": "cancelled",
    }.get(status or "", status or "pending")


def _knowledge_status_label(status: str) -> str:
    return {
        "pending": "待索引",
        "queued": "待索引",
        "indexing": "索引中",
        "indexed": "已索引",
        "partial_failed": "部分失败",
        "failed": "索引失败",
        "cancelled": "已取消",
    }.get(status, status)


def _map_knowledge_error_code(error_code: str) -> str:
    raw = (error_code or "").strip()
    if not raw:
        return ""
    base = raw.removesuffix("_retry_exhausted")
    mapping = {
        "knowledge_index_subject_missing_upload_task_id": "subject_missing_upload_task_id",
        "knowledge_asset_missing_object_ref": "asset_missing_object_ref",
        "knowledge_upload_task_not_found": "upload_task_not_found",
        "knowledge_upload_task_not_knowledge": "upload_task_not_knowledge",
        "knowledge_upload_task_missing_object_ref": "upload_task_missing_object_ref",
        "knowledge_ingest_timeout": "ingest_timeout",
        "knowledge_ingest_request_failed": "ingest_request_failed",
        "knowledge_ingest_service_unavailable": "ingest_service_unavailable",
        "knowledge_ingest_rejected": "ingest_rejected",
        "knowledge_ingest_invalid_response": "ingest_invalid_response",
    }
    if base in mapping:
        return mapping[base]
    if base.startswith("knowledge_"):
        return base.removeprefix("knowledge_")
    return "unknown"


def _upsert_knowledge_asset_projection(
    cur,
    *,
    asset_id: str,
    index_task_id: str,
    execution_status: str,
    result_summary: dict | None = None,
    error_code: str = "",
    error_message: str = "",
) -> None:
    mapped_status = _map_execution_to_knowledge_status(execution_status)
    mapped_error_code = _map_knowledge_error_code(error_code)
    summary = result_summary or {}
    chunk_count = int(summary.get("chunk_count", 0) or 0)
    source_object_id = str(summary.get("doc_id", "") or summary.get("object_id", ""))
    indexed_at_value = "now" if mapped_status == "indexed" else ""
    cur.execute(
        """
        INSERT INTO knowledge_asset_projection (
            asset_id, current_index_task_id, current_status, current_status_label,
            indexed_at, chunk_count, last_error_code, last_error_message,
            last_source_object_id, last_attempt_at, projection_version
        ) VALUES (
            %s,%s,%s,%s,
            CASE WHEN %s = 'now' THEN now() ELSE NULL END,
            %s,%s,%s,%s,now(),1
        )
        ON CONFLICT (asset_id) DO UPDATE SET
            current_index_task_id = EXCLUDED.current_index_task_id,
            current_status = EXCLUDED.current_status,
            current_status_label = EXCLUDED.current_status_label,
            indexed_at = CASE
                WHEN EXCLUDED.indexed_at IS NULL THEN knowledge_asset_projection.indexed_at
                ELSE EXCLUDED.indexed_at
            END,
            chunk_count = EXCLUDED.chunk_count,
            last_error_code = EXCLUDED.last_error_code,
            last_error_message = EXCLUDED.last_error_message,
            last_source_object_id = EXCLUDED.last_source_object_id,
            last_attempt_at = now(),
            projection_version = knowledge_asset_projection.projection_version + 1,
            updated_at = now()
        """,
        (
            asset_id,
            index_task_id,
            mapped_status,
            _knowledge_status_label(mapped_status),
            indexed_at_value,
            chunk_count,
            mapped_error_code,
            error_message,
            source_object_id,
        ),
    )


def _queue_knowledge_asset_projection(
    cur,
    *,
    asset_id: str,
    index_task_id: str = "",
    source_object_id: str = "",
) -> None:
    if not asset_id:
        return
    cur.execute(
        """
        INSERT INTO knowledge_asset_projection (
            asset_id, current_index_task_id, current_status, current_status_label,
            indexed_at, chunk_count, last_error_code, last_error_message,
            last_source_object_id, projection_version
        ) VALUES (
            %s,%s,'queued','待索引',
            NULL,0,'','',%s,1
        )
        ON CONFLICT (asset_id) DO UPDATE SET
            current_index_task_id = EXCLUDED.current_index_task_id,
            current_status = 'queued',
            current_status_label = '待索引',
            indexed_at = NULL,
            chunk_count = 0,
            last_error_code = '',
            last_error_message = '',
            last_source_object_id = CASE
                WHEN EXCLUDED.last_source_object_id = '' THEN knowledge_asset_projection.last_source_object_id
                ELSE EXCLUDED.last_source_object_id
            END,
            projection_version = knowledge_asset_projection.projection_version + 1,
            updated_at = now()
        """,
        (
            asset_id,
            index_task_id,
            source_object_id,
        ),
    )


def _mark_knowledge_asset_projection_running(
    cur,
    *,
    asset_id: str,
    index_task_id: str,
) -> None:
    if not asset_id:
        return
    cur.execute(
        """
        INSERT INTO knowledge_asset_projection (
            asset_id, current_index_task_id, current_status, current_status_label,
            last_error_code, last_error_message, projection_version
        ) VALUES (
            %s,%s,'indexing','索引中',
            '','',1
        )
        ON CONFLICT (asset_id) DO UPDATE SET
            current_index_task_id = EXCLUDED.current_index_task_id,
            current_status = 'indexing',
            current_status_label = '索引中',
            last_error_code = '',
            last_error_message = '',
            projection_version = knowledge_asset_projection.projection_version + 1,
            updated_at = now()
        """,
        (
            asset_id,
            index_task_id,
        ),
    )


def get_knowledge_asset(asset_id: str) -> dict | None:
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM knowledge_asset WHERE asset_id = %s", (asset_id,))
            return _fetchone_dict(cur)


def get_knowledge_asset_by_upload_task(upload_task_id: str) -> dict | None:
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM knowledge_asset
                WHERE latest_upload_task_id = %s
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """,
                (upload_task_id,),
            )
            return _fetchone_dict(cur)


def upsert_knowledge_asset_from_upload(task: dict, uploaded_object: dict, binding: dict) -> dict:
    metadata = task.get("metadata_json") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}

    dataset_code = str(task.get("dataset_code", "") or "")
    asset_key = str(metadata.get("asset_key", "") or "").strip()
    if not asset_key:
        raise ValueError("knowledge asset_key missing")
    upload_task_id = str(task.get("task_id", ""))
    object_id = str(uploaded_object.get("object_id", ""))
    checksum = str(uploaded_object.get("checksum", "") or task.get("checksum", ""))
    idempotency_key = f"knowledge-asset:{dataset_code}:{asset_key}:{object_id or upload_task_id}"
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT asset_id
                FROM knowledge_asset
                WHERE dataset_code = %s
                  AND asset_key = %s
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """,
                (dataset_code, asset_key),
            )
            existing = _fetchone_dict(cur)
            asset_id = str((existing or {}).get("asset_id", "") or _gen_id("kna"))
            cur.execute(
                """
                INSERT INTO knowledge_asset (
                    asset_id, dataset_code, dataset_id, asset_key, asset_type,
                    doc_title, doc_source, latest_upload_task_id, latest_object_id,
                    latest_binding_id, bucket_name, object_key, checksum,
                    content_type, request_id, trace_id, source_event_id,
                    idempotency_key, created_by, metadata_json
                ) VALUES (
                    %s,%s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s::jsonb
                )
                ON CONFLICT (asset_id) DO UPDATE SET
                    dataset_code = EXCLUDED.dataset_code,
                    dataset_id = EXCLUDED.dataset_id,
                    asset_key = EXCLUDED.asset_key,
                    asset_type = EXCLUDED.asset_type,
                    doc_title = EXCLUDED.doc_title,
                    doc_source = EXCLUDED.doc_source,
                    latest_upload_task_id = EXCLUDED.latest_upload_task_id,
                    latest_object_id = EXCLUDED.latest_object_id,
                    latest_binding_id = EXCLUDED.latest_binding_id,
                    bucket_name = EXCLUDED.bucket_name,
                    object_key = EXCLUDED.object_key,
                    checksum = EXCLUDED.checksum,
                    content_type = EXCLUDED.content_type,
                    request_id = EXCLUDED.request_id,
                    trace_id = EXCLUDED.trace_id,
                    source_event_id = EXCLUDED.source_event_id,
                    created_by = EXCLUDED.created_by,
                    metadata_json = EXCLUDED.metadata_json,
                    updated_at = now()
                RETURNING *
                """,
                (
                    asset_id,
                    dataset_code,
                    task.get("dataset_id", ""),
                    asset_key,
                    task.get("asset_type", "") or metadata.get("asset_type", ""),
                    metadata.get("doc_title", ""),
                    metadata.get("doc_source", ""),
                    upload_task_id,
                    object_id,
                    binding.get("binding_id", ""),
                    task.get("bucket_name", ""),
                    task.get("object_key", ""),
                    checksum,
                    task.get("content_type", ""),
                    task.get("request_id", ""),
                    task.get("trace_id", ""),
                    upload_task_id,
                    idempotency_key,
                    task.get("requested_by", "") or task.get("upload_user", ""),
                    _json(metadata),
                ),
            )
            asset = _fetchone_dict(cur) or {}

            _queue_knowledge_asset_projection(
                cur,
                asset_id=asset_id,
                source_object_id=object_id,
            )
        conn.commit()
    return asset


# ---------------------------------------------------------------------------
# upload_*
# ---------------------------------------------------------------------------

def get_upload_task(task_id: str) -> dict | None:
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM upload_task WHERE task_id = %s", (task_id,))
            return _fetchone_dict(cur)


def get_latest_upload_attempt(task_id: str) -> dict | None:
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM upload_attempt
                WHERE task_id = %s
                ORDER BY attempt_no DESC, created_at DESC
                LIMIT 1
                """,
                (task_id,),
            )
            return _fetchone_dict(cur)


def get_latest_upload_heartbeat(task_id: str, attempt_id: str = "") -> dict | None:
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            if attempt_id:
                cur.execute(
                    """
                    SELECT *
                    FROM upload_event
                    WHERE task_id = %s
                      AND attempt_id = %s
                      AND event_type = 'attempt_heartbeat'
                    ORDER BY sequence_no DESC, created_at DESC
                    LIMIT 1
                    """,
                    (task_id, attempt_id),
                )
            else:
                cur.execute(
                    """
                    SELECT *
                    FROM upload_event
                    WHERE task_id = %s
                      AND event_type = 'attempt_heartbeat'
                    ORDER BY sequence_no DESC, created_at DESC
                    LIMIT 1
                    """,
                    (task_id,),
                )
            return _fetchone_dict(cur)


def start_upload_attempt(
    task_id: str,
    *,
    worker_key: str,
    request_id: str = "",
    trace_id: str = "",
    prefect_flow_run_id: str = "",
    prefect_deployment_id: str = "",
) -> dict:
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(current_attempt_no, 0) + 1 AS next_attempt, task_status, object_key FROM upload_task WHERE task_id = %s",
                (task_id,),
            )
            task = _fetchone_dict(cur)
            if not task:
                raise ValueError(f"upload task not found: {task_id}")
            attempt_id = _gen_id("upla")
            attempt_no = int(task["next_attempt"])
            cur.execute(
                """
                INSERT INTO upload_attempt (
                    attempt_id, task_id, attempt_no, attempt_status,
                    worker_key, request_id, trace_id, object_key, started_at
                ) VALUES (%s,%s,%s,'running',%s,%s,%s,%s,now())
                RETURNING *
                """,
                (attempt_id, task_id, attempt_no, worker_key, request_id, trace_id, task.get("object_key", "")),
            )
            attempt = _fetchone_dict(cur) or {}
            cur.execute(
                """
                UPDATE upload_task
                SET task_status = 'binding',
                    binding_status = 'binding',
                    current_attempt_no = %s,
                    updated_at = now()
                WHERE task_id = %s
                """,
                (attempt_no, task_id),
            )
            sequence_no = _next_sequence(cur, "upload_event", task_id)
            cur.execute(
                """
                INSERT INTO upload_event (
                    event_id, task_id, attempt_id, sequence_no, event_type,
                    from_status, to_status, request_id, trace_id, payload_json
                ) VALUES (%s,%s,%s,%s,'binding_started',%s,'binding',%s,%s,%s::jsonb)
                """,
                (
                    _gen_id("uple"),
                    task_id,
                    attempt_id,
                    sequence_no,
                    task.get("task_status", ""),
                    request_id,
                    trace_id,
                    _json(
                        {
                            "worker_key": worker_key,
                            "prefect_flow_run_id": prefect_flow_run_id,
                            "prefect_deployment_id": prefect_deployment_id,
                        }
                    ),
                ),
            )
        conn.commit()
    return attempt


def create_uploaded_object(task: dict, attempt_id: str) -> dict:
    object_id = _gen_id("obj")
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM uploaded_object
                WHERE bucket_name = %s AND object_key = %s
                LIMIT 1
                """,
                (task.get("bucket_name", ""), task.get("object_key", "")),
            )
            row = _fetchone_dict(cur)
            if row:
                object_id = str(row.get("object_id", "") or object_id)
            else:
                cur.execute(
                    """
                    INSERT INTO uploaded_object (
                        object_id, task_id, attempt_id, bucket_type, bucket_name,
                        object_key, checksum, content_type, file_size, metadata_json
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                    RETURNING *
                    """,
                    (
                        object_id,
                        task["task_id"],
                        attempt_id,
                        task.get("bucket_type", "data"),
                        task.get("bucket_name", ""),
                        task.get("object_key", ""),
                        task.get("checksum", ""),
                        task.get("content_type", ""),
                        task.get("file_size", 0),
                        _json(task.get("metadata_json") or {}),
                    ),
                )
                row = _fetchone_dict(cur) or {}
            cur.execute(
                "UPDATE upload_task SET latest_object_id = %s, updated_at = now() WHERE task_id = %s",
                (object_id, task["task_id"]),
            )
        conn.commit()
    return row or {"object_id": object_id}


def bind_uploaded_object(task: dict, object_id: str) -> dict:
    binding_id = _gen_id("bind")
    if task.get("upload_kind") == "knowledge":
        bind_type = "knowledge_dataset"
        bind_key = task.get("dataset_code", "")
    else:
        bind_type = "etl_dataset"
        bind_key = task.get("dataset_code", "")
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM asset_binding
                WHERE object_id = %s AND bind_type = %s AND bind_key = %s
                LIMIT 1
                """,
                (object_id, bind_type, bind_key),
            )
            row = _fetchone_dict(cur)
            if not row:
                cur.execute(
                    """
                    INSERT INTO asset_binding (
                        binding_id, task_id, object_id, binding_status,
                        bind_type, bind_key, request_id, trace_id, bound_at
                    ) VALUES (%s,%s,%s,'bound',%s,%s,%s,%s,now())
                    RETURNING *
                    """,
                    (
                        binding_id,
                        task["task_id"],
                        object_id,
                        bind_type,
                        bind_key,
                        task.get("request_id", ""),
                        task.get("trace_id", ""),
                    ),
                )
                row = _fetchone_dict(cur) or {}
        conn.commit()
    return row or {"binding_id": binding_id, "bind_type": bind_type}


def finish_upload_attempt(
    task_id: str,
    attempt_id: str,
    *,
    final_status: str,
    request_id: str = "",
    trace_id: str = "",
    result_summary: dict | None = None,
    error_code: str = "",
    error_message: str = "",
    prefect_flow_run_id: str = "",
    prefect_deployment_id: str = "",
) -> dict:
    task_status = "completed" if final_status == "uploaded" else "failed"
    binding_status = "bound" if task_status == "completed" else "failed"
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE upload_attempt
                SET attempt_status = %s, finished_at = now(), error_code = %s, error_message = %s
                WHERE attempt_id = %s
                RETURNING *
                """,
                (final_status, error_code, error_message, attempt_id),
            )
            attempt = _fetchone_dict(cur) or {}
            cur.execute(
                """
                UPDATE upload_task
                SET task_status = %s,
                    binding_status = %s,
                    completed_at = CASE WHEN %s = 'completed' THEN now() ELSE completed_at END,
                    updated_at = now(),
                    last_error_code = %s,
                    last_error_message = %s
                WHERE task_id = %s
                RETURNING *
                """,
                (task_status, binding_status, task_status, error_code, error_message, task_id),
            )
            task = _fetchone_dict(cur) or {}
            sequence_no = _next_sequence(cur, "upload_event", task_id)
            cur.execute(
                """
                INSERT INTO upload_event (
                    event_id, task_id, attempt_id, sequence_no, event_type,
                    from_status, to_status, request_id, trace_id, payload_json
                ) VALUES (%s,%s,%s,%s,'binding_finished','binding',%s,%s,%s,%s::jsonb)
                """,
                (
                    _gen_id("uple"),
                    task_id,
                    attempt_id,
                    sequence_no,
                    task_status,
                    request_id,
                    trace_id,
                    _json(
                        {
                            "prefect_flow_run_id": prefect_flow_run_id,
                            "prefect_deployment_id": prefect_deployment_id,
                            **(
                                result_summary
                                if not error_message
                                else {"error_code": error_code, "error_message": error_message}
                            ),
                        }
                    ),
                ),
            )
        conn.commit()
    return {"task": task, "attempt": attempt}


def append_upload_event(
    task_id: str,
    attempt_id: str | None,
    *,
    event_type: str,
    from_status: str = "binding",
    to_status: str = "binding",
    request_id: str = "",
    trace_id: str = "",
    payload: dict | None = None,
) -> dict:
    return _append_domain_event(
        event_table="upload_event",
        event_prefix="uple",
        task_id=task_id,
        attempt_id=attempt_id,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        request_id=request_id,
        trace_id=trace_id,
        payload=payload,
    )


# ---------------------------------------------------------------------------
# cosdrive_upload_*
# ---------------------------------------------------------------------------

def get_cosdrive_task(task_id: str) -> dict | None:
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM cosdrive_upload_task WHERE task_id = %s", (task_id,))
            return _fetchone_dict(cur)


def get_latest_cosdrive_attempt(task_id: str) -> dict | None:
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM cosdrive_upload_attempt
                WHERE task_id = %s
                ORDER BY attempt_no DESC, created_at DESC
                LIMIT 1
                """,
                (task_id,),
            )
            return _fetchone_dict(cur)


def get_latest_cosdrive_heartbeat(task_id: str, attempt_id: str = "") -> dict | None:
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            if attempt_id:
                cur.execute(
                    """
                    SELECT *
                    FROM cosdrive_upload_event
                    WHERE task_id = %s
                      AND attempt_id = %s
                      AND event_type = 'attempt_heartbeat'
                    ORDER BY sequence_no DESC, created_at DESC
                    LIMIT 1
                    """,
                    (task_id, attempt_id),
                )
            else:
                cur.execute(
                    """
                    SELECT *
                    FROM cosdrive_upload_event
                    WHERE task_id = %s
                      AND event_type = 'attempt_heartbeat'
                    ORDER BY sequence_no DESC, created_at DESC
                    LIMIT 1
                    """,
                    (task_id,),
                )
            return _fetchone_dict(cur)


def list_cosdrive_delivery_records(task_id: str, *, delivery_status: str = "pending") -> list[dict]:
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.*, t.drive_path, t.team_name_matched, t.team_space_id, t.team_org_id
                FROM cosdrive_delivery_record r
                LEFT JOIN cosdrive_target t ON t.target_id = r.target_id
                WHERE r.task_id = %s AND r.delivery_status = %s
                ORDER BY r.created_at, r.record_id
                """,
                (task_id, delivery_status),
            )
            rows = cur.fetchall() or []
            if rows and isinstance(rows[0], dict):
                return rows
            cols = [desc[0] for desc in (cur.description or [])]
            return [dict(zip(cols, row)) for row in rows]


def get_cosdrive_delivery_record(record_id: str) -> dict | None:
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM cosdrive_delivery_record
                WHERE record_id = %s
                LIMIT 1
                """,
                (record_id,),
            )
            return _fetchone_dict(cur)


def count_cosdrive_delivery_records(task_id: str) -> dict[str, int]:
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT delivery_status, count(*) AS cnt
                FROM cosdrive_delivery_record
                WHERE task_id = %s
                GROUP BY delivery_status
                """,
                (task_id,),
            )
            rows = cur.fetchall() or []
            counts: dict[str, int] = {}
            if rows and isinstance(rows[0], dict):
                iterable = rows
            else:
                cols = [desc[0] for desc in (cur.description or [])]
                iterable = [dict(zip(cols, row)) for row in rows]
            for row in iterable:
                counts[str(row.get("delivery_status", ""))] = int(row.get("cnt", 0) or 0)
    return counts


def reset_cosdrive_delivery_records(task_id: str, *, from_status: str = "failed") -> int:
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE cosdrive_delivery_record
                SET delivery_status = 'pending',
                    error_code = '',
                    error_message = '',
                    external_receipt_json = '{}'::jsonb
                WHERE task_id = %s AND delivery_status = %s
                """,
                (task_id, from_status),
            )
            rowcount = int(cur.rowcount or 0)
        conn.commit()
    return rowcount


def start_cosdrive_attempt(
    task_id: str,
    *,
    worker_key: str,
    queue_name: str = "cosdrive-upload",
    request_id: str = "",
    trace_id: str = "",
    prefect_flow_run_id: str = "",
    prefect_deployment_id: str = "",
) -> dict:
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(current_attempt_no, 0) + 1 AS next_attempt,
                       task_status, delivery_status
                FROM cosdrive_upload_task
                WHERE task_id = %s
                """,
                (task_id,),
            )
            task = _fetchone_dict(cur)
            if not task:
                raise ValueError(f"cosdrive task not found: {task_id}")
            attempt_id = _gen_id("cosa")
            attempt_no = int(task["next_attempt"])
            cur.execute(
                """
                INSERT INTO cosdrive_upload_attempt (
                    attempt_id, task_id, attempt_no, attempt_status,
                    worker_key, queue_name, request_id, trace_id, started_at
                ) VALUES (%s,%s,%s,'running',%s,%s,%s,%s,now())
                RETURNING *
                """,
                (attempt_id, task_id, attempt_no, worker_key, queue_name, request_id, trace_id),
            )
            attempt = _fetchone_dict(cur) or {}
            cur.execute(
                """
                UPDATE cosdrive_upload_task
                SET task_status = 'uploading',
                    delivery_status = 'uploading',
                    current_attempt_no = %s,
                    updated_at = now()
                WHERE task_id = %s
                """,
                (attempt_no, task_id),
            )
            sequence_no = _next_sequence(cur, "cosdrive_upload_event", task_id)
            cur.execute(
                """
                INSERT INTO cosdrive_upload_event (
                    event_id, task_id, attempt_id, sequence_no, event_type,
                    from_status, to_status, request_id, trace_id, payload_json
                ) VALUES (%s,%s,%s,%s,'delivery_started',%s,'uploading',%s,%s,%s::jsonb)
                """,
                (
                    _gen_id("cose"),
                    task_id,
                    attempt_id,
                    sequence_no,
                    task.get("task_status", ""),
                    request_id,
                    trace_id,
                    _json(
                        {
                            "worker_key": worker_key,
                            "queue_name": queue_name,
                            "prefect_flow_run_id": prefect_flow_run_id,
                            "prefect_deployment_id": prefect_deployment_id,
                        }
                    ),
                ),
            )
        conn.commit()
    return attempt


def mark_cosdrive_delivery_record(
    record_id: str,
    *,
    delivery_status: str,
    external_request_id: str = "",
    external_file_id: str = "",
    external_receipt_json: dict | None = None,
    error_code: str = "",
    error_message: str = "",
) -> dict:
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE cosdrive_delivery_record
                SET delivery_status = %s,
                    external_request_id = %s,
                    external_file_id = %s,
                    external_receipt_json = %s::jsonb,
                    error_code = %s,
                    error_message = %s,
                    delivered_at = CASE WHEN %s = 'delivered' THEN now() ELSE delivered_at END
                WHERE record_id = %s
                RETURNING *
                """,
                (
                    delivery_status,
                    external_request_id,
                    external_file_id,
                    _json(external_receipt_json),
                    error_code,
                    error_message,
                    delivery_status,
                    record_id,
                ),
            )
            row = _fetchone_dict(cur) or {}
        conn.commit()
    return row


def finish_cosdrive_attempt(
    task_id: str,
    attempt_id: str,
    *,
    final_status: str,
    task_status: str,
    delivery_status: str,
    request_id: str = "",
    trace_id: str = "",
    result_summary: dict | None = None,
    error_code: str = "",
    error_message: str = "",
    prefect_flow_run_id: str = "",
    prefect_deployment_id: str = "",
) -> dict:
    with get_portal_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE cosdrive_upload_attempt
                SET attempt_status = %s,
                    finished_at = now(),
                    error_code = %s,
                    error_message = %s,
                    metrics_json = %s::jsonb
                WHERE attempt_id = %s
                RETURNING *
                """,
                (final_status, error_code, error_message, _json(result_summary), attempt_id),
            )
            attempt = _fetchone_dict(cur) or {}
            cur.execute(
                """
                UPDATE cosdrive_upload_task
                SET task_status = %s,
                    delivery_status = %s,
                    external_summary_json = %s::jsonb,
                    completed_at = CASE WHEN %s IN ('completed','partial_failed','failed') THEN now() ELSE completed_at END,
                    updated_at = now(),
                    last_error_code = %s,
                    last_error_message = %s
                WHERE task_id = %s
                RETURNING *
                """,
                (
                    task_status,
                    delivery_status,
                    _json(result_summary),
                    task_status,
                    error_code,
                    error_message,
                    task_id,
                ),
            )
            task = _fetchone_dict(cur) or {}
            sequence_no = _next_sequence(cur, "cosdrive_upload_event", task_id)
            cur.execute(
                """
                INSERT INTO cosdrive_upload_event (
                    event_id, task_id, attempt_id, sequence_no, event_type,
                    from_status, to_status, request_id, trace_id, payload_json
                ) VALUES (%s,%s,%s,%s,'delivery_finished','uploading',%s,%s,%s,%s::jsonb)
                """,
                (
                    _gen_id("cose"),
                    task_id,
                    attempt_id,
                    sequence_no,
                    task_status,
                    request_id,
                    trace_id,
                    _json(
                        {
                            "prefect_flow_run_id": prefect_flow_run_id,
                            "prefect_deployment_id": prefect_deployment_id,
                            **(
                                result_summary
                                if not error_message
                                else {"error_code": error_code, "error_message": error_message, **(result_summary or {})}
                            ),
                        }
                    ),
                ),
            )
        conn.commit()
    return {"task": task, "attempt": attempt}


def append_cosdrive_event(
    task_id: str,
    attempt_id: str | None,
    *,
    event_type: str,
    from_status: str = "uploading",
    to_status: str = "uploading",
    request_id: str = "",
    trace_id: str = "",
    payload: dict | None = None,
) -> dict:
    return _append_domain_event(
        event_table="cosdrive_upload_event",
        event_prefix="cose",
        task_id=task_id,
        attempt_id=attempt_id,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        request_id=request_id,
        trace_id=trace_id,
        payload=payload,
    )
