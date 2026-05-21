package source

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
)

type FileSource struct {
	baseDir string
	relPath string
}

// NewFileSource opens paths produced by the control plane's folder extraction.
// relPath is the classifier src_path, so it must stay relative to baseDir.
func NewFileSource(baseDir, relPath string) *FileSource {
	return &FileSource{baseDir: baseDir, relPath: relPath}
}

func (s *FileSource) Path() string {
	return filepath.Join(s.baseDir, s.relPath)
}

func (s *FileSource) Open() (io.ReadCloser, error) {
	f, err := os.Open(s.Path())
	if err != nil {
		return nil, fmt.Errorf("open source %q: %w", s.Path(), err)
	}
	return f, nil
}

func (s *FileSource) Size() (int64, error) {
	stat, err := os.Stat(s.Path())
	if err != nil {
		return 0, err
	}
	return stat.Size(), nil
}
