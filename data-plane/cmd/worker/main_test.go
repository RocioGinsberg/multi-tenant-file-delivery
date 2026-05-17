package main

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"smh_auto_upload/data-plane/internal/message"
)

func TestRunProcessesLocalOutboxWithMockSink(t *testing.T) {
	dir := t.TempDir()
	inboxDir := filepath.Join(dir, "delivery.tasks.v1")
	resultsDir := filepath.Join(dir, "delivery.results.v1")
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
		TaskID:          "task-cli",
		IdempotencyKey:  "idem-cli",
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
	raw, err := json.Marshal(task)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(inboxDir, "task-cli.json"), raw, 0o644); err != nil {
		t.Fatal(err)
	}

	var stderr bytes.Buffer
	err = run(context.Background(), []string{
		"-inbox", inboxDir,
		"-results", resultsDir,
		"-sink", "mock",
	}, &stderr)
	if err != nil {
		t.Fatalf("run worker: %v\nstderr: %s", err, stderr.String())
	}

	resultRaw, err := os.ReadFile(filepath.Join(resultsDir, "task-cli.json"))
	if err != nil {
		t.Fatal(err)
	}
	var result message.DeliveryResult
	if err := json.Unmarshal(resultRaw, &result); err != nil {
		t.Fatal(err)
	}
	if result.Status != "uploaded" || result.Uploaded != 1 || result.Failed != 0 || result.Processed != 1 {
		t.Fatalf("unexpected result: %+v", result)
	}
}

func TestRunRejectsUnsupportedSink(t *testing.T) {
	var stderr bytes.Buffer
	err := run(context.Background(), []string{"-sink", "missing"}, &stderr)
	if err == nil {
		t.Fatal("expected error")
	}
}

func TestRunRejectsKafkaTransportWithoutBrokers(t *testing.T) {
	var stderr bytes.Buffer
	err := run(context.Background(), []string{
		"-transport", "kafka",
		"-kafka-brokers", " , ",
	}, &stderr)
	if err == nil {
		t.Fatal("expected error")
	}
}
