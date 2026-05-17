package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"

	"smh_auto_upload/data-plane/internal/sink"
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
	sinkName := flags.String("sink", "mock", "sink implementation: mock, s3")
	s3Endpoint := flags.String("s3-endpoint", "http://localhost:9000", "S3-compatible endpoint for s3 sink")
	s3Region := flags.String("s3-region", "us-east-1", "S3 region for s3 sink")
	s3Bucket := flags.String("s3-bucket", "auto-upload-dev", "S3 bucket for s3 sink")
	s3AccessKey := flags.String("s3-access-key-id", "minioadmin", "S3 access key ID for s3 sink")
	s3SecretKey := flags.String("s3-secret-access-key", "minioadmin", "S3 secret access key for s3 sink")
	s3PathStyle := flags.Bool("s3-path-style", true, "use path-style addressing for S3-compatible sinks")
	once := flags.Bool("once", true, "process current inbox contents once and exit")
	if err := flags.Parse(args); err != nil {
		return err
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
		sinkImpl = s3Sink
	default:
		return fmt.Errorf("unsupported sink %q", *sinkName)
	}

	if err := os.MkdirAll(*inbox, 0o755); err != nil {
		return fmt.Errorf("create inbox: %w", err)
	}
	if err := os.MkdirAll(*results, 0o755); err != nil {
		return fmt.Errorf("create results: %w", err)
	}

	wd, _ := os.Getwd()
	log.Printf("worker starting from %s", filepath.Clean(wd))
	log.Printf("inbox=%s results=%s sink=%s", *inbox, *results, *sinkName)

	w := worker.New(worker.Config{
		InboxDir:   *inbox,
		ResultsDir: *results,
		SinkName:   *sinkName,
		Once:       *once,
	}, sinkImpl)

	return w.Run(ctx)
}
