package message

import "time"

// DeliveryItem is one already-classified task_item that the data plane may upload.
// Classification fields are carried for audit/progress context; routing decisions
// remain owned by the Python control plane.
type DeliveryItem struct {
	ItemID string `json:"item_id"`
	// SrcPath is relative to DeliveryTask.TempDir, not an arbitrary absolute path.
	SrcPath           string `json:"src_path"`
	Filename          string `json:"filename"`
	Ext               string `json:"ext"`
	FileSize          int64  `json:"file_size"`
	DstPath           string `json:"dst_path"`
	Severity          string `json:"severity"`
	TargetNameRaw     string `json:"target_name_raw"`
	TargetNameMatched string `json:"target_name_matched"`
	DocumentType      string `json:"document_type"`
	CategoryName      string `json:"category_name"`
	UploadStatus      string `json:"upload_status"`
}

// DeliveryTask is the Phase 2 task message contract.
//
// The file-spool transport currently writes this shape to disk; the Kafka
// transport should preserve the same JSON fields.
type DeliveryTask struct {
	SchemaVersion   int            `json:"schema_version"`
	Topic           string         `json:"topic"`
	TaskID          string         `json:"task_id"`
	IdempotencyKey  string         `json:"idempotency_key"`
	SubmissionLabel string         `json:"submission_label"`
	TempDir         string         `json:"temp_dir"`
	BucketName      string         `json:"bucket_name"`
	CreatedAt       time.Time      `json:"created_at"`
	Items           []DeliveryItem `json:"items"`
	Traceparent     string         `json:"traceparent,omitempty"`
	Metadata        map[string]any `json:"metadata,omitempty"`
}

// DeliveryResult is the worker's result event. The control plane will later
// consume this from delivery.results.v1 and update task/item state.
type DeliveryResult struct {
	Topic     string               `json:"topic"`
	TaskID    string               `json:"task_id"`
	Status    string               `json:"status"`
	Uploaded  int                  `json:"uploaded"`
	Failed    int                  `json:"failed"`
	Processed int                  `json:"processed"`
	Items     []DeliveryResultItem `json:"items,omitempty"`
	StartedAt time.Time            `json:"started_at"`
	EndedAt   time.Time            `json:"ended_at"`
	Error     string               `json:"error,omitempty"`
}

type DeliveryResultItem struct {
	ItemID string `json:"item_id"`
	Status string `json:"status"`
	Key    string `json:"key,omitempty"`
	Size   int64  `json:"size,omitempty"`
	SHA256 string `json:"sha256,omitempty"`
	Error  string `json:"error,omitempty"`
}
