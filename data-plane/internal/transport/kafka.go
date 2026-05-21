package transport

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"strings"
	"time"

	kafka "github.com/segmentio/kafka-go"

	"smh_auto_upload/data-plane/internal/message"
)

type KafkaConfig struct {
	Brokers     []string
	TaskTopic   string
	ResultTopic string
	DLQTopic    string
	GroupID     string
	BatchSize   int
	WorkerID    string
}

type kafkaReader interface {
	FetchMessage(context.Context) (kafka.Message, error)
	CommitMessages(context.Context, ...kafka.Message) error
	Close() error
}

type kafkaWriter interface {
	WriteMessages(context.Context, ...kafka.Message) error
	Close() error
}

type KafkaTransport struct {
	reader    kafkaReader
	writer    kafkaWriter
	dlqWriter kafkaWriter
	batchSize int
	batchWait time.Duration
	taskTopic string
	dlqTopic  string
	workerID  string
}

func NewKafkaTransport(cfg KafkaConfig) (*KafkaTransport, error) {
	if len(cfg.Brokers) == 0 {
		return nil, fmt.Errorf("kafka brokers are required")
	}
	if cfg.TaskTopic == "" {
		cfg.TaskTopic = "delivery.tasks.v1"
	}
	if cfg.ResultTopic == "" {
		cfg.ResultTopic = "delivery.results.v1"
	}
	if cfg.DLQTopic == "" {
		cfg.DLQTopic = "delivery.tasks.dlq.v1"
	}
	if cfg.GroupID == "" {
		cfg.GroupID = "data-plane-worker"
	}
	if cfg.BatchSize <= 0 {
		cfg.BatchSize = 1
	}
	if cfg.WorkerID == "" {
		cfg.WorkerID = cfg.GroupID
	}

	reader := kafka.NewReader(kafka.ReaderConfig{
		Brokers: cfg.Brokers,
		Topic:   cfg.TaskTopic,
		GroupID: cfg.GroupID,
	})
	writer := &kafka.Writer{
		Addr:     kafka.TCP(cfg.Brokers...),
		Topic:    cfg.ResultTopic,
		Balancer: &kafka.Hash{},
	}
	dlqWriter := &kafka.Writer{
		Addr:     kafka.TCP(cfg.Brokers...),
		Topic:    cfg.DLQTopic,
		Balancer: &kafka.Hash{},
	}
	return newKafkaTransportWithDLQ(
		reader,
		writer,
		dlqWriter,
		cfg.BatchSize,
		250*time.Millisecond,
		cfg.TaskTopic,
		cfg.DLQTopic,
		cfg.WorkerID,
	), nil
}

func newKafkaTransport(reader kafkaReader, writer kafkaWriter, batchSize int) *KafkaTransport {
	return newKafkaTransportWithDLQ(
		reader,
		writer,
		nil,
		batchSize,
		250*time.Millisecond,
		"delivery.tasks.v1",
		"delivery.tasks.dlq.v1",
		"data-plane-worker",
	)
}

func newKafkaTransportWithDLQ(
	reader kafkaReader,
	writer kafkaWriter,
	dlqWriter kafkaWriter,
	batchSize int,
	batchWait time.Duration,
	taskTopic string,
	dlqTopic string,
	workerID string,
) *KafkaTransport {
	if batchSize <= 0 {
		batchSize = 1
	}
	if batchWait <= 0 {
		batchWait = 250 * time.Millisecond
	}
	return &KafkaTransport{
		reader:    reader,
		writer:    writer,
		dlqWriter: dlqWriter,
		batchSize: batchSize,
		batchWait: batchWait,
		taskTopic: taskTopic,
		dlqTopic:  dlqTopic,
		workerID:  workerID,
	}
}

func (t *KafkaTransport) Consume(ctx context.Context) ([]TaskMessage, error) {
	tasks := make([]TaskMessage, 0, t.batchSize)
	for fetched := 0; fetched < t.batchSize; fetched++ {
		fetchCtx := ctx
		cancelFetch := func() {}
		if fetched > 0 {
			fetchCtx, cancelFetch = context.WithTimeout(ctx, t.batchWait)
		}
		kafkaMessage, err := t.reader.FetchMessage(fetchCtx)
		cancelFetch()
		if err != nil {
			if fetched > 0 && isKafkaTimeout(err) {
				return tasks, nil
			}
			return nil, err
		}

		var task message.DeliveryTask
		if err := json.Unmarshal(kafkaMessage.Value, &task); err != nil {
			if dlqErr := t.produceDLQ(ctx, kafkaMessage, "invalid_message", err); dlqErr != nil {
				return nil, dlqErr
			}
			if err := t.reader.CommitMessages(ctx, kafkaMessage); err != nil {
				return nil, fmt.Errorf("commit dlq task message: %w", err)
			}
			continue
		}
		if err := validateTaskMessage(task); err != nil {
			if dlqErr := t.produceDLQ(ctx, kafkaMessage, "invalid_message", err); dlqErr != nil {
				return nil, dlqErr
			}
			if err := t.reader.CommitMessages(ctx, kafkaMessage); err != nil {
				return nil, fmt.Errorf("commit dlq task message: %w", err)
			}
			continue
		}

		msg := kafkaMessage
		tasks = append(tasks, TaskMessage{
			Task: task,
			Ack: func(ctx context.Context) error {
				return t.reader.CommitMessages(ctx, msg)
			},
		})
	}
	return tasks, nil
}

func (t *KafkaTransport) Produce(ctx context.Context, result message.DeliveryResult) error {
	payload, err := json.Marshal(result)
	if err != nil {
		return err
	}
	return t.writer.WriteMessages(ctx, kafka.Message{
		Key:   []byte(result.TaskID),
		Value: payload,
	})
}

func (t *KafkaTransport) Close() error {
	readerErr := t.reader.Close()
	writerErr := t.writer.Close()
	var dlqErr error
	if t.dlqWriter != nil {
		dlqErr = t.dlqWriter.Close()
	}
	if readerErr != nil {
		return readerErr
	}
	if writerErr != nil {
		return writerErr
	}
	return dlqErr
}

func (t *KafkaTransport) produceDLQ(ctx context.Context, msg kafka.Message, errorClass string, cause error) error {
	if t.dlqWriter == nil {
		return fmt.Errorf("decode kafka task message: %w", cause)
	}
	payload, err := json.Marshal(DLQMessage{
		Topic:        t.dlqTopic,
		ErrorClass:   errorClass,
		ErrorMessage: cause.Error(),
		WorkerID:     t.workerID,
		FailedAt:     time.Now().UTC().Format(time.RFC3339Nano),
		TaskTopic:    t.taskTopic,
		TaskKey:      string(msg.Key),
		RawMessage:   string(msg.Value),
	})
	if err != nil {
		return err
	}
	if err := t.dlqWriter.WriteMessages(ctx, kafka.Message{
		Key:   msg.Key,
		Value: payload,
	}); err != nil {
		return fmt.Errorf("write kafka dlq message: %w", err)
	}
	return nil
}

func validateTaskMessage(task message.DeliveryTask) error {
	if task.TaskID == "" {
		return fmt.Errorf("task_id is required")
	}
	if task.SchemaVersion == 0 {
		return fmt.Errorf("schema_version is required")
	}
	if task.SchemaVersion >= 2 {
		if task.Source == nil {
			return fmt.Errorf("source is required for schema_version=%d", task.SchemaVersion)
		}
		if task.Source.Type != "object" || task.Source.Bucket == "" || task.Source.Key == "" {
			return fmt.Errorf("valid object source is required for schema_version=%d", task.SchemaVersion)
		}
	}
	return nil
}

func isKafkaTimeout(err error) bool {
	if err == nil {
		return false
	}
	if err == context.DeadlineExceeded || err == io.EOF {
		return true
	}
	var netErr net.Error
	return errors.As(err, &netErr) && netErr.Timeout()
}

func CheckKafkaConnectivity(ctx context.Context, brokers []string) error {
	if len(brokers) == 0 {
		return fmt.Errorf("kafka brokers are required")
	}
	var dialer net.Dialer
	conn, err := dialer.DialContext(ctx, "tcp", brokers[0])
	if err != nil {
		return fmt.Errorf("connect kafka broker %q: %w", brokers[0], err)
	}
	return conn.Close()
}

func ParseBrokerList(raw string) []string {
	parts := strings.Split(raw, ",")
	brokers := make([]string, 0, len(parts))
	for _, part := range parts {
		broker := strings.TrimSpace(part)
		if broker != "" {
			brokers = append(brokers, broker)
		}
	}
	return brokers
}
