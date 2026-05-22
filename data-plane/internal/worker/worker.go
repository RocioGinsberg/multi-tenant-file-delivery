package worker

import (
	"context"
	"time"

	"smh_auto_upload/data-plane/internal/message"
	dmetrics "smh_auto_upload/data-plane/internal/metrics"
	"smh_auto_upload/data-plane/internal/pipeline"
	"smh_auto_upload/data-plane/internal/sink"
	"smh_auto_upload/data-plane/internal/source"
	"smh_auto_upload/data-plane/internal/transport"
)

type Config struct {
	InboxDir           string
	ResultsDir         string
	SinkName           string
	TransportName      string
	Once               bool
	MaxItemConcurrency int
	UploadLimiter      UploadLimiter
	LimiterKey         string
	Metrics            dmetrics.Recorder
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
	recorder := dmetrics.OrNoop(w.cfg.Metrics)
	transportName := w.transportName()
	for {
		consumeStart := time.Now()
		tasks, err := w.tasks.Consume(ctx)
		consumeStatus := dmetrics.StatusFromError(err)
		if err == nil && len(tasks) == 0 {
			consumeStatus = dmetrics.StatusEmpty
		}
		recorder.ObserveTaskConsume(transportName, consumeStatus, time.Since(consumeStart))
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
	recorder := dmetrics.OrNoop(w.cfg.Metrics)
	started := time.Now().UTC()
	result := message.DeliveryResult{
		Topic:     "delivery.results.v1",
		TaskID:    task.TaskID,
		Status:    "uploaded",
		StartedAt: started,
	}

	options := pipeline.Options{
		MaxItemConcurrency: w.cfg.MaxItemConcurrency,
		Metrics:            recorder,
	}
	if w.cfg.UploadLimiter != nil {
		limiterKey := w.cfg.LimiterKey
		if limiterKey == "" {
			limiterKey = "global"
		}
		options.BeforeUpload = func(ctx context.Context, _ message.DeliveryTask, _ message.DeliveryItem) error {
			limiterStart := time.Now()
			err := w.cfg.UploadLimiter.Allow(ctx, limiterKey)
			recorder.ObserveLimiterAcquire(dmetrics.StatusFromError(err), time.Since(limiterStart))
			return err
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

	publishStart := time.Now()
	publishErr := w.results.Produce(ctx, result)
	recorder.ObserveResultPublish(
		w.transportName(),
		dmetrics.StatusFromError(publishErr),
		time.Since(publishStart),
	)
	return publishErr
}

func (w *Worker) transportName() string {
	if w.cfg.TransportName != "" {
		return w.cfg.TransportName
	}
	if w.cfg.InboxDir != "" || w.cfg.ResultsDir != "" {
		return "file"
	}
	return "unknown"
}
