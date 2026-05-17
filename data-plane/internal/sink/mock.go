package sink

import (
	"context"
	"fmt"
	"io"
	"sync"
)

type MockSink struct {
	mu      sync.Mutex
	objects map[string][]byte
}

// NewMockSink keeps uploaded bytes in memory so pipeline tests do not need S3.
func NewMockSink() *MockSink {
	return &MockSink{objects: make(map[string][]byte)}
}

func (s *MockSink) Name() string { return "mock" }

func (s *MockSink) Upload(ctx context.Context, src Source, meta Meta) (Receipt, error) {
	_ = ctx
	reader, err := src.Open()
	if err != nil {
		return Receipt{}, err
	}
	defer reader.Close()

	data, err := io.ReadAll(reader)
	if err != nil {
		return Receipt{}, err
	}

	s.mu.Lock()
	s.objects[meta.DstPath] = data
	s.mu.Unlock()

	return Receipt{Key: meta.DstPath, Size: int64(len(data))}, nil
}

func (s *MockSink) Close() error { return nil }

func (s *MockSink) Object(path string) ([]byte, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	data, ok := s.objects[path]
	return data, ok
}

func (s *MockSink) String() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return fmt.Sprintf("mock sink (%d objects)", len(s.objects))
}
