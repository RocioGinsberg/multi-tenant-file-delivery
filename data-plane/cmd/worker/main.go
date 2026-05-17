package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"

	"smh_auto_upload/data-plane/internal/sink"
	"smh_auto_upload/data-plane/internal/worker"
)

func main() {
	inbox := flag.String("inbox", "/tmp/auto_upload_outbox/delivery.tasks.v1", "directory containing delivery task JSON")
	results := flag.String("results", "/tmp/auto_upload_outbox/delivery.results.v1", "directory for result JSON")
	sinkName := flag.String("sink", "mock", "sink implementation: mock")
	once := flag.Bool("once", true, "process current inbox contents once and exit")
	flag.Parse()

	var sinkImpl sink.Sink = sink.NewMockSink()
	switch *sinkName {
	case "mock":
	default:
		log.Fatalf("unsupported sink %q", *sinkName)
	}

	if err := os.MkdirAll(*inbox, 0o755); err != nil {
		log.Fatalf("create inbox: %v", err)
	}
	if err := os.MkdirAll(*results, 0o755); err != nil {
		log.Fatalf("create results: %v", err)
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

	if err := w.Run(context.Background()); err != nil {
		fmt.Fprintf(os.Stderr, "worker failed: %v\n", err)
		os.Exit(1)
	}
}
