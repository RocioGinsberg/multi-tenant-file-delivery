package transport

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	kafka "github.com/segmentio/kafka-go"

	"smh_auto_upload/data-plane/internal/message"
)

type fakeKafkaReader struct {
	fetched   []kafka.Message
	committed []kafka.Message
}

func (r *fakeKafkaReader) FetchMessage(context.Context) (kafka.Message, error) {
	msg := r.fetched[0]
	r.fetched = r.fetched[1:]
	return msg, nil
}

func (r *fakeKafkaReader) CommitMessages(_ context.Context, messages ...kafka.Message) error {
	r.committed = append(r.committed, messages...)
	return nil
}

func (r *fakeKafkaReader) Close() error { return nil }

type fakeKafkaWriter struct {
	written []kafka.Message
}

func (w *fakeKafkaWriter) WriteMessages(_ context.Context, messages ...kafka.Message) error {
	w.written = append(w.written, messages...)
	return nil
}

func (w *fakeKafkaWriter) Close() error { return nil }

func TestKafkaTransportConsumesTaskAndAcksOffset(t *testing.T) {
	task := message.DeliveryTask{
		SchemaVersion:  1,
		Topic:          "delivery.tasks.v1",
		TaskID:         "task-1",
		IdempotencyKey: "idem-1",
		TempDir:        "/tmp/task-1",
		BucketName:     "auto-upload-dev",
		CreatedAt:      time.Date(2026, 5, 17, 10, 0, 0, 0, time.UTC),
	}
	raw, err := json.Marshal(task)
	if err != nil {
		t.Fatal(err)
	}
	reader := &fakeKafkaReader{fetched: []kafka.Message{{
		Topic: "delivery.tasks.v1",
		Key:   []byte("task-1"),
		Value: raw,
	}}}
	writer := &fakeKafkaWriter{}
	transport := newKafkaTransport(reader, writer, 1)

	tasks, err := transport.Consume(context.Background())
	if err != nil {
		t.Fatalf("consume task: %v", err)
	}
	if len(tasks) != 1 || tasks[0].Task.TaskID != "task-1" {
		t.Fatalf("unexpected tasks: %+v", tasks)
	}
	if len(reader.committed) != 0 {
		t.Fatalf("message should not be committed before ack: %+v", reader.committed)
	}
	if err := tasks[0].Ack(context.Background()); err != nil {
		t.Fatalf("ack task: %v", err)
	}
	if len(reader.committed) != 1 || string(reader.committed[0].Key) != "task-1" {
		t.Fatalf("unexpected commits: %+v", reader.committed)
	}
}

func TestKafkaTransportProducesResult(t *testing.T) {
	reader := &fakeKafkaReader{}
	writer := &fakeKafkaWriter{}
	transport := newKafkaTransport(reader, writer, 1)

	err := transport.Produce(context.Background(), message.DeliveryResult{
		Topic:     "delivery.results.v1",
		TaskID:    "task-1",
		Status:    "uploaded",
		Uploaded:  1,
		StartedAt: time.Date(2026, 5, 17, 10, 0, 0, 0, time.UTC),
		EndedAt:   time.Date(2026, 5, 17, 10, 1, 0, 0, time.UTC),
	})
	if err != nil {
		t.Fatalf("produce result: %v", err)
	}
	if len(writer.written) != 1 || string(writer.written[0].Key) != "task-1" {
		t.Fatalf("unexpected writes: %+v", writer.written)
	}
	var decoded message.DeliveryResult
	if err := json.Unmarshal(writer.written[0].Value, &decoded); err != nil {
		t.Fatal(err)
	}
	if decoded.TaskID != "task-1" || decoded.Status != "uploaded" {
		t.Fatalf("unexpected result payload: %+v", decoded)
	}
}

func TestKafkaTransportSendsInvalidTaskToDLQAndCommits(t *testing.T) {
	reader := &fakeKafkaReader{fetched: []kafka.Message{{
		Topic: "delivery.tasks.v1",
		Key:   []byte("bad-task"),
		Value: []byte("{not-json"),
	}}}
	writer := &fakeKafkaWriter{}
	dlqWriter := &fakeKafkaWriter{}
	transport := newKafkaTransportWithDLQ(
		reader,
		writer,
		dlqWriter,
		1,
		"delivery.tasks.v1",
		"delivery.tasks.dlq.v1",
		"worker-test",
	)

	tasks, err := transport.Consume(context.Background())
	if err != nil {
		t.Fatalf("consume task: %v", err)
	}
	if len(tasks) != 0 {
		t.Fatalf("unexpected tasks: %+v", tasks)
	}
	if len(reader.committed) != 1 || string(reader.committed[0].Key) != "bad-task" {
		t.Fatalf("invalid message should be committed after DLQ write: %+v", reader.committed)
	}
	if len(dlqWriter.written) != 1 || string(dlqWriter.written[0].Key) != "bad-task" {
		t.Fatalf("unexpected dlq writes: %+v", dlqWriter.written)
	}

	var dlq DLQMessage
	if err := json.Unmarshal(dlqWriter.written[0].Value, &dlq); err != nil {
		t.Fatal(err)
	}
	if dlq.Topic != "delivery.tasks.dlq.v1" || dlq.ErrorClass != "invalid_message" || dlq.WorkerID != "worker-test" {
		t.Fatalf("unexpected dlq payload: %+v", dlq)
	}
	if dlq.TaskTopic != "delivery.tasks.v1" || dlq.TaskKey != "bad-task" || dlq.RawMessage != "{not-json" {
		t.Fatalf("unexpected dlq source fields: %+v", dlq)
	}
	if dlq.ErrorMessage == "" || dlq.FailedAt == "" {
		t.Fatalf("expected error metadata: %+v", dlq)
	}
}

func TestParseBrokerList(t *testing.T) {
	brokers := ParseBrokerList("localhost:9092, kafka:9092, ")
	if len(brokers) != 2 || brokers[0] != "localhost:9092" || brokers[1] != "kafka:9092" {
		t.Fatalf("unexpected brokers: %+v", brokers)
	}
}

func TestCheckKafkaConnectivityRequiresBroker(t *testing.T) {
	err := CheckKafkaConnectivity(context.Background(), nil)
	if err == nil {
		t.Fatal("expected error")
	}
}
