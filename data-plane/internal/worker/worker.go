package worker

import (
	"context"
	"time"

	"smh_auto_upload/data-plane/internal/message"
	"smh_auto_upload/data-plane/internal/pipeline"
	"smh_auto_upload/data-plane/internal/sink"
	"smh_auto_upload/data-plane/internal/source"
	"smh_auto_upload/data-plane/internal/transport"
)

type Config struct {
	InboxDir           string
	ResultsDir         string
	SinkName           string
	Once               bool
	MaxItemConcurrency int
	UploadLimiter      UploadLimiter
	LimiterKey         string
}

type UploadLimiter interface {
	Allow(ctx context.Context, key string) error
}

type Worker struct {
	cfg     Config
	sink    sink.Sink
	source  source.Resolver
	tasks   transport.TaskConsumer
	results transport.ResultProducer
}

func New(cfg Config, sinkImpl sink.Sink) *Worker {
	spool := transport.NewFileSpool(cfg.InboxDir, cfg.ResultsDir)
	return NewWithTransport(cfg, sinkImpl, spool, spool)
}

func NewWithTransport(
	cfg Config,
	sinkImpl sink.Sink,
	tasks transport.TaskConsumer,
	results transport.ResultProducer,
) *Worker {
	return NewWithTransportAndResolver(cfg, sinkImpl, source.NewFileResolver(), tasks, results)
}

func NewWithTransportAndResolver(
	cfg Config,
	sinkImpl sink.Sink,
	sourceResolver source.Resolver,
	tasks transport.TaskConsumer,
	results transport.ResultProducer,
) *Worker {
	return &Worker{cfg: cfg, sink: sinkImpl, source: sourceResolver, tasks: tasks, results: results}
}

// Run processes tasks from the configured transport. Kafka can replace the
// file-spool transport without changing pipeline.ProcessTask.
func (w *Worker) Run(ctx context.Context) error {
	for {
		tasks, err := w.tasks.Consume(ctx)
		if err != nil {
			return err
		}

		for _, taskMessage := range tasks {
			if err := w.processTask(ctx, taskMessage.Task); err != nil {
				return err
			}
			if err := taskMessage.Ack(ctx); err != nil {
				return err
			}
		}
		if w.cfg.Once {
			return nil
		}
	}
}

func (w *Worker) processTask(ctx context.Context, task message.DeliveryTask) error {
	started := time.Now().UTC()
	result := message.DeliveryResult{
		Topic:     "delivery.results.v1",
		TaskID:    task.TaskID,
		Status:    "uploaded",
		StartedAt: started,
	}

	options := pipeline.Options{MaxItemConcurrency: w.cfg.MaxItemConcurrency}
	if w.cfg.UploadLimiter != nil {
		limiterKey := w.cfg.LimiterKey
		if limiterKey == "" {
			limiterKey = "global"
		}
		options.BeforeUpload = func(ctx context.Context, _ message.DeliveryTask, _ message.DeliveryItem) error {
			return w.cfg.UploadLimiter.Allow(ctx, limiterKey)
		}
	}

	pipelineResult, err := pipeline.ProcessTaskWithResolverOptions(
		ctx,
		task,
		w.sink,
		w.source,
		options,
	)
	result.Uploaded = pipelineResult.Uploaded
	result.Failed = pipelineResult.Failed
	result.Processed = len(task.Items)
	result.Items = pipelineResult.Items
	result.EndedAt = time.Now().UTC()
	if err != nil {
		result.Status = "partial_failed"
		result.Error = err.Error()
	}

	return w.results.Produce(ctx, result)
}
