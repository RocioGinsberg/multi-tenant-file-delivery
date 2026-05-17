package transport

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	kafka "github.com/segmentio/kafka-go"

	"smh_auto_upload/data-plane/internal/message"
)

type KafkaConfig struct {
	Brokers     []string
	TaskTopic   string
	ResultTopic string
	GroupID     string
	BatchSize   int
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
	batchSize int
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
	if cfg.GroupID == "" {
		cfg.GroupID = "data-plane-worker"
	}
	if cfg.BatchSize <= 0 {
		cfg.BatchSize = 1
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
	return newKafkaTransport(reader, writer, cfg.BatchSize), nil
}

func newKafkaTransport(reader kafkaReader, writer kafkaWriter, batchSize int) *KafkaTransport {
	if batchSize <= 0 {
		batchSize = 1
	}
	return &KafkaTransport{reader: reader, writer: writer, batchSize: batchSize}
}

func (t *KafkaTransport) Consume(ctx context.Context) ([]TaskMessage, error) {
	tasks := make([]TaskMessage, 0, t.batchSize)
	for len(tasks) < t.batchSize {
		kafkaMessage, err := t.reader.FetchMessage(ctx)
		if err != nil {
			return nil, err
		}

		var task message.DeliveryTask
		if err := json.Unmarshal(kafkaMessage.Value, &task); err != nil {
			return nil, fmt.Errorf("decode kafka task message: %w", err)
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
	if readerErr != nil {
		return readerErr
	}
	return writerErr
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
