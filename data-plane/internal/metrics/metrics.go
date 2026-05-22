package metrics

import (
	"context"
	"errors"
	"io"
	"log"
	"net"
	"net/http"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

const (
	StatusSuccess = "success"
	StatusError   = "error"
	StatusEmpty   = "empty"
)

type Recorder interface {
	ObserveTaskConsume(transport, status string, duration time.Duration)
	ObserveSourceRead(source, status string, duration time.Duration)
	ObserveSinkUpload(sink, status string, duration time.Duration)
	ObserveResultPublish(transport, status string, duration time.Duration)
	ObserveLimiterAcquire(status string, duration time.Duration)
}

type NoopRecorder struct{}

func (NoopRecorder) ObserveTaskConsume(string, string, time.Duration)   {}
func (NoopRecorder) ObserveSourceRead(string, string, time.Duration)    {}
func (NoopRecorder) ObserveSinkUpload(string, string, time.Duration)    {}
func (NoopRecorder) ObserveResultPublish(string, string, time.Duration) {}
func (NoopRecorder) ObserveLimiterAcquire(string, time.Duration)        {}

func OrNoop(recorder Recorder) Recorder {
	if recorder == nil {
		return NoopRecorder{}
	}
	return recorder
}

type PrometheusRecorder struct {
	registry *prometheus.Registry

	taskConsumeTotal       *prometheus.CounterVec
	taskConsumeDuration    *prometheus.HistogramVec
	sourceReadTotal        *prometheus.CounterVec
	sourceReadDuration     *prometheus.HistogramVec
	sinkUploadTotal        *prometheus.CounterVec
	sinkUploadDuration     *prometheus.HistogramVec
	resultPublishTotal     *prometheus.CounterVec
	resultPublishDuration  *prometheus.HistogramVec
	limiterAcquireTotal    *prometheus.CounterVec
	limiterAcquireDuration *prometheus.HistogramVec
}

func NewPrometheusRecorder() *PrometheusRecorder {
	registry := prometheus.NewRegistry()
	recorder := &PrometheusRecorder{
		registry: registry,
		taskConsumeTotal: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "data_plane_task_consume_total",
			Help: "Total data-plane task consume operations.",
		}, []string{"transport", "status"}),
		taskConsumeDuration: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name:    "data_plane_task_consume_duration_seconds",
			Help:    "Data-plane task consume operation duration in seconds.",
			Buckets: prometheus.DefBuckets,
		}, []string{"transport", "status"}),
		sourceReadTotal: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "data_plane_source_read_total",
			Help: "Total data-plane source read operations.",
		}, []string{"source", "status"}),
		sourceReadDuration: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name:    "data_plane_source_read_duration_seconds",
			Help:    "Data-plane source read duration in seconds.",
			Buckets: prometheus.DefBuckets,
		}, []string{"source", "status"}),
		sinkUploadTotal: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "data_plane_sink_upload_total",
			Help: "Total data-plane sink upload operations.",
		}, []string{"sink", "status"}),
		sinkUploadDuration: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name:    "data_plane_sink_upload_duration_seconds",
			Help:    "Data-plane sink upload duration in seconds.",
			Buckets: prometheus.DefBuckets,
		}, []string{"sink", "status"}),
		resultPublishTotal: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "data_plane_result_publish_total",
			Help: "Total data-plane result publish operations.",
		}, []string{"transport", "status"}),
		resultPublishDuration: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name:    "data_plane_result_publish_duration_seconds",
			Help:    "Data-plane result publish duration in seconds.",
			Buckets: prometheus.DefBuckets,
		}, []string{"transport", "status"}),
		limiterAcquireTotal: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "data_plane_limiter_acquire_total",
			Help: "Total data-plane limiter acquire operations.",
		}, []string{"status"}),
		limiterAcquireDuration: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name:    "data_plane_limiter_acquire_duration_seconds",
			Help:    "Data-plane limiter acquire duration in seconds.",
			Buckets: prometheus.DefBuckets,
		}, []string{"status"}),
	}
	registry.MustRegister(
		recorder.taskConsumeTotal,
		recorder.taskConsumeDuration,
		recorder.sourceReadTotal,
		recorder.sourceReadDuration,
		recorder.sinkUploadTotal,
		recorder.sinkUploadDuration,
		recorder.resultPublishTotal,
		recorder.resultPublishDuration,
		recorder.limiterAcquireTotal,
		recorder.limiterAcquireDuration,
	)
	return recorder
}

func (r *PrometheusRecorder) Handler() http.Handler {
	return promhttp.HandlerFor(r.registry, promhttp.HandlerOpts{})
}

func (r *PrometheusRecorder) ObserveTaskConsume(transport, status string, duration time.Duration) {
	r.taskConsumeTotal.WithLabelValues(transport, status).Inc()
	r.taskConsumeDuration.WithLabelValues(transport, status).Observe(duration.Seconds())
}

func (r *PrometheusRecorder) ObserveSourceRead(source, status string, duration time.Duration) {
	r.sourceReadTotal.WithLabelValues(source, status).Inc()
	r.sourceReadDuration.WithLabelValues(source, status).Observe(duration.Seconds())
}

func (r *PrometheusRecorder) ObserveSinkUpload(sink, status string, duration time.Duration) {
	r.sinkUploadTotal.WithLabelValues(sink, status).Inc()
	r.sinkUploadDuration.WithLabelValues(sink, status).Observe(duration.Seconds())
}

func (r *PrometheusRecorder) ObserveResultPublish(transport, status string, duration time.Duration) {
	r.resultPublishTotal.WithLabelValues(transport, status).Inc()
	r.resultPublishDuration.WithLabelValues(transport, status).Observe(duration.Seconds())
}

func (r *PrometheusRecorder) ObserveLimiterAcquire(status string, duration time.Duration) {
	r.limiterAcquireTotal.WithLabelValues(status).Inc()
	r.limiterAcquireDuration.WithLabelValues(status).Observe(duration.Seconds())
}

type Source interface {
	Open() (io.ReadCloser, error)
	Size() (int64, error)
	Path() string
}

func WrapSource(sourceName string, src Source, recorder Recorder) Source {
	return instrumentedSource{
		sourceName: sourceName,
		inner:      src,
		recorder:   OrNoop(recorder),
	}
}

type instrumentedSource struct {
	sourceName string
	inner      Source
	recorder   Recorder
}

func (s instrumentedSource) Open() (io.ReadCloser, error) {
	start := time.Now()
	reader, err := s.inner.Open()
	if err != nil {
		s.recorder.ObserveSourceRead(s.sourceName, StatusError, time.Since(start))
		return nil, err
	}
	return &instrumentedReadCloser{
		ReadCloser: reader,
		sourceName: s.sourceName,
		start:      start,
		recorder:   s.recorder,
	}, nil
}

func (s instrumentedSource) Size() (int64, error) {
	return s.inner.Size()
}

func (s instrumentedSource) Path() string {
	return s.inner.Path()
}

type instrumentedReadCloser struct {
	io.ReadCloser
	sourceName string
	start      time.Time
	recorder   Recorder
	readErr    bool
	closed     bool
}

func (r *instrumentedReadCloser) Read(p []byte) (int, error) {
	n, err := r.ReadCloser.Read(p)
	if err != nil && !errors.Is(err, io.EOF) {
		r.readErr = true
	}
	return n, err
}

func (r *instrumentedReadCloser) Close() error {
	err := r.ReadCloser.Close()
	if r.closed {
		return err
	}
	r.closed = true
	status := StatusSuccess
	if err != nil || r.readErr {
		status = StatusError
	}
	r.recorder.ObserveSourceRead(r.sourceName, status, time.Since(r.start))
	return err
}

type Server struct {
	addr   string
	server *http.Server
}

func StartServer(ctx context.Context, addr string, recorder *PrometheusRecorder) (*Server, error) {
	listener, err := net.Listen("tcp", addr)
	if err != nil {
		return nil, err
	}

	mux := http.NewServeMux()
	mux.Handle("/metrics", recorder.Handler())
	server := &http.Server{Handler: mux}
	metricsServer := &Server{
		addr:   listener.Addr().String(),
		server: server,
	}

	go func() {
		if err := server.Serve(listener); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Printf("metrics server stopped: %v", err)
		}
	}()
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		_ = metricsServer.Shutdown(shutdownCtx)
	}()

	return metricsServer, nil
}

func (s *Server) Addr() string {
	return s.addr
}

func (s *Server) Shutdown(ctx context.Context) error {
	return s.server.Shutdown(ctx)
}

func StatusFromError(err error) string {
	if err != nil {
		return StatusError
	}
	return StatusSuccess
}
