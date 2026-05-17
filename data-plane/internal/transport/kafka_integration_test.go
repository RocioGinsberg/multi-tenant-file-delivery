package transport

import (
	"context"
	"encoding/json"
	"net"
	"os"
	"strconv"
	"testing"
	"time"

	kafka "github.com/segmentio/kafka-go"

	"smh_auto_upload/data-plane/internal/message"
)

func TestKafkaTransportRoundTripWithDockerBroker(t *testing.T) {
	if os.Getenv("RUN_DOCKER_TESTS") != "1" {
		t.Skip("set RUN_DOCKER_TESTS=1 with docker-compose.phase2.yml running")
	}

	brokers := ParseBrokerList(os.Getenv("KAFKA_BROKERS"))
	if len(brokers) == 0 {
		brokers = []string{"localhost:9092"}
	}

	suffix := time.Now().UTC().Format("20060102150405")
	taskTopic := "delivery.tasks.test." + suffix
	resultTopic := "delivery.results.test." + suffix
	groupID := "data-plane-worker-test-" + suffix

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	createKafkaTopics(t, brokers[0], taskTopic, resultTopic)

	task := message.DeliveryTask{
		SchemaVersion:  1,
		Topic:          "delivery.tasks.v1",
		TaskID:         "task-kafka-it",
		IdempotencyKey: "idem-kafka-it",
		TempDir:        "/tmp/task-kafka-it",
		BucketName:     "auto-upload-dev",
		CreatedAt:      time.Date(2026, 5, 17, 10, 0, 0, 0, time.UTC),
	}
	taskPayload, err := json.Marshal(task)
	if err != nil {
		t.Fatal(err)
	}

	taskWriter := &kafka.Writer{
		Addr:     kafka.TCP(brokers...),
		Topic:    taskTopic,
		Balancer: &kafka.Hash{},
	}
	defer taskWriter.Close()
	if err := taskWriter.WriteMessages(ctx, kafka.Message{
		Key:   []byte(task.TaskID),
		Value: taskPayload,
	}); err != nil {
		t.Fatalf("write task message: %v", err)
	}

	transport, err := NewKafkaTransport(KafkaConfig{
		Brokers:     brokers,
		TaskTopic:   taskTopic,
		ResultTopic: resultTopic,
		GroupID:     groupID,
		BatchSize:   1,
	})
	if err != nil {
		t.Fatalf("create kafka transport: %v", err)
	}
	defer transport.Close()

	tasks, err := transport.Consume(ctx)
	if err != nil {
		t.Fatalf("consume task: %v", err)
	}
	if len(tasks) != 1 || tasks[0].Task.TaskID != task.TaskID {
		t.Fatalf("unexpected tasks: %+v", tasks)
	}
	if err := tasks[0].Ack(ctx); err != nil {
		t.Fatalf("ack task: %v", err)
	}

	if err := transport.Produce(ctx, message.DeliveryResult{
		Topic:     "delivery.results.v1",
		TaskID:    task.TaskID,
		Status:    "uploaded",
		Uploaded:  1,
		StartedAt: time.Date(2026, 5, 17, 10, 0, 0, 0, time.UTC),
		EndedAt:   time.Date(2026, 5, 17, 10, 1, 0, 0, time.UTC),
	}); err != nil {
		t.Fatalf("produce result: %v", err)
	}

	resultReader := kafka.NewReader(kafka.ReaderConfig{
		Brokers: brokers,
		Topic:   resultTopic,
	})
	defer resultReader.Close()
	resultMessage, err := resultReader.ReadMessage(ctx)
	if err != nil {
		t.Fatalf("read result: %v", err)
	}
	var result message.DeliveryResult
	if err := json.Unmarshal(resultMessage.Value, &result); err != nil {
		t.Fatal(err)
	}
	if result.TaskID != task.TaskID || result.Status != "uploaded" {
		t.Fatalf("unexpected result: %+v", result)
	}
}

func createKafkaTopics(t *testing.T, broker string, topics ...string) {
	t.Helper()

	conn, err := kafka.Dial("tcp", broker)
	if err != nil {
		t.Fatalf("dial kafka broker: %v", err)
	}
	defer conn.Close()

	controller, err := conn.Controller()
	if err != nil {
		t.Fatalf("get kafka controller: %v", err)
	}
	controllerConn, err := kafka.Dial(
		"tcp",
		net.JoinHostPort(controller.Host, strconv.Itoa(controller.Port)),
	)
	if err != nil {
		t.Fatalf("dial kafka controller: %v", err)
	}
	defer controllerConn.Close()

	topicConfigs := make([]kafka.TopicConfig, 0, len(topics))
	for _, topic := range topics {
		topicConfigs = append(topicConfigs, kafka.TopicConfig{
			Topic:             topic,
			NumPartitions:     1,
			ReplicationFactor: 1,
		})
	}
	if err := controllerConn.CreateTopics(topicConfigs...); err != nil {
		t.Fatalf("create kafka topics: %v", err)
	}
}
