package transport

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"smh_auto_upload/data-plane/internal/message"
)

func TestFileSpoolConsumeReadsTaskJSONFiles(t *testing.T) {
	dir := t.TempDir()
	inboxDir := filepath.Join(dir, "inbox")
	if err := os.MkdirAll(inboxDir, 0o755); err != nil {
		t.Fatal(err)
	}
	task := message.DeliveryTask{
		SchemaVersion:  1,
		Topic:          "delivery.tasks.v1",
		TaskID:         "task-1",
		IdempotencyKey: "idem-1",
		TempDir:        filepath.Join(dir, "task"),
		BucketName:     "auto-upload-dev",
		CreatedAt:      time.Date(2026, 5, 17, 10, 0, 0, 0, time.UTC),
	}
	raw, err := json.Marshal(task)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(inboxDir, "task-1.json"), raw, 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(inboxDir, "ignore.txt"), []byte("ignored"), 0o644); err != nil {
		t.Fatal(err)
	}

	spool := NewFileSpool(inboxDir, filepath.Join(dir, "results"))
	tasks, err := spool.Consume(context.Background())
	if err != nil {
		t.Fatalf("consume tasks: %v", err)
	}
	if len(tasks) != 1 || tasks[0].TaskID != "task-1" {
		t.Fatalf("unexpected tasks: %+v", tasks)
	}
}

func TestFileSpoolConsumeReturnsDecodeError(t *testing.T) {
	dir := t.TempDir()
	inboxDir := filepath.Join(dir, "inbox")
	if err := os.MkdirAll(inboxDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(inboxDir, "bad.json"), []byte("{bad"), 0o644); err != nil {
		t.Fatal(err)
	}

	spool := NewFileSpool(inboxDir, filepath.Join(dir, "results"))
	if _, err := spool.Consume(context.Background()); err == nil {
		t.Fatal("expected decode error")
	}
}

func TestFileSpoolProduceWritesResultJSON(t *testing.T) {
	dir := t.TempDir()
	resultsDir := filepath.Join(dir, "results")
	spool := NewFileSpool(filepath.Join(dir, "inbox"), resultsDir)

	result := message.DeliveryResult{
		Topic:     "delivery.results.v1",
		TaskID:    "task-1",
		Status:    "uploaded",
		Uploaded:  1,
		StartedAt: time.Date(2026, 5, 17, 10, 0, 0, 0, time.UTC),
		EndedAt:   time.Date(2026, 5, 17, 10, 1, 0, 0, time.UTC),
	}
	if err := spool.Produce(context.Background(), result); err != nil {
		t.Fatalf("produce result: %v", err)
	}

	raw, err := os.ReadFile(filepath.Join(resultsDir, "task-1.json"))
	if err != nil {
		t.Fatal(err)
	}
	var decoded message.DeliveryResult
	if err := json.Unmarshal(raw, &decoded); err != nil {
		t.Fatal(err)
	}
	if decoded.TaskID != "task-1" || decoded.Status != "uploaded" || decoded.Uploaded != 1 {
		t.Fatalf("unexpected result: %+v", decoded)
	}
}
