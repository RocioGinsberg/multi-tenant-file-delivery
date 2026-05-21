package pipeline

import (
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strconv"
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

func TestProcessTaskBeforeUploadFailureSkipsSinkUpload(t *testing.T) {
	task := message.DeliveryTask{
		TaskID: "task-limited",
		Items: []message.DeliveryItem{{
			ItemID:       "item-1",
			SourcePath:   "one.txt",
			DstPath:      "reports/one.txt",
			Severity:     "ok",
			UploadStatus: "pending",
		}},
	}
	limited := errors.New("rate limited")
	sinkImpl := sink.NewMockSink()

	result, err := ProcessTaskWithResolverOptions(
		context.Background(),
		task,
		sinkImpl,
		staticResolver{src: source.NewMemorySource("memory://one.txt", []byte("hello"))},
		Options{
			MaxItemConcurrency: 1,
			BeforeUpload: func(context.Context, message.DeliveryTask, message.DeliveryItem) error {
				return limited
			},
		},
	)

	if err == nil {
		t.Fatal("expected partial failure")
	}
	if result.Uploaded != 0 || result.Failed != 1 {
		t.Fatalf("unexpected result: %+v", result)
	}
	if result.Items[0].Status != "failed" || result.Items[0].Error != limited.Error() {
		t.Fatalf("unexpected item result: %+v", result.Items[0])
	}
	if _, ok := sinkImpl.Object("reports/one.txt"); ok {
		t.Fatal("expected sink upload to be skipped")
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

func BenchmarkProcessTaskMockSink(b *testing.B) {
	cases := []struct {
		name            string
		items           int
		size            int
		itemConcurrency int
	}{
		{name: "10_items_16KiB_c1", items: 10, size: 16 * 1024, itemConcurrency: 1},
		{name: "10_items_16KiB_c4", items: 10, size: 16 * 1024, itemConcurrency: 4},
		{name: "100_items_16KiB_c1", items: 100, size: 16 * 1024, itemConcurrency: 1},
		{name: "100_items_16KiB_c4", items: 100, size: 16 * 1024, itemConcurrency: 4},
		{name: "1000_items_16KiB_c1", items: 1000, size: 16 * 1024, itemConcurrency: 1},
		{name: "1000_items_16KiB_c4", items: 1000, size: 16 * 1024, itemConcurrency: 4},
		{name: "10_items_1MiB_c1", items: 10, size: 1024 * 1024, itemConcurrency: 1},
		{name: "10_items_1MiB_c4", items: 10, size: 1024 * 1024, itemConcurrency: 4},
		{name: "100_items_1MiB_c1", items: 100, size: 1024 * 1024, itemConcurrency: 1},
		{name: "100_items_1MiB_c4", items: 100, size: 1024 * 1024, itemConcurrency: 4},
		{name: "1000_items_1MiB_c1", items: 1000, size: 1024 * 1024, itemConcurrency: 1},
		{name: "1000_items_1MiB_c4", items: 1000, size: 1024 * 1024, itemConcurrency: 4},
	}

	for _, tc := range cases {
		b.Run(tc.name, func(b *testing.B) {
			task := benchmarkTask(tc.items)
			resolver := benchmarkResolver{payload: bytesOfSize(tc.size)}
			b.SetBytes(int64(tc.items * tc.size))
			b.ReportAllocs()
			b.ResetTimer()

			for i := 0; i < b.N; i++ {
				result, err := ProcessTaskWithResolverOptions(
					context.Background(),
					task,
					sink.NewMockSink(),
					resolver,
					Options{MaxItemConcurrency: tc.itemConcurrency},
				)
				if err != nil {
					b.Fatal(err)
				}
				if result.Uploaded != tc.items || result.Failed != 0 {
					b.Fatalf("unexpected result: %+v", result)
				}
			}
		})
	}
}

func benchmarkTask(items int) message.DeliveryTask {
	task := message.DeliveryTask{
		TaskID: "bench-task",
		Items:  make([]message.DeliveryItem, 0, items),
	}
	for i := 0; i < items; i++ {
		itemID := "item-" + strconv.Itoa(i)
		task.Items = append(task.Items, message.DeliveryItem{
			ItemID:       itemID,
			SourcePath:   itemID + ".bin",
			DstPath:      "bench/" + itemID + ".bin",
			Severity:     "ok",
			UploadStatus: "pending",
		})
	}
	return task
}

func bytesOfSize(size int) []byte {
	payload := make([]byte, size)
	for i := range payload {
		payload[i] = byte(i % 251)
	}
	return payload
}

type benchmarkResolver struct {
	payload []byte
}

func (r benchmarkResolver) Resolve(_ context.Context, _ message.DeliveryTask, item message.DeliveryItem) (source.Source, error) {
	return source.NewMemorySource("memory://"+item.SourcePath, r.payload), nil
}
