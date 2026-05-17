package sink

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"smh_auto_upload/data-plane/internal/source"
)

func TestS3SinkUploadsObject(t *testing.T) {
	var gotPath string
	var gotBody string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Errorf("read request body: %v", err)
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		gotBody = string(body)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "report.txt"), []byte("hello"), 0o644); err != nil {
		t.Fatal(err)
	}

	s3Sink, err := NewS3Sink(context.Background(), S3Config{
		Endpoint:        server.URL,
		Region:          "us-east-1",
		Bucket:          "auto-upload-dev",
		AccessKeyID:     "test-key",
		SecretAccessKey: "test-secret",
		UsePathStyle:    true,
	})
	if err != nil {
		t.Fatalf("create s3 sink: %v", err)
	}

	receipt, err := s3Sink.Upload(context.Background(), source.NewFileSource(dir, "report.txt"), Meta{
		TaskID:  "task-1",
		ItemID:  "item-1",
		DstPath: "reports/report.txt",
	})
	if err != nil {
		t.Fatalf("upload object: %v", err)
	}

	if gotPath != "/auto-upload-dev/reports/report.txt" {
		t.Fatalf("unexpected request path: %q", gotPath)
	}
	if gotBody != "hello" {
		t.Fatalf("unexpected request body: %q", gotBody)
	}
	if receipt.Key != "reports/report.txt" || receipt.Size != 5 {
		t.Fatalf("unexpected receipt: %+v", receipt)
	}
}

func TestNewS3SinkRequiresBucket(t *testing.T) {
	_, err := NewS3Sink(context.Background(), S3Config{
		Region:          "us-east-1",
		AccessKeyID:     "test-key",
		SecretAccessKey: "test-secret",
	})
	if err == nil {
		t.Fatal("expected error")
	}
}
