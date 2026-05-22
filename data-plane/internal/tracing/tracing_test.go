package tracing

import (
	"context"
	"testing"

	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/trace/tracetest"
	"go.opentelemetry.io/otel/trace"
)

const traceparent = "00-1234567890abcdef1234567890abcdef-1234567890abcdef-01"

func TestExtractTraceparentCreatesRemoteParent(t *testing.T) {
	ctx := ExtractTraceparent(context.Background(), traceparent)
	spanContext := trace.SpanContextFromContext(ctx)

	if !spanContext.IsValid() {
		t.Fatal("expected valid span context")
	}
	if !spanContext.IsRemote() {
		t.Fatal("expected remote parent")
	}
	if got := spanContext.TraceID().String(); got != "1234567890abcdef1234567890abcdef" {
		t.Fatalf("unexpected trace id: %s", got)
	}
	if got := spanContext.SpanID().String(); got != "1234567890abcdef" {
		t.Fatalf("unexpected span id: %s", got)
	}
}

func TestExtractTraceparentBecomesSpanParent(t *testing.T) {
	recorder := tracetest.NewSpanRecorder()
	provider := sdktrace.NewTracerProvider(sdktrace.WithSpanProcessor(recorder))
	tracer := provider.Tracer("test")

	ctx := ExtractTraceparent(context.Background(), traceparent)
	_, span := tracer.Start(ctx, "child")
	span.End()

	spans := recorder.Ended()
	if len(spans) != 1 {
		t.Fatalf("expected 1 span, got %d", len(spans))
	}
	parent := spans[0].Parent()
	if !parent.IsRemote() {
		t.Fatal("expected remote parent")
	}
	if got := parent.TraceID().String(); got != "1234567890abcdef1234567890abcdef" {
		t.Fatalf("unexpected parent trace id: %s", got)
	}
	if got := spans[0].SpanContext().TraceID().String(); got != "1234567890abcdef1234567890abcdef" {
		t.Fatalf("unexpected child trace id: %s", got)
	}
}

func TestExtractTraceparentIgnoresEmptyHeader(t *testing.T) {
	ctx := ExtractTraceparent(context.Background(), "")
	if trace.SpanContextFromContext(ctx).IsValid() {
		t.Fatal("expected empty traceparent to leave context invalid")
	}
}
