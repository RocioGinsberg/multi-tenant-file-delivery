package transport

import (
	"context"

	"smh_auto_upload/data-plane/internal/message"
)

type TaskConsumer interface {
	Consume(ctx context.Context) ([]message.DeliveryTask, error)
}

type ResultProducer interface {
	Produce(ctx context.Context, result message.DeliveryResult) error
}
