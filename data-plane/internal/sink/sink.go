package sink

import (
	"context"
	"io"
)

type Meta struct {
	TaskID  string
	ItemID  string
	DstPath string
}

type Receipt struct {
	Key    string
	Size   int64
	SHA256 string
}

// Source is the byte-provider contract for sink adapters. Later S3-staged or
// remote URL sources can implement this without changing sink.Upload.
type Source interface {
	Open() (io.ReadCloser, error)
	Size() (int64, error)
	Path() string
}

// Sink hides protocol-specific upload details behind one action. Multipart,
// resume, or instant-upload handshakes belong inside concrete adapters.
type Sink interface {
	Name() string
	Upload(ctx context.Context, src Source, meta Meta) (Receipt, error)
	Close() error
}
