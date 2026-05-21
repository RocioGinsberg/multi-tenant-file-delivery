package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"time"

	"smh_auto_upload/data-plane/internal/limiter"
	"smh_auto_upload/data-plane/internal/sink"
	"smh_auto_upload/data-plane/internal/source"
	"smh_auto_upload/data-plane/internal/transport"
	"smh_auto_upload/data-plane/internal/worker"
)

func main() {
	if err := run(context.Background(), os.Args[1:], os.Stderr); err != nil {
		fmt.Fprintf(os.Stderr, "worker failed: %v\n", err)
		os.Exit(1)
	}
}

func run(ctx context.Context, args []string, stderr io.Writer) error {
	flags := flag.NewFlagSet("worker", flag.ContinueOnError)
	flags.SetOutput(stderr)
	inbox := flags.String("inbox", "/tmp/auto_upload_outbox/delivery.tasks.v1", "directory containing delivery task JSON")
	results := flags.String("results", "/tmp/auto_upload_outbox/delivery.results.v1", "directory for result JSON")
	redisURL := flags.String("redis-url", "redis://localhost:6379/0", "Redis URL for limiter support")
	redisLimiterEnabled := flags.Bool("redis-limiter-enabled", false, "enable Redis fixed-window upload limiter")
	redisLimiterLimit := flags.Int("redis-limiter-limit", 100, "maximum upload attempts allowed per limiter window")
	redisLimiterWindow := flags.Duration("redis-limiter-window", time.Second, "Redis limiter fixed-window duration")
	redisLimiterKey := flags.String("redis-limiter-key", "global", "Redis limiter key dimension")
	transportName := flags.String("transport", "file", "task/result transport: file, kafka")
	kafkaBrokers := flags.String("kafka-brokers", "localhost:9092", "comma-separated Kafka broker addresses")
	kafkaTaskTopic := flags.String("kafka-task-topic", "delivery.tasks.v1", "Kafka topic for delivery tasks")
	kafkaResultTopic := flags.String("kafka-result-topic", "delivery.results.v1", "Kafka topic for delivery results")
	kafkaDLQTopic := flags.String("kafka-dlq-topic", "delivery.tasks.dlq.v1", "Kafka topic for unrecoverable task messages")
	kafkaGroupID := flags.String("kafka-group-id", "data-plane-worker", "Kafka consumer group ID")
	kafkaBatchSize := flags.Int("kafka-batch-size", 1, "number of Kafka task messages to process per run")
	startupCheck := flags.Bool("startup-check", true, "check external Kafka/S3 dependencies before processing tasks")
	startupCheckTimeout := flags.Duration("startup-check-timeout", 3*time.Second, "timeout for startup dependency checks")
	metricsEnabled := flags.Bool("metrics-enabled", false, "enable Prometheus metrics endpoint (placeholder; no-op in Phase 5.1)")
	metricsListenAddr := flags.String("metrics-listen-addr", ":8081", "listen address for Prometheus metrics endpoint")
	tracingEnabled := flags.Bool("tracing-enabled", false, "enable OpenTelemetry tracing (placeholder; no-op in Phase 5.1)")
	tracingServiceName := flags.String("tracing-service-name", "data-plane-worker", "OpenTelemetry service name")
	tracingOTLPEndpoint := flags.String("tracing-otlp-endpoint", "http://localhost:4318", "OpenTelemetry OTLP endpoint")
	sinkName := flags.String("sink", "mock", "sink implementation: mock, s3")
	s3Endpoint := flags.String("s3-endpoint", "http://localhost:9000", "S3-compatible endpoint for s3 sink")
	s3Region := flags.String("s3-region", "us-east-1", "S3 region for s3 sink")
	s3Bucket := flags.String("s3-bucket", "auto-upload-dev", "S3 bucket for s3 sink")
	stagingBucket := flags.String("staging-bucket", "auto-upload-staging", "staging bucket checked when source-mode=object")
	s3AccessKey := flags.String("s3-access-key-id", "minioadmin", "S3 access key ID for s3 sink")
	s3SecretKey := flags.String("s3-secret-access-key", "minioadmin", "S3 secret access key for s3 sink")
	s3PathStyle := flags.Bool("s3-path-style", true, "use path-style addressing for S3-compatible sinks")
	sourceMode := flags.String("source-mode", "file", "source resolver mode: file, object")
	itemConcurrency := flags.Int("item-concurrency", 1, "maximum number of task items to upload concurrently")
	once := flags.Bool("once", true, "process one batch and exit; set false for a long-running worker loop")
	if err := flags.Parse(args); err != nil {
		return err
	}
	parsedRedisURL, err := url.Parse(*redisURL)
	if err != nil {
		return fmt.Errorf("invalid redis url %q: %w", *redisURL, err)
	}
	if parsedRedisURL.Scheme == "" || parsedRedisURL.Host == "" {
		return fmt.Errorf("invalid redis url %q: missing scheme or host", *redisURL)
	}
	if *itemConcurrency <= 0 {
		return fmt.Errorf("item concurrency must be positive")
	}
	if *startupCheckTimeout <= 0 {
		return fmt.Errorf("startup check timeout must be positive")
	}
	if *redisLimiterLimit <= 0 {
		return fmt.Errorf("redis limiter limit must be positive")
	}
	if *redisLimiterWindow <= 0 {
		return fmt.Errorf("redis limiter window must be positive")
	}
	if *metricsEnabled {
		if _, _, err := net.SplitHostPort(*metricsListenAddr); err != nil {
			return fmt.Errorf("invalid metrics listen addr %q: %w", *metricsListenAddr, err)
		}
	}
	if *tracingEnabled {
		if *tracingServiceName == "" {
			return fmt.Errorf("tracing service name must not be empty")
		}
		parsedOTLPEndpoint, err := url.Parse(*tracingOTLPEndpoint)
		if err != nil {
			return fmt.Errorf("invalid tracing otlp endpoint %q: %w", *tracingOTLPEndpoint, err)
		}
		if parsedOTLPEndpoint.Scheme == "" || parsedOTLPEndpoint.Host == "" {
			return fmt.Errorf("invalid tracing otlp endpoint %q: missing scheme or host", *tracingOTLPEndpoint)
		}
	}
	log.Printf(
		"redis-limiter-enabled=%t redis-url=%s redis-limiter-key=%s redis-limiter-limit=%d redis-limiter-window=%s",
		*redisLimiterEnabled,
		parsedRedisURL.Redacted(),
		*redisLimiterKey,
		*redisLimiterLimit,
		*redisLimiterWindow,
	)
	log.Printf(
		"observability metrics-enabled=%t metrics-listen-addr=%s tracing-enabled=%t tracing-service-name=%s tracing-otlp-endpoint=%s",
		*metricsEnabled,
		*metricsListenAddr,
		*tracingEnabled,
		*tracingServiceName,
		redactURL(*tracingOTLPEndpoint),
	)
	checkCtx := ctx
	var cancelCheck context.CancelFunc
	if *startupCheck {
		checkCtx, cancelCheck = context.WithTimeout(ctx, *startupCheckTimeout)
		defer cancelCheck()
	}

	var sinkImpl sink.Sink = sink.NewMockSink()
	switch *sinkName {
	case "mock":
	case "s3":
		s3Sink, err := sink.NewS3Sink(ctx, sink.S3Config{
			Endpoint:        *s3Endpoint,
			Region:          *s3Region,
			Bucket:          *s3Bucket,
			AccessKeyID:     *s3AccessKey,
			SecretAccessKey: *s3SecretKey,
			UsePathStyle:    *s3PathStyle,
		})
		if err != nil {
			return fmt.Errorf("create s3 sink: %w", err)
		}
		if *startupCheck {
			if err := s3Sink.Check(checkCtx); err != nil {
				return err
			}
			log.Printf("startup check passed: s3 sink bucket=%s", *s3Bucket)
		}
		sinkImpl = s3Sink
	default:
		return fmt.Errorf("unsupported sink %q", *sinkName)
	}

	wd, _ := os.Getwd()
	log.Printf("worker starting from %s", filepath.Clean(wd))
	log.Printf("transport=%s inbox=%s results=%s sink=%s", *transportName, *inbox, *results, *sinkName)

	cfg := worker.Config{
		InboxDir:           *inbox,
		ResultsDir:         *results,
		SinkName:           *sinkName,
		Once:               *once,
		MaxItemConcurrency: *itemConcurrency,
	}
	if *redisLimiterEnabled {
		uploadLimiter, err := limiter.NewRedisLimiter(limiter.RedisConfig{
			URL:    *redisURL,
			Limit:  *redisLimiterLimit,
			Window: *redisLimiterWindow,
		})
		if err != nil {
			return fmt.Errorf("create redis limiter: %w", err)
		}
		defer func() {
			if err := uploadLimiter.Close(); err != nil {
				log.Printf("close redis limiter: %v", err)
			}
		}()
		cfg.UploadLimiter = uploadLimiter
		cfg.LimiterKey = *redisLimiterKey
	}

	var sourceResolver source.Resolver = source.NewFileResolver()
	switch *sourceMode {
	case "file":
	case "object":
		fetcher, err := source.NewS3ObjectFetcher(ctx, source.S3Config{
			Endpoint:        *s3Endpoint,
			Region:          *s3Region,
			AccessKeyID:     *s3AccessKey,
			SecretAccessKey: *s3SecretKey,
			UsePathStyle:    *s3PathStyle,
		})
		if err != nil {
			return fmt.Errorf("create source fetcher: %w", err)
		}
		if *startupCheck {
			if err := fetcher.CheckBucket(checkCtx, *stagingBucket); err != nil {
				return err
			}
			log.Printf("startup check passed: source bucket=%s", *stagingBucket)
		}
		sourceResolver = source.NewZipArchiveResolver(fetcher)
	default:
		return fmt.Errorf("unsupported source mode %q", *sourceMode)
	}

	var taskConsumer transport.TaskConsumer
	var resultProducer transport.ResultProducer
	switch *transportName {
	case "file":
		if err := os.MkdirAll(*inbox, 0o755); err != nil {
			return fmt.Errorf("create inbox: %w", err)
		}
		if err := os.MkdirAll(*results, 0o755); err != nil {
			return fmt.Errorf("create results: %w", err)
		}
		spool := transport.NewFileSpool(*inbox, *results)
		taskConsumer = spool
		resultProducer = spool
	case "kafka":
		kafkaTransport, err := transport.NewKafkaTransport(transport.KafkaConfig{
			Brokers:     transport.ParseBrokerList(*kafkaBrokers),
			TaskTopic:   *kafkaTaskTopic,
			ResultTopic: *kafkaResultTopic,
			DLQTopic:    *kafkaDLQTopic,
			GroupID:     *kafkaGroupID,
			BatchSize:   *kafkaBatchSize,
		})
		if err != nil {
			return fmt.Errorf("create kafka transport: %w", err)
		}
		if *startupCheck {
			if err := transport.CheckKafkaConnectivity(checkCtx, transport.ParseBrokerList(*kafkaBrokers)); err != nil {
				return err
			}
			log.Printf("startup check passed: kafka brokers=%s", *kafkaBrokers)
		}
		defer kafkaTransport.Close()
		taskConsumer = kafkaTransport
		resultProducer = kafkaTransport
	default:
		return fmt.Errorf("unsupported transport %q", *transportName)
	}

	w := worker.NewWithTransportAndResolver(cfg, sinkImpl, sourceResolver, taskConsumer, resultProducer)

	return w.Run(ctx)
}

func redactURL(raw string) string {
	parsed, err := url.Parse(raw)
	if err != nil {
		return raw
	}
	return parsed.Redacted()
}
