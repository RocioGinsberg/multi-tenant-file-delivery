package message

import (
	"encoding/json"
	"testing"
	"time"
)

func TestDeliveryTaskJSONRoundTrip(t *testing.T) {
	task := DeliveryTask{
		SchemaVersion:   1,
		Topic:           "delivery.tasks.v1",
		TaskID:          "task-1",
		IdempotencyKey:  "idem-1",
		SubmissionLabel: "upload.zip",
		TempDir:         "/tmp/task-1",
		BucketName:      "auto-upload-dev",
		CreatedAt:       time.Date(2026, 5, 13, 10, 0, 0, 0, time.UTC),
		Items: []DeliveryItem{{
			ItemID:        "item-1",
			SrcPath:       "acme/report.xlsx",
			Filename:      "report.xlsx",
			Ext:           ".xlsx",
			FileSize:      123,
			DstPath:       "reports/report.xlsx",
			Severity:      "ok",
			TargetNameRaw: "acme",
			UploadStatus:  "pending",
		}},
	}

	raw, err := json.Marshal(task)
	if err != nil {
		t.Fatal(err)
	}

	var decoded DeliveryTask
	if err := json.Unmarshal(raw, &decoded); err != nil {
		t.Fatal(err)
	}

	if decoded.TaskID != task.TaskID || decoded.BucketName != task.BucketName {
		t.Fatalf("unexpected roundtrip: %+v", decoded)
	}
	if len(decoded.Items) != 1 || decoded.Items[0].DstPath != "reports/report.xlsx" {
		t.Fatalf("unexpected items: %+v", decoded.Items)
	}
}

func TestDeliveryTaskAcceptsControlPlaneMetadataValues(t *testing.T) {
	raw := []byte(`{
		"schema_version": 1,
		"topic": "delivery.tasks.v1",
		"task_id": "task-1",
		"idempotency_key": "idem-1",
		"submission_label": "upload.zip",
		"temp_dir": "/tmp/task-1",
		"bucket_name": "auto-upload-dev",
		"created_at": "2026-05-13T10:00:00Z",
		"items": [],
		"metadata": {
			"status": "confirmed",
			"created_by": "local-user",
			"confirmed_at": null
		}
	}`)

	var decoded DeliveryTask
	if err := json.Unmarshal(raw, &decoded); err != nil {
		t.Fatal(err)
	}

	if decoded.Metadata["status"] != "confirmed" {
		t.Fatalf("unexpected metadata: %+v", decoded.Metadata)
	}
}

func TestDeliveryTaskAcceptsSourceReferencePayload(t *testing.T) {
	raw := []byte(`{
		"schema_version": 2,
		"topic": "delivery.tasks.v1",
		"task_id": "task-source",
		"idempotency_key": "idem-source",
		"submission_label": "upload.zip",
		"bucket_name": "auto-upload-dev",
		"created_at": "2026-05-13T10:00:00Z",
		"source": {
			"type": "object",
			"bucket": "auto-upload-staging",
			"key": "staged/tasks/task-source/archive.zip",
			"sha256": "abc",
			"size": 456
		},
		"items": [{
			"item_id": "item-source",
			"src_path": "acme/report.xlsx",
			"source_path": "acme/report.xlsx",
			"filename": "report.xlsx",
			"ext": ".xlsx",
			"file_size": 123,
			"dst_path": "reports/report.xlsx",
			"severity": "ok",
			"target_name_raw": "acme",
			"target_name_matched": "acme",
			"document_type": "report",
			"category_name": "reports",
			"upload_status": "pending"
		}]
	}`)

	var decoded DeliveryTask
	if err := json.Unmarshal(raw, &decoded); err != nil {
		t.Fatal(err)
	}

	if decoded.SchemaVersion != 2 || decoded.Source == nil {
		t.Fatalf("unexpected source payload: %+v", decoded)
	}
	if decoded.Source.Bucket != "auto-upload-staging" || decoded.Source.Size != 456 {
		t.Fatalf("unexpected source reference: %+v", decoded.Source)
	}
	if len(decoded.Items) != 1 || decoded.Items[0].SourcePath != "acme/report.xlsx" {
		t.Fatalf("unexpected source item: %+v", decoded.Items)
	}
}

func TestDeliveryResultJSONRoundTripIncludesItems(t *testing.T) {
	result := DeliveryResult{
		Topic:     "delivery.results.v1",
		TaskID:    "task-1",
		Status:    "uploaded",
		Uploaded:  1,
		Processed: 1,
		Items: []DeliveryResultItem{{
			ItemID: "item-1",
			Status: "uploaded",
			Key:    "reports/report.xlsx",
			Size:   5,
			SHA256: "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
		}},
		StartedAt: time.Date(2026, 5, 13, 10, 0, 0, 0, time.UTC),
		EndedAt:   time.Date(2026, 5, 13, 10, 0, 1, 0, time.UTC),
	}

	raw, err := json.Marshal(result)
	if err != nil {
		t.Fatal(err)
	}

	var decoded DeliveryResult
	if err := json.Unmarshal(raw, &decoded); err != nil {
		t.Fatal(err)
	}

	if len(decoded.Items) != 1 || decoded.Items[0].SHA256 != result.Items[0].SHA256 {
		t.Fatalf("unexpected result items: %+v", decoded.Items)
	}
}
