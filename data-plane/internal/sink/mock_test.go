package sink

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"smh_auto_upload/data-plane/internal/source"
)

func TestMockSinkReturnsChecksumReceipt(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "report.txt"), []byte("hello"), 0o644); err != nil {
		t.Fatal(err)
	}

	mockSink := NewMockSink()
	receipt, err := mockSink.Upload(context.Background(), source.NewFileSource(dir, "report.txt"), Meta{
		TaskID:  "task-1",
		ItemID:  "item-1",
		DstPath: "reports/report.txt",
	})
	if err != nil {
		t.Fatalf("upload object: %v", err)
	}

	if receipt.Key != "reports/report.txt" || receipt.Size != 5 || receipt.SHA256 != "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824" {
		t.Fatalf("unexpected receipt: %+v", receipt)
	}
}
