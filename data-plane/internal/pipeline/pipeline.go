package pipeline

import (
	"context"
	"fmt"
	"strings"
	"sync"
	"time"

	"smh_auto_upload/data-plane/internal/message"
	dmetrics "smh_auto_upload/data-plane/internal/metrics"
	"smh_auto_upload/data-plane/internal/sink"
	"smh_auto_upload/data-plane/internal/source"
)

type Result struct {
	TaskID   string
	Uploaded int
	Failed   int
	Items    []message.DeliveryResultItem
}

type Options struct {
	MaxItemConcurrency int
	BeforeUpload       func(context.Context, message.DeliveryTask, message.DeliveryItem) error
	Metrics            dmetrics.Recorder
}

// ProcessTask is deliberately narrow: it trusts the control plane's
// classification output and only moves bytes for uploadable items.
func ProcessTask(ctx context.Context, task message.DeliveryTask, sinkImpl sink.Sink) (Result, error) {
	return ProcessTaskWithResolver(ctx, task, sinkImpl, source.NewFileResolver())
}

func ProcessTaskWithResolver(
	ctx context.Context,
	task message.DeliveryTask,
	sinkImpl sink.Sink,
	resolver source.Resolver,
) (Result, error) {
	return ProcessTaskWithResolverOptions(ctx, task, sinkImpl, resolver, Options{MaxItemConcurrency: 1})
}

func ProcessTaskWithResolverOptions(
	ctx context.Context,
	task message.DeliveryTask,
	sinkImpl sink.Sink,
	resolver source.Resolver,
	opts Options,
) (Result, error) {
	result := Result{TaskID: task.TaskID}
	recorder := dmetrics.OrNoop(opts.Metrics)
	maxConcurrency := opts.MaxItemConcurrency
	if maxConcurrency <= 0 {
		maxConcurrency = 1
	}

	uploadItems := make([]message.DeliveryItem, 0, len(task.Items))
	for _, item := range task.Items {
		if item.UploadStatus != "pending" {
			continue
		}
		if item.Severity != "ok" && item.Severity != "warning" {
			continue
		}
		uploadItems = append(uploadItems, item)
	}

	itemResults := make([]message.DeliveryResultItem, len(uploadItems))
	sem := make(chan struct{}, maxConcurrency)
	var wg sync.WaitGroup
	for i, item := range uploadItems {
		wg.Add(1)
		sem <- struct{}{}
		go func(index int, uploadItem message.DeliveryItem) {
			defer wg.Done()
			defer func() { <-sem }()
			itemResults[index] = processItem(
				ctx,
				task,
				uploadItem,
				sinkImpl,
				resolver,
				opts.BeforeUpload,
				recorder,
			)
		}(i, item)
	}
	wg.Wait()

	for _, itemResult := range itemResults {
		if itemResult.Status == "failed" {
			result.Failed++
		} else if itemResult.Status == "uploaded" {
			result.Uploaded++
		}
		result.Items = append(result.Items, itemResult)
	}

	if result.Failed > 0 {
		return result, fmt.Errorf("task %s finished with %d failed items", task.TaskID, result.Failed)
	}
	return result, nil
}

func processItem(
	ctx context.Context,
	task message.DeliveryTask,
	item message.DeliveryItem,
	sinkImpl sink.Sink,
	resolver source.Resolver,
	beforeUpload func(context.Context, message.DeliveryTask, message.DeliveryItem) error,
	recorder dmetrics.Recorder,
) message.DeliveryResultItem {
	sourceName := sourceKindForTask(task)
	resolveStart := time.Now()
	src, err := resolver.Resolve(ctx, task, item)
	if err != nil {
		recorder.ObserveSourceRead(sourceName, dmetrics.StatusError, time.Since(resolveStart))
		return message.DeliveryResultItem{
			ItemID: item.ItemID,
			Status: "failed",
			Error:  err.Error(),
		}
	}
	sourceName = sourceKindFromPath(src.Path(), sourceName)
	src = dmetrics.WrapSource(sourceName, src, recorder)
	if beforeUpload != nil {
		if err := beforeUpload(ctx, task, item); err != nil {
			return message.DeliveryResultItem{
				ItemID: item.ItemID,
				Status: "failed",
				Error:  err.Error(),
			}
		}
	}
	uploadStart := time.Now()
	receipt, err := sinkImpl.Upload(ctx, src, sink.Meta{
		TaskID:  task.TaskID,
		ItemID:  item.ItemID,
		DstPath: item.DstPath,
	})
	recorder.ObserveSinkUpload(sinkImpl.Name(), dmetrics.StatusFromError(err), time.Since(uploadStart))
	if err != nil {
		return message.DeliveryResultItem{
			ItemID: item.ItemID,
			Status: "failed",
			Error:  err.Error(),
		}
	}
	return message.DeliveryResultItem{
		ItemID: item.ItemID,
		Status: "uploaded",
		Key:    receipt.Key,
		Size:   receipt.Size,
		SHA256: receipt.SHA256,
	}
}

func sourceKindForTask(task message.DeliveryTask) string {
	if task.Source != nil {
		switch task.Source.Type {
		case "", "object":
			return "object"
		default:
			return "other"
		}
	}
	return "file"
}

func sourceKindFromPath(path string, fallback string) string {
	switch {
	case strings.HasPrefix(path, "s3://"):
		return "object"
	case strings.HasPrefix(path, "memory://"):
		return "memory"
	case fallback != "":
		return fallback
	default:
		return "file"
	}
}
