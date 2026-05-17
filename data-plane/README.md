# Data Plane（Go）

高并发流式文件投递、多 sink 协议适配、平台层 dedup precheck。

## 当前状态
**Phase 2 scaffolded + local bridge covered**。当前已具备 worker 入口、任务消息模型、文件 source、mock sink、S3/MinIO 单段 PUT sink、本地 outbox bridge、transport 抽象，以及 message / source / sink / transport / pipeline / worker / CLI 测试。

当前 worker 的本地闭环：
1. 扫描 `delivery.tasks.v1` inbox 目录里的 `.json` 任务。
2. 反序列化为 `DeliveryTask`。
3. 过滤 `upload_status=pending` 且 `severity=ok/warning` 的 item。
4. 用 `FileSource` 从 `temp_dir + src_path` 打开文件。
5. 调用 `Sink.Upload`；当前支持 `mock` 和 `s3`。
6. 写出 `delivery.results.v1/{task_id}.json`。

## 目录
```
cmd/worker/           main 入口
internal/
  message/            delivery task/result JSON schema
  sink/               Sink interface + mock / S3 sink
  source/             File source
  transport/          Task/result transport interface + file spool
  pipeline/           文件处理编排
  worker/             inbox -> sink -> result 的 worker 主循环
```

## 接口规范
参见 [../docs/SINK_PROTOCOL.md](../docs/SINK_PROTOCOL.md)。

## 启动方式
```bash
cd data-plane
go run ./cmd/worker
```

可选参数：
```bash
go run ./cmd/worker \
  -inbox /tmp/auto_upload_outbox/delivery.tasks.v1 \
  -results /tmp/auto_upload_outbox/delivery.results.v1 \
  -sink mock
```

MinIO / S3-compatible 单段上传：
```bash
go run ./cmd/worker \
  -sink s3 \
  -s3-endpoint http://localhost:9000 \
  -s3-region us-east-1 \
  -s3-bucket auto-upload-dev \
  -s3-access-key-id minioadmin \
  -s3-secret-access-key minioadmin \
  -s3-path-style=true
```

## 测试
```bash
cd data-plane
GOCACHE=/tmp/smh_go_cache go test ./...
```

当前覆盖：
- `cmd/worker`：CLI 参数 -> inbox -> result JSON 的本地 bridge。
- `internal/message`：任务消息 JSON round-trip 和 metadata 兼容。
- `internal/source`：文件路径、打开、大小、缺失文件错误。
- `internal/sink`：mock sink、S3 单段 PutObject 请求和配置校验。
- `internal/transport`：file-spool 任务读取和结果写出。
- `internal/pipeline`：可上传 item、跳过不可上传 item、部分失败。
- `internal/worker`：本地 inbox -> mock sink -> result JSON 闭环。

## 尚未实现
- Kafka consumer / producer。
- S3 multipart、断点续传、checksum / 平台层 dedup。
- 并发调度和 backpressure。
