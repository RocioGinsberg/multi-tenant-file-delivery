package worker

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"smh_auto_upload/data-plane/internal/message"
	dmetrics "smh_auto_upload/data-plane/internal/metrics"
	"smh_auto_upload/data-plane/internal/sink"
	"smh_auto_upload/data-plane/internal/transport"
)

const testTraceparent = "00-1234567890abcdef1234567890abcdef-1234567890abcdef-01"

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
	if len(result.Items) != 1 || result.Items[0].ItemID != "item-1" || result.Items[0].Status != "uploaded" || result.Items[0].SHA256 != "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824" {
		t.Fatalf("unexpected item result: %+v", result.Items)
	}
	data, ok := mockSink.Object("reports/report.txt")
	if !ok {
		t.Fatal("expected mock sink object")
	}
	if string(data) != "hello" {
		t.Fatalf("unexpected object data: %q", string(data))
	}
}

func TestRunProcessesTaskWithTraceparent(t *testing.T) {
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
		TaskID:      "task-trace",
		TempDir:     tempDir,
		BucketName:  "auto-upload-dev",
		Traceparent: testTraceparent,
		CreatedAt:   time.Date(2026, 5, 17, 10, 0, 0, 0, time.UTC),
		Items: []message.DeliveryItem{{
			ItemID:       "item-1",
			SrcPath:      "report.txt",
			DstPath:      "reports/report.txt",
			Severity:     "ok",
			UploadStatus: "pending",
		}},
	}
	writeTaskFile(t, inboxDir, task)

	w := New(Config{InboxDir: inboxDir, ResultsDir: resultsDir, SinkName: "mock", Once: true}, sink.NewMockSink())
	if err := w.Run(context.Background()); err != nil {
		t.Fatalf("run worker: %v", err)
	}

	result := readResultFile(t, resultsDir, "task-trace")
	if result.Status != "uploaded" || result.Uploaded != 1 {
		t.Fatalf("unexpected result: %+v", result)
	}
}

func TestRunRecordsMetricsForWorkerPath(t *testing.T) {
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
		TaskID:     "task-metrics",
		TempDir:    tempDir,
		BucketName: "auto-upload-dev",
		CreatedAt:  time.Date(2026, 5, 17, 10, 0, 0, 0, time.UTC),
		Items: []message.DeliveryItem{{
			ItemID:       "item-1",
			SrcPath:      "report.txt",
			DstPath:      "reports/report.txt",
			Severity:     "ok",
			UploadStatus: "pending",
		}},
	}
	writeTaskFile(t, inboxDir, task)

	recorder := &recordingMetrics{}
	w := New(
		Config{
			InboxDir:      inboxDir,
			ResultsDir:    resultsDir,
			SinkName:      "mock",
			TransportName: "file",
			Once:          true,
			Metrics:       recorder,
		},
		sink.NewMockSink(),
	)

	if err := w.Run(context.Background()); err != nil {
		t.Fatalf("run worker: %v", err)
	}

	if !recorder.has(metricEvent{operation: "task_consume", transport: "file", status: dmetrics.StatusSuccess}) {
		t.Fatalf("missing task consume metric: %+v", recorder.events)
	}
	if !recorder.has(metricEvent{operation: "source_read", source: "file", status: dmetrics.StatusSuccess}) {
		t.Fatalf("missing source read metric: %+v", recorder.events)
	}
	if !recorder.has(metricEvent{operation: "sink_upload", sink: "mock", status: dmetrics.StatusSuccess}) {
		t.Fatalf("missing sink upload metric: %+v", recorder.events)
	}
	if !recorder.has(metricEvent{operation: "result_publish", transport: "file", status: dmetrics.StatusSuccess}) {
		t.Fatalf("missing result publish metric: %+v", recorder.events)
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
	if len(result.Items) != 1 || result.Items[0].ItemID != "item-1" || result.Items[0].Status != "failed" || result.Items[0].Error == "" {
		t.Fatalf("unexpected item result: %+v", result.Items)
	}
}

func TestRunWritesLimiterFailureResult(t *testing.T) {
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
		TaskID:         "task-limited",
		IdempotencyKey: "idem-limited",
		TempDir:        tempDir,
		BucketName:     "auto-upload-dev",
		CreatedAt:      time.Date(2026, 5, 17, 10, 0, 0, 0, time.UTC),
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
	recorder := &recordingMetrics{}
	w := New(
		Config{
			InboxDir:      inboxDir,
			ResultsDir:    resultsDir,
			SinkName:      "mock",
			Once:          true,
			UploadLimiter: failingLimiter{err: errors.New("redis limiter acquire: rate limited")},
			LimiterKey:    "sink:s3",
			Metrics:       recorder,
		},
		mockSink,
	)
	if err := w.Run(context.Background()); err != nil {
		t.Fatalf("run worker: %v", err)
	}

	result := readResultFile(t, resultsDir, "task-limited")
	if result.Status != "partial_failed" || result.Uploaded != 0 || result.Failed != 1 {
		t.Fatalf("unexpected result: %+v", result)
	}
	if len(result.Items) != 1 || !strings.Contains(result.Items[0].Error, "redis limiter acquire") {
		t.Fatalf("expected item limiter error, got %+v", result.Items)
	}
	if _, ok := mockSink.Object("reports/report.txt"); ok {
		t.Fatal("expected upload to be blocked by limiter")
	}
	if !recorder.has(metricEvent{operation: "limiter_acquire", status: dmetrics.StatusError}) {
		t.Fatalf("missing limiter acquire metric: %+v", recorder.events)
	}
}

func TestRunLoopsWhenOnceIsFalse(t *testing.T) {
	task := message.DeliveryTask{
		TaskID: "task-loop",
		Items: []message.DeliveryItem{{
			ItemID:       "item-1",
			SourcePath:   "report.txt",
			DstPath:      "reports/report.txt",
			Severity:     "ok",
			UploadStatus: "pending",
		}},
	}
	consumer := &loopConsumer{
		batches: [][]transport.TaskMessage{
			{transport.NewTaskMessage(task)},
			{},
		},
		finalErr: errors.New("stop loop"),
	}
	producer := &recordingProducer{}
	w := NewWithTransport(
		Config{SinkName: "mock", Once: false},
		sink.NewMockSink(),
		consumer,
		producer,
	)

	err := w.Run(context.Background())
	if err == nil || err.Error() != "stop loop" {
		t.Fatalf("unexpected run error: %v", err)
	}
	if consumer.calls != 3 {
		t.Fatalf("expected repeated consume calls, got %d", consumer.calls)
	}
	if len(producer.results) != 1 || producer.results[0].TaskID != "task-loop" {
		t.Fatalf("unexpected produced results: %+v", producer.results)
	}
}

type loopConsumer struct {
	batches  [][]transport.TaskMessage
	finalErr error
	calls    int
}

func (c *loopConsumer) Consume(context.Context) ([]transport.TaskMessage, error) {
	c.calls++
	if len(c.batches) == 0 {
		return nil, c.finalErr
	}
	batch := c.batches[0]
	c.batches = c.batches[1:]
	return batch, nil
}

type recordingProducer struct {
	results []message.DeliveryResult
}

type metricEvent struct {
	operation string
	transport string
	source    string
	sink      string
	status    string
}

type recordingMetrics struct {
	mu     sync.Mutex
	events []metricEvent
}

func (r *recordingMetrics) ObserveTaskConsume(transport, status string, _ time.Duration) {
	r.append(metricEvent{operation: "task_consume", transport: transport, status: status})
}

func (r *recordingMetrics) ObserveSourceRead(source, status string, _ time.Duration) {
	r.append(metricEvent{operation: "source_read", source: source, status: status})
}

func (r *recordingMetrics) ObserveSinkUpload(sinkName, status string, _ time.Duration) {
	r.append(metricEvent{operation: "sink_upload", sink: sinkName, status: status})
}

func (r *recordingMetrics) ObserveResultPublish(transport, status string, _ time.Duration) {
	r.append(metricEvent{operation: "result_publish", transport: transport, status: status})
}

func (r *recordingMetrics) ObserveLimiterAcquire(status string, _ time.Duration) {
	r.append(metricEvent{operation: "limiter_acquire", status: status})
}

func (r *recordingMetrics) append(event metricEvent) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.events = append(r.events, event)
}

func (r *recordingMetrics) has(expected metricEvent) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, event := range r.events {
		if event == expected {
			return true
		}
	}
	return false
}

type failingLimiter struct {
	err error
}

func (l failingLimiter) Allow(context.Context, string) error {
	return l.err
}

func (p *recordingProducer) Produce(_ context.Context, result message.DeliveryResult) error {
	p.results = append(p.results, result)
	return nil
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
