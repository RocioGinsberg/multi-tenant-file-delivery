package main

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
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

func TestRunRejectsUnsupportedSourceMode(t *testing.T) {
	var stderr bytes.Buffer
	err := run(context.Background(), []string{"-source-mode", "missing"}, &stderr)
	if err == nil {
		t.Fatal("expected error")
	}
}

func TestRunAcceptsRedisLimiterFlags(t *testing.T) {
	dir := t.TempDir()
	inboxDir := filepath.Join(dir, "delivery.tasks.v1")
	resultsDir := filepath.Join(dir, "delivery.results.v1")

	var stderr bytes.Buffer
	err := run(context.Background(), []string{
		"-inbox", inboxDir,
		"-results", resultsDir,
		"-redis-url", "redis://localhost:6379/0",
		"-redis-limiter-enabled",
	}, &stderr)
	if err != nil {
		t.Fatalf("run worker: %v\nstderr: %s", err, stderr.String())
	}
}

func TestRunAcceptsDefaultObservabilityFlags(t *testing.T) {
	dir := t.TempDir()
	inboxDir := filepath.Join(dir, "delivery.tasks.v1")
	resultsDir := filepath.Join(dir, "delivery.results.v1")

	var stderr bytes.Buffer
	err := run(context.Background(), []string{
		"-inbox", inboxDir,
		"-results", resultsDir,
	}, &stderr)
	if err != nil {
		t.Fatalf("run worker: %v\nstderr: %s", err, stderr.String())
	}
}

func TestRunAcceptsEnabledObservabilityConfig(t *testing.T) {
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
		TaskID:  "task-metrics-cli",
		TempDir: tempDir,
		Items: []message.DeliveryItem{{
			ItemID:       "item-1",
			SrcPath:      "report.txt",
			DstPath:      "reports/report.txt",
			Severity:     "ok",
			UploadStatus: "pending",
		}},
	}
	raw, err := json.Marshal(task)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(inboxDir, "task-metrics-cli.json"), raw, 0o644); err != nil {
		t.Fatal(err)
	}

	var stderr bytes.Buffer
	err = run(context.Background(), []string{
		"-inbox", inboxDir,
		"-results", resultsDir,
		"-metrics-enabled",
		"-metrics-listen-addr", "127.0.0.1:0",
		"-tracing-enabled",
		"-tracing-service-name", "data-plane-test",
		"-tracing-otlp-endpoint", "http://otel-collector:4318",
	}, &stderr)
	if err != nil {
		t.Fatalf("run worker: %v\nstderr: %s", err, stderr.String())
	}
}

func TestRunRejectsInvalidRedisURL(t *testing.T) {
	var stderr bytes.Buffer
	err := run(context.Background(), []string{"-redis-url", "localhost:6379"}, &stderr)
	if err == nil {
		t.Fatal("expected error")
	}
	if !strings.Contains(err.Error(), "invalid redis url") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestRunRejectsNonPositiveItemConcurrency(t *testing.T) {
	var stderr bytes.Buffer
	err := run(context.Background(), []string{"-item-concurrency", "0"}, &stderr)
	if err == nil {
		t.Fatal("expected error")
	}
}

func TestRunRejectsInvalidRedisLimiterConfig(t *testing.T) {
	var stderr bytes.Buffer
	err := run(context.Background(), []string{"-redis-limiter-limit", "0"}, &stderr)
	if err == nil {
		t.Fatal("expected limit error")
	}
	if !strings.Contains(err.Error(), "redis limiter limit") {
		t.Fatalf("unexpected error: %v", err)
	}

	err = run(context.Background(), []string{"-redis-limiter-window", "0s"}, &stderr)
	if err == nil {
		t.Fatal("expected window error")
	}
	if !strings.Contains(err.Error(), "redis limiter window") {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestRunRejectsInvalidObservabilityConfigWhenEnabled(t *testing.T) {
	var stderr bytes.Buffer
	err := run(context.Background(), []string{
		"-metrics-enabled",
		"-metrics-listen-addr", "localhost",
	}, &stderr)
	if err == nil {
		t.Fatal("expected metrics listen addr error")
	}
	if !strings.Contains(err.Error(), "invalid metrics listen addr") {
		t.Fatalf("unexpected error: %v", err)
	}

	err = run(context.Background(), []string{
		"-tracing-enabled",
		"-tracing-service-name", "",
	}, &stderr)
	if err == nil {
		t.Fatal("expected tracing service name error")
	}
	if !strings.Contains(err.Error(), "tracing service name") {
		t.Fatalf("unexpected error: %v", err)
	}

	err = run(context.Background(), []string{
		"-tracing-enabled",
		"-tracing-otlp-endpoint", "localhost:4318",
	}, &stderr)
	if err == nil {
		t.Fatal("expected tracing otlp endpoint error")
	}
	if !strings.Contains(err.Error(), "invalid tracing otlp endpoint") {
		t.Fatalf("unexpected error: %v", err)
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

func TestRunKafkaStartupCheckFailsFast(t *testing.T) {
	var stderr bytes.Buffer
	err := run(context.Background(), []string{
		"-transport", "kafka",
		"-kafka-brokers", "127.0.0.1:1",
		"-startup-check-timeout", "100ms",
	}, &stderr)
	if err == nil {
		t.Fatal("expected error")
	}
	if !strings.Contains(err.Error(), "connect kafka broker") {
		t.Fatalf("unexpected error: %v", err)
	}
}
