package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"time"

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
	transportName := flags.String("transport", "file", "task/result transport: file, kafka")
	kafkaBrokers := flags.String("kafka-brokers", "localhost:9092", "comma-separated Kafka broker addresses")
	kafkaTaskTopic := flags.String("kafka-task-topic", "delivery.tasks.v1", "Kafka topic for delivery tasks")
	kafkaResultTopic := flags.String("kafka-result-topic", "delivery.results.v1", "Kafka topic for delivery results")
	kafkaDLQTopic := flags.String("kafka-dlq-topic", "delivery.tasks.dlq.v1", "Kafka topic for unrecoverable task messages")
	kafkaGroupID := flags.String("kafka-group-id", "data-plane-worker", "Kafka consumer group ID")
	kafkaBatchSize := flags.Int("kafka-batch-size", 1, "number of Kafka task messages to process per run")
	startupCheck := flags.Bool("startup-check", true, "check external Kafka/S3 dependencies before processing tasks")
	startupCheckTimeout := flags.Duration("startup-check-timeout", 3*time.Second, "timeout for startup dependency checks")
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
	once := flags.Bool("once", true, "process current inbox contents once and exit")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if *itemConcurrency <= 0 {
		return fmt.Errorf("item concurrency must be positive")
	}
	if *startupCheckTimeout <= 0 {
		return fmt.Errorf("startup check timeout must be positive")
	}
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
