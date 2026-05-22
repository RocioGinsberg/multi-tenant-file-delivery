package tracing

import (
	"context"
	"testing"

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

func TestExtractTraceparentIgnoresEmptyHeader(t *testing.T) {
	ctx := ExtractTraceparent(context.Background(), "")
	if trace.SpanContextFromContext(ctx).IsValid() {
		t.Fatal("expected empty traceparent to leave context invalid")
	}
}
