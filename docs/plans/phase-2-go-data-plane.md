# Phase 2 — Go 数据面

> 当前状态：完成。Phase 2 已完成 Go 数据面 worker、file-spool 本地桥接、Kafka transport adapter、S3 / MinIO 单段 PUT sink、结果回写，以及跨语言集成验证。Phase 1 的分类逻辑继续以 control plane 为准；Phase 2 只接管分类后的上传执行。

## Summary
- 控制面维持分类、确认、状态机和事件写入。
- 数据面负责消费 Phase 1 产出的任务消息，完成文件读取、sink 上传和结果回传。
- 本地开发默认使用 file-spool bridge；Kafka transport 已实现并通过 Docker broker 集成测试。

## Key Changes
- [x] 新增任务消息模型：`delivery.tasks.v1` / `delivery.results.v1`。
- [x] 新增 Go worker 主干：`cmd/worker`、`message`、`source`、`sink`、`pipeline`、`worker`。
- [x] 控制面上传入口切到双模式：
  - `python`：保留 Phase 1 直传，便于回归。
  - `go-worker`：写入 outbox，交给 Go worker 消费。
- [x] 本地 bridge 覆盖：worker/CLI 读取 inbox JSON、执行 mock sink、写出 result JSON。
- [x] 首个真实 sink adapter：S3 / MinIO 单段 `PutObject`。
- [x] 抽象 task/result transport：当前实现 file-spool，Kafka 可替换此层。
- [x] Go data-plane Kafka transport adapter + worker CLI 选择项。
- [x] Docker Compose Kafka / MinIO scaffold 与 Go Kafka broker 集成测试（默认 skip）。
- [x] 控制面 Kafka producer / consumer adapter，并支持 `DELIVERY_TRANSPORT=kafka` 发布任务。
- [x] 控制面 Kafka producer / consumer 的 Docker broker 集成测试。
- [x] S3 / mock sink receipt 返回 SHA-256。
- [x] `delivery.results.v1` 返回 item 级上传 receipt / error。
- [x] 控制面本地 result consumer 可应用 `delivery.results.v1`，回写 task / item 状态。

## Deferred
- S3 multipart / resume：进入后续高性能上传阶段实现。
- 平台层 dedup：依赖 physical object / metadata 表设计，进入后续存储治理阶段实现。
- worker 并发调度 / backpressure：依赖真实负载和限流策略，进入后续性能阶段实现。

## Test Plan
- [x] 控制面单测：消息构建、outbox 写入、go-worker 模式路由。
- [x] Go 单测：消息 JSON round-trip、file source、file transport、mock sink pipeline、S3 sink 单段上传、worker/CLI 本地 bridge。
- [x] 集成验证：control-plane outbox 生成 -> Go worker 消费 -> result 输出 -> control-plane 回写状态。

## Current Implementation
- `control-plane/app/services/delivery.py`：构建 `DeliveryTaskMessage`，本地 outbox publisher 写入 `delivery.tasks.v1/{task_id}.json`。
- `control-plane/app/services/delivery.py`：定义 file-spool / Kafka delivery producer 和 result consumer；`consume_delivery_results()` 调用 `apply_delivery_result()` 回写 task / item 状态。
- `control-plane/app/api/tasks.py`：`DELIVERY_BACKEND=go-worker` 时发布任务消息并把 task 状态更新为 `queued`；`DELIVERY_TRANSPORT=file|kafka` 决定 transport。
- `data-plane/cmd/worker`：本地 worker CLI，默认读取 `/tmp/auto_upload_outbox/delivery.tasks.v1`。
- `data-plane/internal/worker`：目录扫描、JSON decode、调用 pipeline、写 `delivery.results.v1` result；result item 明细带上传 receipt 或错误。
- `data-plane/internal/transport`：定义 task/result transport 接口，当前支持 file-spool 和 Kafka adapter；file-spool 仍是默认本地路径。
- `data-plane/internal/pipeline`：只上传 pending 且 severity 为 ok/warning 的 item。
- `data-plane/internal/source`：从控制面解压目录读取源文件。
- `data-plane/internal/sink`：定义 `Sink` / `Source` 接口，当前实现 `MockSink` 和 S3 / MinIO 单段 PUT sink，receipt 返回 `key/size/sha256`。

## Verification
- `GOCACHE=/tmp/smh_go_cache go test ./...`
- `cd control-plane && uv run pytest tests/integration/test_phase2_bridge.py`
- `cd deploy && docker compose up -d kafka minio minio-init`
- `cd data-plane && RUN_DOCKER_TESTS=1 KAFKA_BROKERS=localhost:9092 go test ./internal/transport`
- `cd control-plane && RUN_DOCKER_TESTS=1 KAFKA_BOOTSTRAP_SERVERS=localhost:9092 uv run pytest tests/integration/test_delivery_kafka_docker.py`

## Assumptions
- 分类实现继续参考 control plane 的 Phase 1 版本，不回退到 `_legacy` 业务耦合脚本。
- Kafka 主题命名保留；file-spool 仍是默认开发路径，Kafka adapter 已就绪。
