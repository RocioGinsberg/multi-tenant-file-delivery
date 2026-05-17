package source

import (
	"archive/zip"
	"bytes"
	"context"
	"fmt"
	"io"
	"sync"

	"smh_auto_upload/data-plane/internal/message"
)

type Source interface {
	Open() (io.ReadCloser, error)
	Size() (int64, error)
	Path() string
}

type Resolver interface {
	Resolve(ctx context.Context, task message.DeliveryTask, item message.DeliveryItem) (Source, error)
}

type FileResolver struct{}

func NewFileResolver() FileResolver {
	return FileResolver{}
}

func (r FileResolver) Resolve(_ context.Context, task message.DeliveryTask, item message.DeliveryItem) (Source, error) {
	return NewFileSource(task.TempDir, item.SrcPath), nil
}

type ObjectFetcher interface {
	GetObject(ctx context.Context, bucket, key string) ([]byte, error)
}

type ZipArchiveResolver struct {
	fetcher ObjectFetcher
	mu      sync.Mutex
	cache   map[string][]byte
}

func NewZipArchiveResolver(fetcher ObjectFetcher) *ZipArchiveResolver {
	return &ZipArchiveResolver{
		fetcher: fetcher,
		cache:   make(map[string][]byte),
	}
}

func (r *ZipArchiveResolver) Resolve(ctx context.Context, task message.DeliveryTask, item message.DeliveryItem) (Source, error) {
	if task.Source == nil {
		return nil, fmt.Errorf("task %s has no source reference", task.TaskID)
	}
	sourcePath := item.SourcePath
	if sourcePath == "" {
		sourcePath = item.SrcPath
	}
	archive, err := r.getArchive(ctx, task.Source.Bucket, task.Source.Key)
	if err != nil {
		return nil, err
	}
	reader, err := zip.NewReader(bytes.NewReader(archive), int64(len(archive)))
	if err != nil {
		return nil, fmt.Errorf("open source archive %q: %w", task.Source.Key, err)
	}
	for _, file := range reader.File {
		if file.Name != sourcePath {
			continue
		}
		rc, err := file.Open()
		if err != nil {
			return nil, fmt.Errorf("open source item %q: %w", sourcePath, err)
		}
		data, err := io.ReadAll(rc)
		closeErr := rc.Close()
		if err != nil {
			return nil, fmt.Errorf("read source item %q: %w", sourcePath, err)
		}
		if closeErr != nil {
			return nil, fmt.Errorf("close source item %q: %w", sourcePath, closeErr)
		}
		return NewMemorySource(fmt.Sprintf("s3://%s/%s#%s", task.Source.Bucket, task.Source.Key, sourcePath), data), nil
	}
	return nil, fmt.Errorf("source item %q not found in archive %q", sourcePath, task.Source.Key)
}

func (r *ZipArchiveResolver) getArchive(ctx context.Context, bucket, key string) ([]byte, error) {
	cacheKey := bucket + "/" + key
	r.mu.Lock()
	defer r.mu.Unlock()
	archive, ok := r.cache[cacheKey]
	if ok {
		return archive, nil
	}

	archive, err := r.fetcher.GetObject(ctx, bucket, key)
	if err != nil {
		return nil, err
	}
	r.cache[cacheKey] = archive
	return archive, nil
}

type MemorySource struct {
	path string
	data []byte
}

func NewMemorySource(path string, data []byte) *MemorySource {
	return &MemorySource{path: path, data: data}
}

func (s *MemorySource) Path() string {
	return s.path
}

func (s *MemorySource) Open() (io.ReadCloser, error) {
	return io.NopCloser(bytes.NewReader(s.data)), nil
}

func (s *MemorySource) Size() (int64, error) {
	return int64(len(s.data)), nil
}
