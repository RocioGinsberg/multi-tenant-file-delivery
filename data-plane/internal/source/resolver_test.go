package source

import (
	"archive/zip"
	"bytes"
	"context"
	"io"
	"strings"
	"testing"

	"smh_auto_upload/data-plane/internal/message"
)

type fakeObjectFetcher struct {
	data []byte
}

func (f fakeObjectFetcher) GetObject(context.Context, string, string) ([]byte, error) {
	return f.data, nil
}

func TestZipArchiveResolverOpensSourcePathFromArchive(t *testing.T) {
	archive := buildZip(t, map[string]string{
		"acme/report.txt": "hello",
	})
	resolver := NewZipArchiveResolver(fakeObjectFetcher{data: archive})

	src, err := resolver.Resolve(context.Background(), message.DeliveryTask{
		TaskID: "task-1",
		Source: &message.SourceRef{
			Bucket: "auto-upload-staging",
			Key:    "staged/tasks/task-1/archive.zip",
		},
	}, message.DeliveryItem{
		ItemID:     "item-1",
		SourcePath: "acme/report.txt",
	})
	if err != nil {
		t.Fatalf("resolve source: %v", err)
	}

	reader, err := src.Open()
	if err != nil {
		t.Fatalf("open source: %v", err)
	}
	defer reader.Close()
	data, err := io.ReadAll(reader)
	if err != nil {
		t.Fatalf("read source: %v", err)
	}
	if string(data) != "hello" {
		t.Fatalf("unexpected source data: %q", string(data))
	}
	if !strings.Contains(src.Path(), "archive.zip#acme/report.txt") {
		t.Fatalf("unexpected source path: %q", src.Path())
	}
}

func TestZipArchiveResolverFallsBackToSrcPath(t *testing.T) {
	archive := buildZip(t, map[string]string{
		"report.txt": "hello",
	})
	resolver := NewZipArchiveResolver(fakeObjectFetcher{data: archive})

	src, err := resolver.Resolve(context.Background(), message.DeliveryTask{
		TaskID: "task-1",
		Source: &message.SourceRef{
			Bucket: "auto-upload-staging",
			Key:    "staged/tasks/task-1/archive.zip",
		},
	}, message.DeliveryItem{
		ItemID:  "item-1",
		SrcPath: "report.txt",
	})
	if err != nil {
		t.Fatalf("resolve source: %v", err)
	}
	size, err := src.Size()
	if err != nil {
		t.Fatalf("source size: %v", err)
	}
	if size != 5 {
		t.Fatalf("unexpected size: got %d want 5", size)
	}
}

func buildZip(t *testing.T, files map[string]string) []byte {
	t.Helper()
	var buf bytes.Buffer
	writer := zip.NewWriter(&buf)
	for name, content := range files {
		fileWriter, err := writer.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := fileWriter.Write([]byte(content)); err != nil {
			t.Fatal(err)
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}
