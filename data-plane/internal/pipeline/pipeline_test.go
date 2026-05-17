package pipeline

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"smh_auto_upload/data-plane/internal/message"
	"smh_auto_upload/data-plane/internal/sink"
)

func TestProcessTaskUploadsPendingItems(t *testing.T) {
	dir := t.TempDir()
	srcDir := filepath.Join(dir, "task")
	if err := os.MkdirAll(srcDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(srcDir, "report.xlsx"), []byte("hello"), 0o644); err != nil {
		t.Fatal(err)
	}

	task := message.DeliveryTask{
		TaskID:  "task-1",
		TempDir: srcDir,
		Items: []message.DeliveryItem{{
			ItemID:       "item-1",
			SrcPath:      "report.xlsx",
			Filename:     "report.xlsx",
			DstPath:      "reports/report.xlsx",
			Severity:     "ok",
			UploadStatus: "pending",
		}},
	}

	mockSink := sink.NewMockSink()
	result, err := ProcessTask(context.Background(), task, mockSink)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Uploaded != 1 || result.Failed != 0 {
		t.Fatalf("unexpected result: %+v", result)
	}
	if len(result.Items) != 1 || result.Items[0].ItemID != "item-1" || result.Items[0].Status != "uploaded" || result.Items[0].SHA256 != "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824" {
		t.Fatalf("unexpected item result: %+v", result.Items)
	}
	data, ok := mockSink.Object("reports/report.xlsx")
	if !ok {
		t.Fatal("expected uploaded object")
	}
	if string(data) != "hello" {
		t.Fatalf("unexpected content: %q", string(data))
	}
}

func TestProcessTaskSkipsNonUploadableItems(t *testing.T) {
	task := message.DeliveryTask{
		TaskID:  "task-2",
		TempDir: t.TempDir(),
		Items: []message.DeliveryItem{{
			ItemID:       "item-1",
			SrcPath:      "missing.txt",
			DstPath:      "reports/missing.txt",
			Severity:     "error",
			UploadStatus: "pending",
		}},
	}

	mockSink := sink.NewMockSink()
	result, err := ProcessTask(context.Background(), task, mockSink)
	if err != nil {
		t.Fatalf("expected no error, got %v", err)
	}
	if result.Uploaded != 0 || result.Failed != 0 {
		t.Fatalf("unexpected result: %+v", result)
	}
}

func TestProcessTaskPartialFailureReturnsError(t *testing.T) {
	task := message.DeliveryTask{
		TaskID:  "task-3",
		TempDir: t.TempDir(),
		Items: []message.DeliveryItem{{
			ItemID:       "item-1",
			SrcPath:      "missing.txt",
			DstPath:      "reports/missing.txt",
			Severity:     "ok",
			UploadStatus: "pending",
		}},
	}

	mockSink := sink.NewMockSink()
	result, err := ProcessTask(context.Background(), task, mockSink)
	if err == nil {
		t.Fatal("expected error")
	}
	if result.Failed != 1 {
		t.Fatalf("unexpected result: %+v", result)
	}
	if len(result.Items) != 1 || result.Items[0].ItemID != "item-1" || result.Items[0].Status != "failed" || result.Items[0].Error == "" {
		t.Fatalf("unexpected item result: %+v", result.Items)
	}
}
