package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"smh_auto_upload/data-plane/internal/message"
	"smh_auto_upload/data-plane/internal/pipeline"
	"smh_auto_upload/data-plane/internal/sink"
)

type Config struct {
	InboxDir   string
	ResultsDir string
	SinkName   string
	Once       bool
}

type Worker struct {
	cfg  Config
	sink sink.Sink
}

func New(cfg Config, sinkImpl sink.Sink) *Worker {
	return &Worker{cfg: cfg, sink: sinkImpl}
}

// Run processes the current local outbox once. Kafka consumption will replace
// this directory scan without changing pipeline.ProcessTask.
func (w *Worker) Run(ctx context.Context) error {
	if err := os.MkdirAll(w.cfg.ResultsDir, 0o755); err != nil {
		return err
	}

	files, err := os.ReadDir(w.cfg.InboxDir)
	if err != nil {
		return err
	}

	for _, entry := range files {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		if err := w.processFile(ctx, filepath.Join(w.cfg.InboxDir, entry.Name())); err != nil {
			return err
		}
	}
	return nil
}

func (w *Worker) processFile(ctx context.Context, path string) error {
	raw, err := os.ReadFile(path)
	if err != nil {
		return err
	}

	var task message.DeliveryTask
	if err := json.Unmarshal(raw, &task); err != nil {
		return fmt.Errorf("decode %s: %w", path, err)
	}

	started := time.Now().UTC()
	result := message.DeliveryResult{
		Topic:     "delivery.results.v1",
		TaskID:    task.TaskID,
		Status:    "uploaded",
		StartedAt: started,
	}

	pipelineResult, err := pipeline.ProcessTask(ctx, task, w.sink)
	result.Uploaded = pipelineResult.Uploaded
	result.Failed = pipelineResult.Failed
	result.Processed = len(task.Items)
	result.EndedAt = time.Now().UTC()
	if err != nil {
		result.Status = "partial_failed"
		result.Error = err.Error()
	}

	out, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(w.cfg.ResultsDir, task.TaskID+".json"), out, 0o644)
}
