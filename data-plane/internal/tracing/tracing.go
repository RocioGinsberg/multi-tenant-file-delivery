package tracing

import (
	"context"
	"errors"
	"net/url"
	"time"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.26.0"
	"go.opentelemetry.io/otel/trace"
)

const instrumentationName = "smh_auto_upload/data-plane"

type Config struct {
	Enabled     bool
	ServiceName string
	Endpoint    string
}

type Provider struct {
	tracerProvider *sdktrace.TracerProvider
}

func Configure(ctx context.Context, cfg Config) (*Provider, error) {
	otel.SetTextMapPropagator(propagation.TraceContext{})
	if !cfg.Enabled {
		return &Provider{}, nil
	}
	if cfg.ServiceName == "" {
		cfg.ServiceName = "data-plane-worker"
	}

	parsedEndpoint, err := url.Parse(cfg.Endpoint)
	if err != nil {
		return nil, err
	}
	options := []otlptracehttp.Option{
		otlptracehttp.WithEndpointURL(cfg.Endpoint),
		otlptracehttp.WithTimeout(5 * time.Second),
	}
	if parsedEndpoint.Scheme == "http" {
		options = append(options, otlptracehttp.WithInsecure())
	}

	exporter, err := otlptracehttp.New(ctx, options...)
	if err != nil {
		return nil, err
	}
	tracerProvider := sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(exporter),
		sdktrace.WithSampler(sdktrace.ParentBased(sdktrace.AlwaysSample())),
		sdktrace.WithResource(resource.NewWithAttributes(
			semconv.SchemaURL,
			semconv.ServiceName(cfg.ServiceName),
		)),
	)
	otel.SetTracerProvider(tracerProvider)
	return &Provider{tracerProvider: tracerProvider}, nil
}

func (p *Provider) Shutdown(ctx context.Context) error {
	if p == nil || p.tracerProvider == nil {
		return nil
	}
	return p.tracerProvider.Shutdown(ctx)
}

func Tracer() trace.Tracer {
	return otel.Tracer(instrumentationName)
}

func ExtractTraceparent(ctx context.Context, traceparent string) context.Context {
	if traceparent == "" {
		return ctx
	}
	return propagation.TraceContext{}.Extract(
		ctx,
		propagation.MapCarrier{"traceparent": traceparent},
	)
}

func RecordError(span trace.Span, err error) {
	if err == nil || span == nil || !span.IsRecording() {
		return
	}
	span.RecordError(err)
	span.SetStatus(codes.Error, err.Error())
}

func EndWithError(span trace.Span, err error) {
	RecordError(span, err)
	if span != nil {
		span.End()
	}
}

func StatusFromError(err error) codes.Code {
	if err != nil {
		return codes.Error
	}
	return codes.Ok
}

func SetErrorStatus(span trace.Span, err error) {
	if span == nil || !span.IsRecording() {
		return
	}
	if err == nil {
		span.SetStatus(codes.Ok, "")
		return
	}
	RecordError(span, err)
}

func SetPartialFailure(span trace.Span, err error) {
	if span == nil || !span.IsRecording() || err == nil {
		return
	}
	span.RecordError(err)
	span.SetStatus(codes.Error, err.Error())
}

func String(name string, value string) attribute.KeyValue {
	return attribute.String(name, value)
}

func Int(name string, value int) attribute.KeyValue {
	return attribute.Int(name, value)
}

func Int64(name string, value int64) attribute.KeyValue {
	return attribute.Int64(name, value)
}

func Bool(name string, value bool) attribute.KeyValue {
	return attribute.Bool(name, value)
}

func IgnoreCanceled(err error) error {
	if errors.Is(err, context.Canceled) {
		return nil
	}
	return err
}
