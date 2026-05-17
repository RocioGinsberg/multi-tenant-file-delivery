# Phase 2 — Go 数据面

> 当前状态：实现中，本地 outbox bridge scaffold 已落地，S3 / MinIO 单段 PUT sink 已接入并补齐 Go 单测。Phase 1 的分类逻辑继续以 control plane 为准；Phase 2 只接管分类后的上传执行。

## Summary
- 控制面维持分类、确认、状态机和事件写入。
- 数据面负责消费 Phase 1 产出的任务消息，完成文件读取、sink 上传和结果回传。
- 本地开发先使用 outbox bridge 作为 Kafka 之前的可运行过渡层。

## Key Changes
- [x] 新增任务消息模型：`delivery.tasks.v1` / `delivery.results.v1`。
- [x] 新增 Go worker 主干：`cmd/worker`、`message`、`source`、`sink`、`pipeline`、`worker`。
- [x] 控制面上传入口切到双模式：
  - `python`：保留 Phase 1 直传，便于回归。
  - `go-worker`：写入 outbox，交给 Go worker 消费。
- [x] 本地 bridge 覆盖：worker/CLI 读取 inbox JSON、执行 mock sink、写出 result JSON。
- [x] 首个真实 sink adapter：S3 / MinIO 单段 `PutObject`。
- [x] 抽象 task/result transport：当前实现 file-spool，Kafka 可替换此层。
- [ ] Kafka transport 替换目录扫描。
- [x] S3 / mock sink receipt 返回 SHA-256。
- [x] `delivery.results.v1` 返回 item 级上传 receipt / error。
- [x] 控制面本地 result consumer 可应用 `delivery.results.v1`，回写 task / item 状态。
- [ ] S3 multipart / resume / dedup。

## Test Plan
- [x] 控制面单测：消息构建、outbox 写入、go-worker 模式路由。
- [x] Go 单测：消息 JSON round-trip、file source、file transport、mock sink pipeline、S3 sink 单段上传、worker/CLI 本地 bridge。
- [x] 集成验证：control-plane outbox 生成 -> Go worker 消费 -> result 输出 -> control-plane 回写状态。

## Current Implementation
- `control-plane/app/services/delivery.py`：构建 `DeliveryTaskMessage`，本地 outbox publisher 写入 `delivery.tasks.v1/{task_id}.json`。
- `control-plane/app/services/delivery.py`：定义 `DeliveryResultMessage`，本地 result consumer 读取 `delivery.results.v1/*.json`，`consume_delivery_results()` 调用 `apply_delivery_result()` 回写 task / item 状态。
- `control-plane/app/api/tasks.py`：`DELIVERY_BACKEND=go-worker` 时发布任务消息并把 task 状态更新为 `queued`。
- `data-plane/cmd/worker`：本地 worker CLI，默认读取 `/tmp/auto_upload_outbox/delivery.tasks.v1`。
- `data-plane/internal/worker`：目录扫描、JSON decode、调用 pipeline、写 `delivery.results.v1` result；result item 明细带上传 receipt 或错误。
- `data-plane/internal/transport`：定义 task/result transport 接口，当前 file-spool 实现负责目录扫描和 result JSON 写出。
- `data-plane/internal/pipeline`：只上传 pending 且 severity 为 ok/warning 的 item。
- `data-plane/internal/source`：从控制面解压目录读取源文件。
- `data-plane/internal/sink`：定义 `Sink` / `Source` 接口，当前实现 `MockSink` 和 S3 / MinIO 单段 PUT sink，receipt 返回 `key/size/sha256`。

## Verification
- `GOCACHE=/tmp/smh_go_cache go test ./...`
- `cd control-plane && uv run pytest tests/integration/test_phase2_bridge.py`

## Assumptions
- 分类实现继续参考 control plane 的 Phase 1 版本，不回退到 `_legacy` 业务耦合脚本。
- Kafka 主题命名先保留，但本地桥接先用文件 outbox，后续可平移为真实 Kafka producer/consumer。
