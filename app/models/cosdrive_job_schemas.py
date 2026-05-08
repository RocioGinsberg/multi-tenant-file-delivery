"""CosDrive task-related request/response models for Portal control-plane APIs."""

from __future__ import annotations

from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field


class RegistryConfigPayload(BaseModel):
    """注册表配置体 — 即 task_classification.json 的完整结构"""
    team_aliases: Dict[str, str] = Field(default_factory=dict)
    task_classification: Dict[str, Any] = Field(default_factory=dict)
    description_mapping: Dict[str, str] = Field(default_factory=dict)
    mapping_config: Dict[str, Any] = Field(default_factory=dict)
    suffix_priority: Dict[str, str] = Field(default_factory=dict)
    suffix_fallback: Dict[str, str] = Field(default_factory=dict)
    ignored_filenames: List[str] = Field(default_factory=list)


class RegistrySaveDraftRequest(BaseModel):
    config: RegistryConfigPayload


class RegistryVersionResponse(BaseModel):
    id: str
    version_no: int
    status: str
    config_json: Dict[str, Any]
    created_by: str
    created_at: str
    published_by: str
    published_at: Optional[str] = None


class RegistryValidateResponse(BaseModel):
    valid: bool
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class TeamItem(BaseModel):
    id: Any
    name: str
    original_name: str
    space_id: str
    org_id: str


class TeamRefreshResponse(BaseModel):
    teams: List[TeamItem]
    count: int


class TaskIdentityMixin(BaseModel):
    task_id: str


class TaskCreateResponse(TaskIdentityMixin):
    status: str
    file_count: int
    files: List[str]


class ClassifyRequest(BaseModel):
    registry_version_id: str


class ClassifiedItemResponse(BaseModel):
    item_id: str
    filename: str
    relative_path: str
    ext: str
    team_name_raw: str
    team_name_matched: str
    team_space_id: str
    task_name: str
    category_name: str
    drive_dir: str
    drive_path: str
    team_match_method: str
    task_match_method: str
    match_score: int
    mapping_source: str
    severity: str
    error_code: str
    error_message: str
    warning_message: str


class ClassifySummary(BaseModel):
    total: int = 0
    ok: int = 0
    warning: int = 0
    error: int = 0
    ignored: int = 0
    unmatched_teams: int = 0
    unmatched_tasks: int = 0
    teams_involved: List[str] = Field(default_factory=list)
    has_blocking_errors: bool = False


class ClassifyResponse(TaskIdentityMixin):
    status: str
    summary: ClassifySummary
    items: List[ClassifiedItemResponse]


class PreviewResponse(TaskIdentityMixin):
    status: str
    registry_version_id: str
    summary: ClassifySummary
    items: List[ClassifiedItemResponse]


class ConfirmResponse(TaskIdentityMixin):
    status: str
    confirmed_at: str


class UploadProgressResponse(TaskIdentityMixin):
    status: str
    total: int
    pending: int
    uploading: int
    uploaded: int
    failed: int
    skipped: int


class RetryRequest(BaseModel):
    item_ids: List[str] = Field(default_factory=list, description="指定要重试的 item_id 列表；为空则重试所有 failed 项")


class TaskDetailResponse(TaskIdentityMixin):
    status: str
    registry_version_id: str
    summary: ClassifySummary
    created_by: str
    created_at: str
    confirmed_at: Optional[str] = None
    finished_at: Optional[str] = None
    latest_attempt_status: Optional[str] = None
    latest_attempt_started_at: Optional[str] = None
    latest_attempt_finished_at: Optional[str] = None
    latest_event_type: Optional[str] = None
    latest_event_status: Optional[str] = None
    latest_event_at: Optional[str] = None


class TaskAttemptResponse(BaseModel):
    attempt_id: str
    attempt_no: int = 0
    attempt_status: str = ""
    worker_key: str = ""
    request_id: str = ""
    trace_id: str = ""
    error_code: str = ""
    error_message: str = ""
    metrics_json: Dict[str, Any] = Field(default_factory=dict)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class TaskEventResponse(BaseModel):
    event_id: str
    attempt_id: str = ""
    sequence_no: int = 0
    event_type: str = ""
    from_status: str = ""
    to_status: str = ""
    request_id: str = ""
    trace_id: str = ""
    payload_json: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None


class TaskDetailWithTimelineResponse(TaskDetailResponse):
    prefect_flow_run_id: Optional[str] = None
    prefect_ui_url: Optional[str] = None
    attempts: List[TaskAttemptResponse] = Field(default_factory=list)
    events: List[TaskEventResponse] = Field(default_factory=list)


class TaskItemListResponse(TaskIdentityMixin):
    items: List[ClassifiedItemResponse]
    total: int


class TaskListResponse(BaseModel):
    tasks: List[TaskDetailResponse]
    total: int
    page: int = 1
    page_size: int = 50
