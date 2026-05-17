package transport

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"smh_auto_upload/data-plane/internal/message"
)

type FileSpool struct {
	InboxDir   string
	ResultsDir string
}

func NewFileSpool(inboxDir, resultsDir string) *FileSpool {
	return &FileSpool{InboxDir: inboxDir, ResultsDir: resultsDir}
}

func (s *FileSpool) Consume(ctx context.Context) ([]message.DeliveryTask, error) {
	_ = ctx
	files, err := os.ReadDir(s.InboxDir)
	if err != nil {
		return nil, err
	}

	tasks := make([]message.DeliveryTask, 0, len(files))
	for _, entry := range files {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		path := filepath.Join(s.InboxDir, entry.Name())
		raw, err := os.ReadFile(path)
		if err != nil {
			return nil, err
		}

		var task message.DeliveryTask
		if err := json.Unmarshal(raw, &task); err != nil {
			return nil, fmt.Errorf("decode %s: %w", path, err)
		}
		tasks = append(tasks, task)
	}
	return tasks, nil
}

func (s *FileSpool) Produce(ctx context.Context, result message.DeliveryResult) error {
	_ = ctx
	if err := os.MkdirAll(s.ResultsDir, 0o755); err != nil {
		return err
	}
	out, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(s.ResultsDir, result.TaskID+".json"), out, 0o644)
}
