package source

import (
	"errors"
	"io"
	"os"
	"path/filepath"
	"testing"
)

func TestFileSourcePathJoinsBaseAndRelativePath(t *testing.T) {
	src := NewFileSource("/tmp/task-1", "reports/report.txt")

	want := filepath.Join("/tmp/task-1", "reports/report.txt")
	if src.Path() != want {
		t.Fatalf("unexpected path: got %q want %q", src.Path(), want)
	}
}

func TestFileSourceOpenReadsFileContent(t *testing.T) {
	dir := t.TempDir()
	if err := os.MkdirAll(filepath.Join(dir, "reports"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "reports", "report.txt"), []byte("hello"), 0o644); err != nil {
		t.Fatal(err)
	}

	src := NewFileSource(dir, "reports/report.txt")
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
		t.Fatalf("unexpected data: %q", string(data))
	}
}

func TestFileSourceSizeReturnsFileSize(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "report.txt"), []byte("hello"), 0o644); err != nil {
		t.Fatal(err)
	}

	src := NewFileSource(dir, "report.txt")
	size, err := src.Size()
	if err != nil {
		t.Fatalf("source size: %v", err)
	}
	if size != 5 {
		t.Fatalf("unexpected size: got %d want 5", size)
	}
}

func TestFileSourceOpenMissingFileReturnsPathError(t *testing.T) {
	src := NewFileSource(t.TempDir(), "missing.txt")

	reader, err := src.Open()
	if err == nil {
		if reader != nil {
			reader.Close()
		}
		t.Fatal("expected error")
	}
	if !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("expected not-exist error, got %v", err)
	}
}

func TestFileSourceSizeMissingFileReturnsPathError(t *testing.T) {
	src := NewFileSource(t.TempDir(), "missing.txt")

	_, err := src.Size()
	if err == nil {
		t.Fatal("expected error")
	}
	if !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("expected not-exist error, got %v", err)
	}
}
