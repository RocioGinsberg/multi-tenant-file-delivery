from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from app.schemas.task import (
    CreateTaskResponse,
    ClassifyResponse,
    PreviewItem,
    PreviewResponse,
    ConfirmResponse,
    UploadResponse,
    TaskDetailResponse,
    TaskListResponse,
    ProgressEvent,
)


class TestCreateTaskResponse:
    def test_round_trip(self):
        data = {"task_id": "abc", "status": "draft"}
        obj = CreateTaskResponse.model_validate(data)
        assert obj.model_dump() == data

    def test_extra_forbidden(self):
        with pytest.raises(ValidationError):
            CreateTaskResponse.model_validate(
                {"task_id": "x", "status": "y", "extra": "bad"}
            )


class TestClassifyResponse:
    def test_round_trip(self):
        data = {"task_id": "abc", "summary": {"total": 5}}
        obj = ClassifyResponse.model_validate(data)
        assert obj.model_dump() == data

    def test_extra_forbidden(self):
        with pytest.raises(ValidationError):
            ClassifyResponse.model_validate(
                {"task_id": "x", "summary": {}, "extra": "bad"}
            )


class TestPreviewItem:
    def test_round_trip(self):
        data = {
            "id": "i1",
            "task_id": "t1",
            "src_path": "a/b.txt",
            "filename": "b.txt",
            "ext": ".txt",
            "file_size": 100,
            "target_name_raw": "raw",
            "target_name_matched": "matched",
            "document_type": "doc",
            "category_name": "cat",
            "dst_dir": "/dst",
            "dst_path": "/dst/b.txt",
            "severity": "ok",
            "error_code": "",
            "error_message": "",
            "warning_message": "",
            "upload_status": "pending",
            "upload_error": "",
            "uploaded_at": None,
        }
        obj = PreviewItem.model_validate(data)
        assert obj.model_dump() == data

    def test_extra_forbidden(self):
        with pytest.raises(ValidationError):
            PreviewItem.model_validate(
                {
                    "id": "i1",
                    "task_id": "t1",
                    "src_path": "a",
                    "filename": "b",
                    "ext": ".txt",
                    "file_size": 0,
                    "target_name_raw": "",
                    "target_name_matched": None,
                    "document_type": "",
                    "category_name": "",
                    "dst_dir": "",
                    "dst_path": "",
                    "severity": "",
                    "error_code": "",
                    "error_message": "",
                    "warning_message": "",
                    "upload_status": "",
                    "upload_error": "",
                    "uploaded_at": None,
                    "extra": "bad",
                }
            )


class TestPreviewResponse:
    def test_round_trip(self):
        data = {
            "task_id": "t1",
            "summary": {"total": 1},
            "items": [
                {
                    "id": "i1",
                    "task_id": "t1",
                    "src_path": "a",
                    "filename": "b",
                    "ext": ".txt",
                    "file_size": 0,
                    "target_name_raw": "",
                    "target_name_matched": None,
                    "document_type": "",
                    "category_name": "",
                    "dst_dir": "",
                    "dst_path": "",
                    "severity": "",
                    "error_code": "",
                    "error_message": "",
                    "warning_message": "",
                    "upload_status": "",
                    "upload_error": "",
                    "uploaded_at": None,
                }
            ],
        }
        obj = PreviewResponse.model_validate(data)
        assert obj.model_dump() == data

    def test_extra_forbidden(self):
        with pytest.raises(ValidationError):
            PreviewResponse.model_validate(
                {"task_id": "x", "summary": {}, "items": [], "extra": "bad"}
            )


class TestConfirmResponse:
    def test_round_trip(self):
        data = {"task_id": "abc", "status": "confirmed"}
        obj = ConfirmResponse.model_validate(data)
        assert obj.model_dump() == data

    def test_extra_forbidden(self):
        with pytest.raises(ValidationError):
            ConfirmResponse.model_validate(
                {"task_id": "x", "status": "y", "extra": "bad"}
            )


class TestUploadResponse:
    def test_round_trip(self):
        data = {"task_id": "abc", "status": "uploading"}
        obj = UploadResponse.model_validate(data)
        assert obj.model_dump() == data

    def test_extra_forbidden(self):
        with pytest.raises(ValidationError):
            UploadResponse.model_validate(
                {"task_id": "x", "status": "y", "extra": "bad"}
            )


class TestTaskDetailResponse:
    def test_round_trip(self):
        data = {
            "task_id": "t1",
            "status": "draft",
            "idempotency_key": "idem-1",
            "submission_label": "test.zip",
            "temp_dir": "/tmp/auto_upload_tasks/t1",
            "summary_json": {"total": 0},
            "created_by": "local-user",
            "created_at": None,
            "confirmed_at": None,
            "finished_at": None,
        }
        obj = TaskDetailResponse.model_validate(data)
        assert obj.model_dump() == data

    def test_extra_forbidden(self):
        with pytest.raises(ValidationError):
            TaskDetailResponse.model_validate(
                {
                    "task_id": "x",
                    "status": "y",
                    "idempotency_key": "z",
                    "submission_label": "a",
                    "temp_dir": "b",
                    "summary_json": {},
                    "created_by": "c",
                    "created_at": None,
                    "confirmed_at": None,
                    "finished_at": None,
                    "extra": "bad",
                }
            )


class TestTaskListResponse:
    def test_round_trip(self):
        data = {
            "tasks": [
                {
                    "task_id": "t1",
                    "status": "draft",
                    "idempotency_key": "idem-1",
                    "submission_label": "test.zip",
                    "temp_dir": "/tmp/auto_upload_tasks/t1",
                    "summary_json": {"total": 0},
                    "created_by": "local-user",
                    "created_at": None,
                    "confirmed_at": None,
                    "finished_at": None,
                }
            ],
            "limit": 10,
            "offset": 0,
        }
        obj = TaskListResponse.model_validate(data)
        assert obj.model_dump() == data

    def test_extra_forbidden(self):
        with pytest.raises(ValidationError):
            TaskListResponse.model_validate(
                {"tasks": [], "limit": 0, "offset": 0, "extra": "bad"}
            )


class TestProgressEvent:
    def test_round_trip(self):
        data = {"type": "progress", "task_id": "t1"}
        obj = ProgressEvent.model_validate(data)
        assert obj.model_dump() == data

    def test_extra_allowed(self):
        data = {"type": "progress", "task_id": "t1", "extra_field": "ok"}
        obj = ProgressEvent.model_validate(data)
        assert obj.model_dump() == data

    def test_task_id_optional(self):
        data = {"type": "progress"}
        obj = ProgressEvent.model_validate(data)
        assert obj.model_dump() == {"type": "progress", "task_id": None}
