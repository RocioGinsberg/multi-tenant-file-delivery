from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CreateTaskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: str


class ClassifyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    summary: dict


class PreviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    task_id: str
    src_path: str
    filename: str
    ext: str
    file_size: int
    target_name_raw: str
    target_name_matched: str | None
    document_type: str
    category_name: str
    dst_dir: str
    dst_path: str
    severity: str
    error_code: str
    error_message: str
    warning_message: str
    upload_status: str
    upload_error: str
    uploaded_at: datetime | None


class PreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    summary: dict
    items: list[PreviewItem]


class ConfirmResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: str


class UploadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: str


class TaskDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    status: str
    idempotency_key: str
    owner_tenant_id: str
    owner_user_id: str
    submission_label: str
    temp_dir: str
    summary_json: dict
    created_by: str
    created_at: datetime | None
    confirmed_at: datetime | None
    finished_at: datetime | None


class TaskListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tasks: list[TaskDetailResponse]
    limit: int
    offset: int


class ProgressEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    task_id: str | None = None
