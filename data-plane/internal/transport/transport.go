package transport

import (
	"context"

	"smh_auto_upload/data-plane/internal/message"
)

type TaskMessage struct {
	Task message.DeliveryTask
	Ack  func(context.Context) error
}

func NewTaskMessage(task message.DeliveryTask) TaskMessage {
	return TaskMessage{
		Task: task,
		Ack:  func(context.Context) error { return nil },
	}
}

type TaskConsumer interface {
	Consume(ctx context.Context) ([]TaskMessage, error)
}

type ResultProducer interface {
	Produce(ctx context.Context, result message.DeliveryResult) error
}

type DLQMessage struct {
	Topic        string `json:"topic"`
	ErrorClass   string `json:"error_class"`
	ErrorMessage string `json:"error_message"`
	WorkerID     string `json:"worker_id"`
	FailedAt     string `json:"failed_at"`
	TaskTopic    string `json:"task_topic"`
	TaskKey      string `json:"task_key,omitempty"`
	RawMessage   string `json:"raw_message"`
}
