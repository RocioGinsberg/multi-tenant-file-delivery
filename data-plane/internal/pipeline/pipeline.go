package pipeline

import (
	"context"
	"fmt"

	"smh_auto_upload/data-plane/internal/message"
	"smh_auto_upload/data-plane/internal/sink"
	"smh_auto_upload/data-plane/internal/source"
)

type Result struct {
	TaskID   string
	Uploaded int
	Failed   int
	Items    []message.DeliveryResultItem
}

// ProcessTask is deliberately narrow: it trusts the control plane's
// classification output and only moves bytes for uploadable items.
func ProcessTask(ctx context.Context, task message.DeliveryTask, sinkImpl sink.Sink) (Result, error) {
	result := Result{TaskID: task.TaskID}

	for _, item := range task.Items {
		if item.UploadStatus != "pending" {
			continue
		}
		if item.Severity != "ok" && item.Severity != "warning" {
			continue
		}

		src := source.NewFileSource(task.TempDir, item.SrcPath)
		receipt, err := sinkImpl.Upload(ctx, src, sink.Meta{
			TaskID:  task.TaskID,
			ItemID:  item.ItemID,
			DstPath: item.DstPath,
		})
		if err != nil {
			result.Failed++
			result.Items = append(result.Items, message.DeliveryResultItem{
				ItemID: item.ItemID,
				Status: "failed",
				Error:  err.Error(),
			})
			continue
		}
		result.Uploaded++
		result.Items = append(result.Items, message.DeliveryResultItem{
			ItemID: item.ItemID,
			Status: "uploaded",
			Key:    receipt.Key,
			Size:   receipt.Size,
			SHA256: receipt.SHA256,
		})
	}

	if result.Failed > 0 {
		return result, fmt.Errorf("task %s finished with %d failed items", task.TaskID, result.Failed)
	}
	return result, nil
}
