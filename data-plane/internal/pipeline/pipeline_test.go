package pipeline

import (
	"context"
	"io"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"smh_auto_upload/data-plane/internal/message"
	"smh_auto_upload/data-plane/internal/sink"
	"smh_auto_upload/data-plane/internal/source"
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

func TestProcessTaskWithResolverUploadsWithoutTempDir(t *testing.T) {
	task := message.DeliveryTask{
		TaskID: "task-source",
		Items: []message.DeliveryItem{{
			ItemID:       "item-1",
			SourcePath:   "report.txt",
			DstPath:      "reports/report.txt",
			Severity:     "ok",
			UploadStatus: "pending",
		}},
	}

	mockSink := sink.NewMockSink()
	result, err := ProcessTaskWithResolver(
		context.Background(),
		task,
		mockSink,
		staticResolver{src: source.NewMemorySource("memory://report.txt", []byte("hello"))},
	)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Uploaded != 1 || result.Failed != 0 {
		t.Fatalf("unexpected result: %+v", result)
	}
	data, ok := mockSink.Object("reports/report.txt")
	if !ok {
		t.Fatal("expected uploaded object")
	}
	if string(data) != "hello" {
		t.Fatalf("unexpected content: %q", string(data))
	}
}

func TestProcessTaskWithResolverOptionsLimitsItemConcurrency(t *testing.T) {
	task := message.DeliveryTask{
		TaskID: "task-concurrency",
		Items: []message.DeliveryItem{
			{
				ItemID:       "item-1",
				SourcePath:   "one.txt",
				DstPath:      "reports/one.txt",
				Severity:     "ok",
				UploadStatus: "pending",
			},
			{
				ItemID:       "item-2",
				SourcePath:   "two.txt",
				DstPath:      "reports/two.txt",
				Severity:     "ok",
				UploadStatus: "pending",
			},
			{
				ItemID:       "item-3",
				SourcePath:   "three.txt",
				DstPath:      "reports/three.txt",
				Severity:     "ok",
				UploadStatus: "pending",
			},
		},
	}
	sinkImpl := &trackingSink{
		reachedTwo: make(chan struct{}),
		release:    make(chan struct{}),
	}

	done := make(chan struct{})
	var result Result
	var err error
	go func() {
		defer close(done)
		result, err = ProcessTaskWithResolverOptions(
			context.Background(),
			task,
			sinkImpl,
			staticResolver{src: source.NewMemorySource("memory://report.txt", []byte("hello"))},
			Options{MaxItemConcurrency: 2},
		)
	}()

	select {
	case <-sinkImpl.reachedTwo:
	case <-time.After(time.Second):
		t.Fatal("expected two concurrent uploads")
	}
	close(sinkImpl.release)

	<-done
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Uploaded != 3 || result.Failed != 0 {
		t.Fatalf("unexpected result: %+v", result)
	}
	if sinkImpl.maxActive != 2 {
		t.Fatalf("unexpected max concurrency: got %d want 2", sinkImpl.maxActive)
	}
}

type staticResolver struct {
	src source.Source
}

func (r staticResolver) Resolve(context.Context, message.DeliveryTask, message.DeliveryItem) (source.Source, error) {
	return r.src, nil
}

type trackingSink struct {
	mu         sync.Mutex
	active     int
	maxActive  int
	reachedTwo chan struct{}
	release    chan struct{}
	closed     bool
}

func (s *trackingSink) init() {
	if s.reachedTwo == nil {
		s.reachedTwo = make(chan struct{})
	}
	if s.release == nil {
		s.release = make(chan struct{})
	}
}

func (s *trackingSink) Name() string { return "tracking" }

func (s *trackingSink) Upload(_ context.Context, src sink.Source, meta sink.Meta) (sink.Receipt, error) {
	s.mu.Lock()
	s.init()
	s.active++
	if s.active > s.maxActive {
		s.maxActive = s.active
	}
	if s.active == 2 && !s.closed {
		close(s.reachedTwo)
		s.closed = true
	}
	release := s.release
	s.mu.Unlock()

	<-release

	reader, err := src.Open()
	if err != nil {
		return sink.Receipt{}, err
	}
	_, err = io.ReadAll(reader)
	closeErr := reader.Close()

	s.mu.Lock()
	s.active--
	s.mu.Unlock()

	if err != nil {
		return sink.Receipt{}, err
	}
	if closeErr != nil {
		return sink.Receipt{}, closeErr
	}
	return sink.Receipt{Key: meta.DstPath, Size: 5, SHA256: "sha256"}, nil
}

func (s *trackingSink) Close() error { return nil }
