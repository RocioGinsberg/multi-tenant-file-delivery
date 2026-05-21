# Data Plane（Go）

高并发流式文件投递、多 sink 协议适配、平台层 dedup precheck。

## 当前状态
**Phase 2 completed**。当前已具备 worker 入口、任务消息模型、文件 source、mock sink、S3/MinIO 单段 PUT sink、本地 outbox bridge、Kafka transport adapter、transport 抽象、上传 receipt SHA-256，以及 message / source / sink / transport / pipeline / worker / CLI 测试。

当前 worker 的本地闭环：
1. 扫描 `delivery.tasks.v1` inbox 目录里的 `.json` 任务。
2. 反序列化为 `DeliveryTask`。
3. 过滤 `upload_status=pending` 且 `severity=ok/warning` 的 item。
4. 用 `FileSource` 从 `temp_dir + src_path` 打开文件。
5. 调用 `Sink.Upload`；当前支持 `mock` 和 `s3`。
6. sink 返回 `key/size/sha256` receipt。
7. worker 写出 `delivery.results.v1/{task_id}.json`，其中 `items[]` 带 item 级 `status/key/size/sha256/error`。
8. control-plane 可通过本地 result consumer 读取 result JSON，并回写 task / item 状态。

## 目录
```
cmd/worker/           main 入口
internal/
  message/            delivery task/result JSON schema
  sink/               Sink interface + mock / S3 sink
  source/             File source
  transport/          Task/result transport interface + file spool / Kafka
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

Kafka transport（默认仍是 file-spool；需要本地 Kafka）：
```bash
go run ./cmd/worker \
  -transport kafka \
  -kafka-brokers localhost:9092 \
  -kafka-task-topic delivery.tasks.v1 \
  -kafka-result-topic delivery.results.v1 \
  -kafka-group-id data-plane-worker \
  -sink mock
```

Worker item 并发默认是 1，可按单个 task 内 item 数量和 sink 能力调高：
```bash
go run ./cmd/worker \
  -transport kafka \
  -kafka-brokers localhost:9092 \
  -item-concurrency 4 \
  -sink mock
```

Object source reference（从 MinIO / S3 staged archive 读取源文件）：
```bash
go run ./cmd/worker \
  -transport file \
  -inbox /tmp/auto_upload_outbox/delivery.tasks.v1 \
  -results /tmp/auto_upload_outbox/delivery.results.v1 \
  -source-mode object \
  -s3-endpoint http://localhost:9000 \
  -s3-region us-east-1 \
  -s3-access-key-id minioadmin \
  -s3-secret-access-key minioadmin \
  -staging-bucket auto-upload-staging \
  -s3-path-style=true \
  -sink mock
```

同一 worker 进程内，object source resolver 会按 `bucket/key` 缓存 staged archive，避免同一 task 多 item 重复下载原始 zip。

Production-like Kafka + object source + S3 sink：
```bash
go run ./cmd/worker \
  -transport kafka \
  -kafka-brokers localhost:9092 \
  -kafka-task-topic delivery.tasks.v1 \
  -kafka-result-topic delivery.results.v1 \
  -kafka-dlq-topic delivery.tasks.dlq.v1 \
  -kafka-group-id data-plane-worker \
  -source-mode object \
  -sink s3 \
  -s3-endpoint http://localhost:9000 \
  -s3-region us-east-1 \
  -s3-bucket auto-upload-dev \
  -s3-access-key-id minioadmin \
  -s3-secret-access-key minioadmin \
  -staging-bucket auto-upload-staging \
  -s3-path-style=true \
  -item-concurrency 4
```

Phase 4 Redis limiter 参数已预留，当前只解析和校验，不改变 worker 执行路径：
```bash
go run ./cmd/worker \
  -transport kafka \
  -kafka-brokers localhost:9092 \
  -redis-url redis://localhost:6379/0 \
  -redis-limiter-enabled \
  -sink mock
```

worker 默认会在处理任务前检查外部依赖：
- `-transport kafka`：用 `-startup-check-timeout` 限制 Kafka broker TCP 连接检查。
- `-source-mode object`：对 `-staging-bucket` 执行 S3 `HeadBucket`。
- `-sink s3`：对 `-s3-bucket` 执行 S3 `HeadBucket`。

本地离线调试可临时加 `-startup-check=false` 跳过这些检查。

Kafka DLQ：
- 默认 topic 为 `delivery.tasks.dlq.v1`，可用 `-kafka-dlq-topic` 覆盖。
- 仅 message 级不可恢复错误进入 DLQ，例如 task payload 不是合法 JSON。
- DLQ 写入成功后才 commit 原 task offset；item 级 source/sink 失败仍写 normal result topic。

本地 Kafka / MinIO：
```bash
cd ../deploy
docker compose up -d mysql kafka minio minio-init
```

Kafka 真 broker 集成测试默认跳过；启动 Compose 后可手动开启：
```bash
RUN_DOCKER_TESTS=1 KAFKA_BROKERS=localhost:9092 go test ./internal/transport
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
- `internal/source`：file resolver、zip archive object resolver、S3 object fetcher。
- `internal/sink`：mock sink、S3 单段 PutObject 请求和配置校验。
- `internal/transport`：file-spool 任务读取和结果写出。
- `internal/pipeline`：可上传 item、跳过不可上传 item、部分失败。
- `internal/worker`：本地 inbox -> mock sink -> result JSON 闭环。
- `control-plane/tests/integration/test_phase2_bridge.py`：Python outbox / source reference -> Go worker -> Python result consumer 的跨语言闭环。

## 后续阶段
- S3 multipart、断点续传、平台层 dedup。
- 并发调度和 backpressure。
