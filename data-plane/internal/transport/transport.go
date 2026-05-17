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
