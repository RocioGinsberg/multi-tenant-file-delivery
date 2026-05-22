package metrics

import (
	"context"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

func TestPrometheusRecorderExposesMetricsEndpoint(t *testing.T) {
	recorder := NewPrometheusRecorder()
	recorder.ObserveTaskConsume("file", StatusSuccess, 5*time.Millisecond)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	server, err := StartServer(ctx, "127.0.0.1:0", recorder)
	if err != nil {
		t.Fatalf("start metrics server: %v", err)
	}
	defer server.Shutdown(context.Background())

	resp, err := http.Get("http://" + server.Addr() + "/metrics")
	if err != nil {
		t.Fatalf("get metrics: %v", err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatalf("read metrics: %v", err)
	}
	text := string(body)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("unexpected status %d: %s", resp.StatusCode, text)
	}
	if !strings.Contains(text, "data_plane_task_consume_total") {
		t.Fatalf("expected task consume metric, got: %s", text)
	}
	if !strings.Contains(text, `transport="file"`) || !strings.Contains(text, `status="success"`) {
		t.Fatalf("expected low-cardinality labels, got: %s", text)
	}
}
