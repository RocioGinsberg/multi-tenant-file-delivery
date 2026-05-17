package worker

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"smh_auto_upload/data-plane/internal/message"
	"smh_auto_upload/data-plane/internal/sink"
)

func TestRunProcessesInboxTaskAndWritesResult(t *testing.T) {
	dir := t.TempDir()
	inboxDir := filepath.Join(dir, "inbox")
	resultsDir := filepath.Join(dir, "results")
	tempDir := filepath.Join(dir, "task")
	if err := os.MkdirAll(inboxDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(tempDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(tempDir, "report.txt"), []byte("hello"), 0o644); err != nil {
		t.Fatal(err)
	}

	task := message.DeliveryTask{
		SchemaVersion:   1,
		Topic:           "delivery.tasks.v1",
		TaskID:          "task-1",
		IdempotencyKey:  "idem-1",
		SubmissionLabel: "upload.zip",
		TempDir:         tempDir,
		BucketName:      "auto-upload-dev",
		CreatedAt:       time.Date(2026, 5, 17, 10, 0, 0, 0, time.UTC),
		Items: []message.DeliveryItem{{
			ItemID:       "item-1",
			SrcPath:      "report.txt",
			Filename:     "report.txt",
			DstPath:      "reports/report.txt",
			Severity:     "ok",
			UploadStatus: "pending",
		}},
	}
	writeTaskFile(t, inboxDir, task)

	mockSink := sink.NewMockSink()
	w := New(Config{InboxDir: inboxDir, ResultsDir: resultsDir, SinkName: "mock", Once: true}, mockSink)
	if err := w.Run(context.Background()); err != nil {
		t.Fatalf("run worker: %v", err)
	}

	result := readResultFile(t, resultsDir, "task-1")
	if result.Status != "uploaded" || result.Uploaded != 1 || result.Failed != 0 || result.Processed != 1 {
		t.Fatalf("unexpected result: %+v", result)
	}
	data, ok := mockSink.Object("reports/report.txt")
	if !ok {
		t.Fatal("expected mock sink object")
	}
	if string(data) != "hello" {
		t.Fatalf("unexpected object data: %q", string(data))
	}
}

func TestRunWritesPartialFailureResult(t *testing.T) {
	dir := t.TempDir()
	inboxDir := filepath.Join(dir, "inbox")
	resultsDir := filepath.Join(dir, "results")
	tempDir := filepath.Join(dir, "task")
	if err := os.MkdirAll(inboxDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(tempDir, 0o755); err != nil {
		t.Fatal(err)
	}

	task := message.DeliveryTask{
		SchemaVersion:   1,
		Topic:           "delivery.tasks.v1",
		TaskID:          "task-2",
		IdempotencyKey:  "idem-2",
		SubmissionLabel: "upload.zip",
		TempDir:         tempDir,
		BucketName:      "auto-upload-dev",
		CreatedAt:       time.Date(2026, 5, 17, 10, 0, 0, 0, time.UTC),
		Items: []message.DeliveryItem{{
			ItemID:       "item-1",
			SrcPath:      "missing.txt",
			Filename:     "missing.txt",
			DstPath:      "reports/missing.txt",
			Severity:     "ok",
			UploadStatus: "pending",
		}},
	}
	writeTaskFile(t, inboxDir, task)

	mockSink := sink.NewMockSink()
	w := New(Config{InboxDir: inboxDir, ResultsDir: resultsDir, SinkName: "mock", Once: true}, mockSink)
	if err := w.Run(context.Background()); err != nil {
		t.Fatalf("run worker: %v", err)
	}

	result := readResultFile(t, resultsDir, "task-2")
	if result.Status != "partial_failed" || result.Uploaded != 0 || result.Failed != 1 || result.Processed != 1 {
		t.Fatalf("unexpected result: %+v", result)
	}
	if result.Error == "" {
		t.Fatal("expected result error")
	}
}

func writeTaskFile(t *testing.T, inboxDir string, task message.DeliveryTask) {
	t.Helper()
	raw, err := json.Marshal(task)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(inboxDir, task.TaskID+".json"), raw, 0o644); err != nil {
		t.Fatal(err)
	}
}

func readResultFile(t *testing.T, resultsDir, taskID string) message.DeliveryResult {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(resultsDir, taskID+".json"))
	if err != nil {
		t.Fatal(err)
	}
	var result message.DeliveryResult
	if err := json.Unmarshal(raw, &result); err != nil {
		t.Fatal(err)
	}
	return result
}
