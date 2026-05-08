"""CosDrive repository backed by the unified task-domain tables.

Registry remains on ``cosdrive_registry_version`` and task execution is stored in:
- cosdrive_upload_task
- cosdrive_upload_attempt
- cosdrive_upload_event
- cosdrive_target
- cosdrive_delivery_record
"""

from __future__ import annotations

import json
import uuid
from typing import Optional

from ..db import get_portal_cursor


def _gen_id() -> str:
    return uuid.uuid4().hex[:16]


def _task_status_to_legacy(status: str) -> str:
    return {"completed": "uploaded"}.get(status, status)


def _legacy_status_to_task(status: str) -> str:
    return {"uploaded": "completed"}.get(status, status)


def _delivery_status_to_legacy(status: str) -> str:
    return {"delivered": "uploaded"}.get(status, status)


def _legacy_upload_to_delivery(status: str) -> str:
    return {"uploaded": "delivered"}.get(status, status)


def _normalize_task_row(row: dict | None) -> dict | None:
    if not row:
        return None
    out = dict(row)
    task_status = out.get("task_status", "")
    classification_status = out.get("classification_status", "")
    if task_status == "classified" and classification_status == "failed":
        out["status"] = "classification_failed"
    else:
        out["status"] = _task_status_to_legacy(task_status)
    out["created_by"] = out.get("submitted_by", "")
    out["summary_json"] = out.get("classification_summary_json", {})
    out["finished_at"] = out.get("completed_at")
    return out


def _attach_latest_summary(cur, row: dict) -> dict:
    task_id = row.get("task_id", "")
    if not task_id:
        return row
    cur.execute(
        """
        SELECT attempt_status, started_at, finished_at
        FROM cosdrive_upload_attempt
        WHERE task_id = %s
        ORDER BY attempt_no DESC, created_at DESC
        LIMIT 1
        """,
        (task_id,),
    )
    latest_attempt = cur.fetchone()
    if latest_attempt:
        latest_attempt = dict(latest_attempt)
        row["latest_attempt_status"] = latest_attempt.get("attempt_status", "")
        row["latest_attempt_started_at"] = latest_attempt.get("started_at")
        row["latest_attempt_finished_at"] = latest_attempt.get("finished_at")

    cur.execute(
        """
        SELECT event_type, to_status, created_at
        FROM cosdrive_upload_event
        WHERE task_id = %s
        ORDER BY sequence_no DESC, created_at DESC
        LIMIT 1
        """,
        (task_id,),
    )
    latest_event = cur.fetchone()
    if latest_event:
        latest_event = dict(latest_event)
        row["latest_event_type"] = latest_event.get("event_type", "")
        row["latest_event_status"] = latest_event.get("to_status", "")
        row["latest_event_at"] = latest_event.get("created_at")
    return row


def _normalize_item_row(row: dict | None) -> dict | None:
    if not row:
        return None
    out = dict(row)
    out["item_id"] = out.get("record_id", "")
    out["upload_status"] = _delivery_status_to_legacy(out.get("delivery_status", ""))
    out["upload_error"] = out.get("error_message", "")
    out["uploaded_at"] = out.get("delivered_at")
    return out


def get_published_registry() -> Optional[dict]:
    with get_portal_cursor() as cur:
        cur.execute(
            "SELECT * FROM cosdrive_registry_version WHERE status = 'published' ORDER BY version_no DESC LIMIT 1"
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_registry_version(version_id: str) -> Optional[dict]:
    with get_portal_cursor() as cur:
        cur.execute("SELECT * FROM cosdrive_registry_version WHERE id = %s", (version_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def list_registry_versions(limit: int = 50) -> list[dict]:
    with get_portal_cursor() as cur:
        cur.execute("SELECT * FROM cosdrive_registry_version ORDER BY version_no DESC LIMIT %s", (limit,))
        return [dict(r) for r in cur.fetchall()]


def get_draft_registry() -> Optional[dict]:
    with get_portal_cursor() as cur:
        cur.execute(
            "SELECT * FROM cosdrive_registry_version WHERE status = 'draft' ORDER BY created_at DESC LIMIT 1"
        )
        row = cur.fetchone()
        return dict(row) if row else None


def save_draft_registry(config_json: dict, user: str) -> dict:
    existing = get_draft_registry()
    if existing:
        with get_portal_cursor() as cur:
            cur.execute(
                "UPDATE cosdrive_registry_version SET config_json = %s, created_by = %s "
                "WHERE id = %s RETURNING *",
                (json.dumps(config_json, ensure_ascii=False), user, existing["id"]),
            )
            return dict(cur.fetchone())
    with get_portal_cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(version_no), 0) + 1 AS next_no FROM cosdrive_registry_version")
        next_no = cur.fetchone()["next_no"]
    vid = _gen_id()
    with get_portal_cursor() as cur:
        cur.execute(
            "INSERT INTO cosdrive_registry_version (id, version_no, status, config_json, created_by) "
            "VALUES (%s, %s, 'draft', %s, %s) RETURNING *",
            (vid, next_no, json.dumps(config_json, ensure_ascii=False), user),
        )
        return dict(cur.fetchone())


def publish_registry(version_id: str, user: str) -> Optional[dict]:
    with get_portal_cursor() as cur:
        cur.execute("UPDATE cosdrive_registry_version SET status = 'archived' WHERE status = 'published'")
        cur.execute(
            "UPDATE cosdrive_registry_version SET status = 'published', published_by = %s, published_at = now() "
            "WHERE id = %s RETURNING *",
            (user, version_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def rollback_registry(target_version_id: str, user: str) -> Optional[dict]:
    target = get_registry_version(target_version_id)
    if not target:
        return None
    with get_portal_cursor() as cur:
        cur.execute("UPDATE cosdrive_registry_version SET status = 'archived' WHERE status = 'published'")
        cur.execute("SELECT COALESCE(MAX(version_no), 0) + 1 AS next_no FROM cosdrive_registry_version")
        next_no = cur.fetchone()["next_no"]
    vid = _gen_id()
    config = target["config_json"]
    if isinstance(config, str):
        config = json.loads(config)
    with get_portal_cursor() as cur:
        cur.execute(
            "INSERT INTO cosdrive_registry_version "
            "(id, version_no, status, config_json, created_by, published_by, published_at) "
            "VALUES (%s, %s, 'published', %s, %s, %s, now()) RETURNING *",
            (vid, next_no, json.dumps(config, ensure_ascii=False), user, user),
        )
        return dict(cur.fetchone())


def create_task(task_id: str, temp_dir: str, user: str) -> dict:
    with get_portal_cursor() as cur:
        cur.execute(
            """
            INSERT INTO cosdrive_upload_task (
                task_id, task_status, classification_status, delivery_status,
                idempotency_key, temp_dir, submitted_by
            ) VALUES (
                %s, 'draft', 'pending', 'pending',
                %s, %s, %s
            ) RETURNING *
            """,
            (task_id, f"cosdrive:{task_id}", temp_dir, user),
        )
        return _normalize_task_row(dict(cur.fetchone()))


def get_task(task_id: str) -> Optional[dict]:
    with get_portal_cursor() as cur:
        cur.execute("SELECT * FROM cosdrive_upload_task WHERE task_id = %s", (task_id,))
        row = cur.fetchone()
        return _normalize_task_row(_attach_latest_summary(cur, dict(row))) if row else None


def list_attempts(task_id: str, *, limit: int = 50) -> list[dict]:
    with get_portal_cursor() as cur:
        cur.execute(
            """
            SELECT attempt_id, task_id, attempt_no, attempt_status,
                   worker_key, request_id, trace_id, started_at, finished_at,
                   error_code, error_message, metrics_json, created_at
            FROM cosdrive_upload_attempt
            WHERE task_id = %s
            ORDER BY attempt_no DESC, created_at DESC
            LIMIT %s
            """,
            (task_id, limit),
        )
        return [dict(r) for r in (cur.fetchall() or [])]


def list_events(task_id: str, *, limit: int = 100) -> list[dict]:
    with get_portal_cursor() as cur:
        cur.execute(
            """
            SELECT event_id, task_id, attempt_id, sequence_no, event_type,
                   from_status, to_status, request_id, trace_id, payload_json, created_at
            FROM cosdrive_upload_event
            WHERE task_id = %s
            ORDER BY sequence_no DESC, created_at DESC
            LIMIT %s
            """,
            (task_id, limit),
        )
        return [dict(r) for r in (cur.fetchall() or [])]


def update_task(task_id: str, **kwargs) -> Optional[dict]:
    if not kwargs:
        return get_task(task_id)
    sets, params = [], []
    mapping = {
        "status": "task_status",
        "classification_status": "classification_status",
        "delivery_status": "delivery_status",
        "registry_version_id": "registry_version_id",
        "temp_dir": "temp_dir",
        "team_snapshot_json": "team_snapshot_json",
        "summary_json": "classification_summary_json",
        "confirmed_at": "confirmed_at",
        "finished_at": "completed_at",
    }
    for key, col in mapping.items():
        if key not in kwargs:
            continue
        value = kwargs[key]
        if col in ("team_snapshot_json", "classification_summary_json") and not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
            sets.append(f"{col} = %s::jsonb")
            params.append(value)
        elif value == "now()":
            sets.append(f"{col} = now()")
        else:
            if col == "task_status":
                value = _legacy_status_to_task(value)
            sets.append(f"{col} = %s")
            params.append(value)
    if not sets:
        return get_task(task_id)
    params.append(task_id)
    with get_portal_cursor() as cur:
        cur.execute(
            f"UPDATE cosdrive_upload_task SET {', '.join(sets)} WHERE task_id = %s RETURNING *",
            params,
        )
        row = cur.fetchone()
        return _normalize_task_row(dict(row)) if row else None


def list_tasks(user: str = "", limit: int = 50, offset: int = 0) -> tuple[list, int]:
    conds, params = [], []
    if user:
        conds.append("submitted_by = %s")
        params.append(user)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    with get_portal_cursor() as cur:
        cur.execute(f"SELECT count(*) AS total FROM cosdrive_upload_task {where}", params)
        total = cur.fetchone()["total"]
        cur.execute(
            f"SELECT * FROM cosdrive_upload_task {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
            params + [limit, offset],
        )
        rows = []
        for r in cur.fetchall():
            rows.append(_normalize_task_row(_attach_latest_summary(cur, dict(r))))
        return rows, total


def bulk_insert_items(items: list[dict]) -> int:
    if not items:
        return 0
    with get_portal_cursor() as cur:
        for item in items:
            item_id = item.get("item_id") or _gen_id()
            target_id = f"t_{item_id}"
            cur.execute(
                """
                INSERT INTO cosdrive_target (
                    target_id, task_id, team_name_raw, team_name_matched, team_space_id,
                    team_org_id, task_name, category_name, drive_dir, drive_path,
                    mapping_source, target_snapshot_json
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s::jsonb
                )
                ON CONFLICT (target_id) DO NOTHING
                """,
                (
                    target_id,
                    item["task_id"],
                    item.get("team_name_raw", ""),
                    item.get("team_name_matched", ""),
                    item.get("team_space_id", ""),
                    item.get("team_org_id", ""),
                    item.get("task_name", ""),
                    item.get("category_name", ""),
                    item.get("drive_dir", ""),
                    item.get("drive_path", ""),
                    item.get("mapping_source", ""),
                    json.dumps(
                        {
                            "team_match_method": item.get("team_match_method", ""),
                            "task_match_method": item.get("task_match_method", ""),
                            "match_score": item.get("match_score", 0),
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            cur.execute(
                """
                INSERT INTO cosdrive_delivery_record (
                    record_id, task_id, target_id, source_item_key, filename, relative_path,
                    file_size, severity, delivery_status, error_code, error_message, warning_message
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, 'pending', %s, %s, %s
                )
                """,
                (
                    item_id,
                    item["task_id"],
                    target_id,
                    item.get("item_id", item_id),
                    item.get("filename", ""),
                    item.get("relative_path", ""),
                    item.get("file_size", 0),
                    item.get("severity", "ok"),
                    item.get("error_code", ""),
                    item.get("error_message", ""),
                    item.get("warning_message", ""),
                ),
            )
    return len(items)


def delete_items_by_task(task_id: str) -> int:
    with get_portal_cursor() as cur:
        cur.execute("DELETE FROM cosdrive_delivery_record WHERE task_id = %s", (task_id,))
        deleted = cur.rowcount
        cur.execute("DELETE FROM cosdrive_target WHERE task_id = %s", (task_id,))
        return deleted


def list_items(task_id: str, severity: str = "", upload_status: str = "") -> list[dict]:
    conds = ["r.task_id = %s"]
    params: list = [task_id]
    if severity:
        conds.append("r.severity = %s")
        params.append(severity)
    if upload_status:
        conds.append("r.delivery_status = %s")
        params.append(_legacy_upload_to_delivery(upload_status))
    where = " AND ".join(conds)
    with get_portal_cursor() as cur:
        cur.execute(
            f"""
            SELECT
                r.*,
                t.team_name_raw, t.team_name_matched, t.team_space_id, t.team_org_id,
                t.task_name, t.category_name, t.drive_dir, t.drive_path, t.mapping_source,
                t.target_snapshot_json
            FROM cosdrive_delivery_record r
            LEFT JOIN cosdrive_target t ON t.target_id = r.target_id
            WHERE {where}
            ORDER BY t.team_name_matched, t.category_name, t.drive_path
            """,
            params,
        )
        return [_normalize_item_row(dict(r)) for r in cur.fetchall()]


def get_item(item_id: str) -> Optional[dict]:
    with get_portal_cursor() as cur:
        cur.execute(
            """
            SELECT r.*, t.team_name_raw, t.team_name_matched, t.team_space_id, t.team_org_id,
                   t.task_name, t.category_name, t.drive_dir, t.drive_path, t.mapping_source,
                   t.target_snapshot_json
            FROM cosdrive_delivery_record r
            LEFT JOIN cosdrive_target t ON t.target_id = r.target_id
            WHERE r.record_id = %s
            """,
            (item_id,),
        )
        row = cur.fetchone()
        return _normalize_item_row(dict(row)) if row else None


def update_item_upload_status(item_id: str, upload_status: str, upload_error: str = "") -> Optional[dict]:
    sets = ["delivery_status = %s", "error_message = %s"]
    params: list = [_legacy_upload_to_delivery(upload_status), upload_error]
    if upload_status == "uploaded":
        sets.append("delivered_at = now()")
    params.append(item_id)
    with get_portal_cursor() as cur:
        cur.execute(
            f"UPDATE cosdrive_delivery_record SET {', '.join(sets)} WHERE record_id = %s RETURNING *",
            params,
        )
        row = cur.fetchone()
        return _normalize_item_row(dict(row)) if row else None


def batch_reset_failed_items(task_id: str, item_ids: list[str] = None) -> int:
    if item_ids:
        placeholders = ", ".join(["%s"] * len(item_ids))
        params = [task_id] + item_ids
        sql = (
            f"UPDATE cosdrive_delivery_record SET delivery_status = 'pending', error_message = '' "
            f"WHERE task_id = %s AND record_id IN ({placeholders}) AND delivery_status = 'failed'"
        )
    else:
        params = [task_id]
        sql = (
            "UPDATE cosdrive_delivery_record SET delivery_status = 'pending', error_message = '' "
            "WHERE task_id = %s AND delivery_status = 'failed'"
        )
    with get_portal_cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def count_items_by_status(task_id: str) -> dict:
    with get_portal_cursor() as cur:
        cur.execute(
            "SELECT delivery_status, count(*) AS cnt FROM cosdrive_delivery_record WHERE task_id = %s GROUP BY delivery_status",
            (task_id,),
        )
        result = {_delivery_status_to_legacy(r["delivery_status"]): r["cnt"] for r in cur.fetchall()}
    for s in ("pending", "uploading", "uploaded", "failed", "skipped"):
        result.setdefault(s, 0)
    result["total"] = sum(result.values())
    return result
