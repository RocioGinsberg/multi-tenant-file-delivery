package worker

import (
	"context"
	"time"

	"smh_auto_upload/data-plane/internal/message"
	dmetrics "smh_auto_upload/data-plane/internal/metrics"
	"smh_auto_upload/data-plane/internal/pipeline"
	"smh_auto_upload/data-plane/internal/sink"
	"smh_auto_upload/data-plane/internal/source"
	dtracing "smh_auto_upload/data-plane/internal/tracing"
	"smh_auto_upload/data-plane/internal/transport"

	"go.opentelemetry.io/otel/trace"
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
		consumeCtx, consumeSpan := dtracing.Tracer().Start(
			ctx,
			"data_plane.task.consume",
			trace.WithAttributes(
				dtracing.String("messaging.system", transportName),
				dtracing.String("delivery.transport", transportName),
			),
		)
		consumeStart := time.Now()
		tasks, err := w.tasks.Consume(consumeCtx)
		consumeStatus := dmetrics.StatusFromError(err)
		if err == nil && len(tasks) == 0 {
			consumeStatus = dmetrics.StatusEmpty
		}
		recorder.ObserveTaskConsume(transportName, consumeStatus, time.Since(consumeStart))
		if err == nil {
			consumeSpan.SetAttributes(dtracing.Int("delivery.task.count", len(tasks)))
		}
		dtracing.EndWithError(consumeSpan, err)
		if err != nil {
			return err
		}

		for _, taskMessage := range tasks {
			taskCtx := dtracing.ExtractTraceparent(ctx, taskMessage.Task.Traceparent)
			taskCtx, taskSpan := dtracing.Tracer().Start(
				taskCtx,
				"data_plane.task.process",
				trace.WithAttributes(
					dtracing.String("delivery.task_id", taskMessage.Task.TaskID),
					dtracing.Int("delivery.item_count", len(taskMessage.Task.Items)),
					dtracing.Bool("delivery.traceparent.present", taskMessage.Task.Traceparent != ""),
				),
			)
			if err := w.processTask(taskCtx, taskMessage.Task); err != nil {
				dtracing.EndWithError(taskSpan, err)
				return err
			}
			if err := taskMessage.Ack(taskCtx); err != nil {
				dtracing.EndWithError(taskSpan, err)
				return err
			}
			taskSpan.End()
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
			limiterCtx, limiterSpan := dtracing.Tracer().Start(
				ctx,
				"data_plane.limiter.acquire",
				trace.WithAttributes(dtracing.String("delivery.limiter.key", limiterKey)),
			)
			limiterStart := time.Now()
			err := w.cfg.UploadLimiter.Allow(limiterCtx, limiterKey)
			recorder.ObserveLimiterAcquire(dmetrics.StatusFromError(err), time.Since(limiterStart))
			dtracing.EndWithError(limiterSpan, err)
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
		dtracing.SetPartialFailure(trace.SpanFromContext(ctx), err)
	}

	publishCtx, publishSpan := dtracing.Tracer().Start(
		ctx,
		"data_plane.result.publish",
		trace.WithAttributes(
			dtracing.String("delivery.transport", w.transportName()),
			dtracing.String("delivery.task_id", result.TaskID),
			dtracing.String("delivery.result.status", result.Status),
			dtracing.Int("delivery.result.uploaded", result.Uploaded),
			dtracing.Int("delivery.result.failed", result.Failed),
		),
	)
	publishStart := time.Now()
	publishErr := w.results.Produce(publishCtx, result)
	recorder.ObserveResultPublish(
		w.transportName(),
		dmetrics.StatusFromError(publishErr),
		time.Since(publishStart),
	)
	dtracing.EndWithError(publishSpan, publishErr)
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
