# Phase 3.x — Source reference 生产化与 worker 集群前置条件

> **状态**：Done（3.9-3.21 已完成）
> **目标**：在 Phase 3 已打通 source reference 基础链路后，补齐 Kafka 真实链路、性能、GC、幂等和生产化运行边界。
> **完成定义**：Kafka + object source 的端到端链路可复验；staging source 有可清理生命周期；重复 result 不破坏 DB 最终状态；剩余 worker 集群化风险有明确 RFC / 测试 / benchmark 入口。
> **关联计划**：[Phase 3 — MySQL 数据层与 source reference 迁移](./phase-3-data-layer-and-source-ref.md)
> **关联 RFC**：[0002 Stateless data-plane 与 source reference 迁移](../RFC/0002-stateless-data-plane-source-ref.md)、[0003 Kafka retry / DLQ / idempotency semantics](../RFC/0003-kafka-retry-dlq-idempotency.md)

## Summary

Phase 3.x 不是继续扩大 Phase 3 的基础迁移范围，而是单独承接“worker 集群前置条件”的 hardening 工作。

Phase 3 已证明：

- control-plane 可以把源 archive 暂存到 MinIO / S3 staging bucket。
- delivery task message 可以携带 source reference。
- Go worker 可以用 object source mode 从 staged archive 读取 item bytes。

Phase 3.x 继续证明：

- 生产形态 transport 使用 Kafka 时，task / result 双向链路也成立。
- worker 不会在同一 task 内重复下载同一个 archive。
- staged source object 有 metadata、retention 和 GC 入口。
- Kafka at-least-once 下，重复 result apply 的 DB 最终状态稳定。

## 一、拆分原则

- Phase 3 只保留 3.1-3.8：MySQL、source reference 基础模型、file-spool/object source bridge 和文档。
- Phase 3.x 从 3.9 开始：Kafka 真实链路、性能、GC、幂等、DLQ、benchmark、worker readiness。
- 单个 plan 文件不继续无限增长；如果 3.x 后续继续扩大，按主题拆成 `phase-3x-gc.md`、`phase-3x-worker-performance.md` 或对应 RFC。
- 实现优先小步提交：每个 checkpoint 应有明确测试或文档验收。

## 二、调试验证主线

这部分看起来像“调试工作多”，实际是在把跨组件边界固化为测试：

```text
control-plane
  -> receive folder files and build internal archive
  -> stage internal archive to MinIO/S3
  -> publish schema_version=2 task to Kafka

data-plane worker
  -> consume Kafka task
  -> fetch staged archive by source reference
  -> upload item to sink
  -> produce Kafka result

control-plane
  -> consume Kafka result
  -> apply task/item DB status
```

每次调试失败都应沉淀到以下位置之一：

- 集成测试：证明跨语言 / 跨组件链路可复验。
- 单元测试：证明 resolver、GC、幂等等局部语义。
- RFC：记录 Kafka ack、retry、DLQ 等暂不立刻实现的生产语义。
- README / plan：记录运行命令和阶段边界。

## 三、任务清单

状态标记：`[ ]` 未开始 / `[~]` 进行中 / `[x]` 已完成 / `[!]` 阻塞

### 3.9 Kafka source reference 端到端验证

- **状态**：`[x]`
- **L 等级**：L2
- **范围**：
  - control-plane 将 schema_version=2 source reference task 发布到 Kafka。
  - Go worker 使用 Kafka transport 和 object source mode 消费任务。
  - Go worker 将 result 写回 Kafka。
  - control-plane Kafka result consumer 回写 task / item 状态。
- **验收**：
  - 测试不依赖 control-plane 与 data-plane 共享本地解压目录。
  - Kafka topic 使用测试唯一名，避免重跑时读取历史消息。
- **实际变更**：
  - `control-plane/tests/integration/test_phase2_bridge.py`：新增 source reference Kafka bridge round-trip。
- **验证**：
  - `cd deploy && docker compose up -d kafka minio minio-init`
  - `cd control-plane && RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest tests/integration/test_phase2_bridge.py::test_source_reference_kafka_bridge_round_trip`

### 3.10 Archive fetch cache

- **状态**：`[x]`
- **L 等级**：L2
- **范围**：
  - data-plane object source resolver 按 `bucket/key` 缓存 staged archive bytes。
  - 同一 task 多 item 解析时避免重复 `GetObject`。
- **验收**：
  - 同一 archive 内两个 item 解析只触发一次 object fetch。
- **实际变更**：
  - `data-plane/internal/source/resolver.go`：`ZipArchiveResolver` 增加 archive cache。
  - `data-plane/internal/source/resolver_test.go`：新增 fetch count 回归测试。
- **验证**：
  - `cd data-plane && GOCACHE=/tmp/smh_go_cache go test ./internal/source`

### 3.11 Worker batch / concurrency tuning

- **状态**：`[x]`
- **L 等级**：L2
- **范围**：
  - 明确 worker 单次 Kafka batch、item 并发、sink 并发边界。
  - 先做 worker 内部可控并发，不做 autoscaling。
- **验收**：
  - Go 单测覆盖并发上限。
  - 现有 Kafka / file-spool bridge 不回归。
- **实际变更**：
  - `data-plane/internal/pipeline`：新增 `Options{MaxItemConcurrency}` 和并发 item 处理。
  - `data-plane/internal/worker`：worker config 传递 item concurrency 到 pipeline。
  - `data-plane/cmd/worker`：新增 `-item-concurrency` CLI 参数，默认 1。
  - `data-plane/README.md`：记录 item 并发配置示例。
- **验证**：
  - `cd data-plane && GOCACHE=/tmp/smh_go_cache go test ./internal/pipeline ./cmd/worker ./internal/worker`

### 3.12 Benchmark baseline

- **状态**：`[x]`
- **L 等级**：L1
- **范围**：
  - 新增 benchmark 文档或脚本入口。
  - 记录 10 / 100 / 1000 item，小文件 / 中等文件下的本地实测。
- **验收**：
  - 有可复跑命令。
  - `docs/BENCHMARKS.md` 或本计划记录一组基线结果。
- **实际变更**：
  - `data-plane/internal/pipeline`：新增 `BenchmarkProcessTaskMockSink`，覆盖 10 / 100 / 1000 item、16 KiB / 1 MiB payload、item concurrency 1 / 4。
  - `docs/BENCHMARKS.md`：记录复跑命令、测试环境、代表结果和适用边界。
- **验证**：
  - `cd data-plane && GOCACHE=/tmp/smh_go_cache go test ./internal/pipeline`
  - `cd data-plane && GOCACHE=/tmp/smh_go_cache go test ./internal/pipeline -run '^$' -bench 'BenchmarkProcessTaskMockSink' -benchmem -count 3`

### 3.13 Staging source metadata

- **状态**：`[x]`
- **L 等级**：L1
- **范围**：
  - object source task 发布时，把 staging source metadata 写入 task event。
  - 不新增表，先复用 append-only event log。
- **验收**：
  - `task_staged_source` event 包含 bucket/key/sha256/size。
- **实际变更**：
  - `control-plane/app/api/tasks.py`：object source 模式下追加 `task_staged_source` event。
  - `control-plane/tests/integration/test_api_tasks.py`：覆盖 event payload。

### 3.14 Staging object cleanup service

- **状态**：`[x]`
- **L 等级**：L2
- **范围**：
  - 扫描终态 task 的过期 `task_staged_source` event。
  - 删除对应 S3 / MinIO object。
  - 删除成功后写 `task_staged_source_deleted` event，避免重复删除。
- **验收**：
  - 未终态 task 不删。
  - 未过 retention 不删。
  - 已删除 source 不重复删。
- **实际变更**：
  - `control-plane/app/services/staging_cleanup.py`：新增 cleanup service。
  - `control-plane/tests/integration/test_staging_cleanup.py`：覆盖删除、跳过、重复删除保护。

### 3.15 GC command

- **状态**：`[x]`
- **L 等级**：L1
- **范围**：
  - 提供手动运行的 staging source cleanup job。
  - 暂不引入 Celery / cron / scheduler 框架。
- **验收**：
  - 可通过 `python -m app.jobs.cleanup_staging_sources` 手动执行。
- **实际变更**：
  - `control-plane/app/jobs/cleanup_staging_sources.py`：新增 CLI job。
  - `control-plane/tests/unit/test_cleanup_staging_job.py`：覆盖参数解析。
- **运行**：
  - `cd control-plane && python -m app.jobs.cleanup_staging_sources --retention-days 7 --bucket-name auto-upload-staging`

### 3.16 Kafka retry / DLQ contract

- **状态**：`[x]`
- **L 等级**：L1
- **范围**：
  - 明确 Kafka ack、retry、DLQ 和幂等边界。
  - 暂不实现 DLQ topic。
- **实际变更**：
  - `docs/RFC/0003-kafka-retry-dlq-idempotency.md`：新增生产化语义草案。

### 3.17 Idempotency hardening

- **状态**：`[x]`
- **L 等级**：L2
- **范围**：
  - 当前阶段先覆盖 duplicate result apply 的 DB 最终状态稳定性。
  - 后续继续补重复 task execution / sink 幂等 key 的端到端验证。
- **实际变更**：
  - `control-plane/tests/integration/test_delivery.py`：新增重复 result apply 回归测试。
- **补充变更**：
  - `control-plane/tests/integration/test_phase2_bridge.py`：新增重复 source reference Kafka task 端到端测试。
  - mock sink 以 `dst_path` 作为稳定 object key，重复执行同一 task 时最终 object key 不漂移。
- **验证**：
  - `cd control-plane && .venv/bin/python -m pytest tests/integration/test_delivery.py::test_apply_delivery_result_is_stable_for_duplicate_result`
  - `cd control-plane && RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest tests/integration/test_phase2_bridge.py::test_duplicate_source_reference_kafka_task_keeps_final_state_stable`

### 3.18 Config profiles

- **状态**：`[x]`
- **L 等级**：L1
- **范围**：
  - 整理 local / docker / production-like 配置差异。
  - 明确 MySQL / Kafka / MinIO / S3 参数从 env 注入。
- **验收**：
  - README 有 object source + Kafka 的最小生产形态配置表。
- **实际变更**：
  - `control-plane/README.md`：新增 local file-spool、Docker object source、production-like Kafka 配置 profiles 和最小 env 示例。
  - `data-plane/README.md`：新增 production-like Kafka + object source + S3 sink worker 启动命令。
- **验证**：
  - Not run; documentation-only change.

### 3.19 Worker health / readiness

- **状态**：`[x]`
- **L 等级**：L2
- **范围**：
  - worker 启动时检查 Kafka / S3 配置。
  - 先做启动前快速失败和清晰日志；是否暴露 HTTP health 后续再定。
- **验收**：
  - 错误 broker / S3 endpoint 能快速失败。
  - 日志能定位是 Kafka、source storage 还是 sink 配置错误。
- **实际变更**：
  - `data-plane/cmd/worker`：新增默认开启的 `-startup-check`、`-startup-check-timeout` 和 `-staging-bucket` 参数。
  - `data-plane/internal/transport`：新增 Kafka broker TCP startup check。
  - `data-plane/internal/source`：object source fetcher 支持 staging bucket `HeadBucket` 检查。
  - `data-plane/internal/sink`：S3 sink 支持目标 bucket `HeadBucket` 检查。
  - `data-plane/README.md`：记录 startup check 行为和临时跳过方式。
- **验证**：
  - `cd data-plane && GOCACHE=/tmp/smh_go_cache go test ./...`

### 3.20 Kafka task DLQ

- **状态**：`[x]`
- **L 等级**：L1
- **范围**：
  - 先覆盖 task message 级不可恢复错误，不改变 item 级失败语义。
  - invalid JSON task 写入 `delivery.tasks.dlq.v1`，写入成功后 commit 原 task offset。
  - DLQ payload 保留原始 message、error_class、error_message、worker_id、failed_at 和原 topic/key。
- **验收**：
  - Go 单测覆盖 invalid JSON task 的 DLQ write + commit 顺序。
  - README / RFC 记录 DLQ topic 和适用边界。
- **实际变更**：
  - `data-plane/internal/transport`：Kafka transport 新增 DLQ writer 和 `DLQMessage` payload。
  - `data-plane/cmd/worker`：新增 `-kafka-dlq-topic` 参数，默认 `delivery.tasks.dlq.v1`。
  - `docs/RFC/0003-kafka-retry-dlq-idempotency.md`：RFC 标记 Accepted，补充最小 DLQ 实现语义。
- **验证**：
  - `cd data-plane && GOCACHE=/tmp/smh_go_cache go test ./...`

### 3.21 Review hardening sweep

- **状态**：`[x]`
- **L 等级**：L2
- **范围**：
  - HQ 上传入口改为文件夹多文件上传；用户不再手工上传 zip。
  - control-plane 继续生成内部 archive，复用分类和 source reference 管线。
  - 修复 Kafka result consumer 单条 offset commit、Kafka partial batch、invalid message DLQ 校验。
  - 修复 object source publish 失败后的 staging cleanup 补偿。
  - retry failed items 后恢复 task 到可再次 upload 的状态。
  - 同步 Phase 状态文档、README、ROADMAP、AGENTS。
- **验收**：
  - 文件夹上传 e2e 覆盖相对路径。
  - Kafka ack / partial batch / missing task_id DLQ 有回归测试。
  - `ruff check app tests` 通过。

## 四、建议执行顺序

1. 完成 `3.21` review hardening sweep。
2. 跑完非 Docker 全量测试和 lint。
3. Docker Kafka / MinIO / MySQL 链路按需手动复验。
4. 进入 Phase 4 前，若 3.x 继续扩大，应按主题拆新 plan。

## 五、验证矩阵

快速验证：

```bash
cd control-plane
.venv/bin/python -m pytest \
  tests/integration/test_api_tasks.py::test_upload_task_can_publish_object_source_reference \
  tests/integration/test_staging_cleanup.py \
  tests/unit/test_cleanup_staging_job.py \
  tests/integration/test_delivery.py::test_apply_delivery_result_is_stable_for_duplicate_result

cd ../data-plane
GOCACHE=/tmp/smh_go_cache go test ./...
```

Docker 验证：

```bash
cd deploy
docker compose up -d kafka minio minio-init

cd ../control-plane
RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest \
  tests/integration/test_phase2_bridge.py::test_source_reference_kafka_bridge_round_trip
```

## 六、风险与控制

- Kafka topic 重跑读到旧消息：测试使用唯一 topic，生产依赖 consumer group 和 offset 管理。
- object source 重复下载：已补进程内 archive cache；大文件场景后续评估本地临时文件 cache / streaming。
- staging object 泄露：已补 event metadata、cleanup service、手动 GC job 和 publish 失败补偿；后续接 cron / K8s CronJob。
- at-least-once 重复执行：已补 duplicate result apply 和重复 source-reference task e2e；Kafka result commit 改为按已 apply record 单条提交 offset。
- plan 文件膨胀：3.x 后续若单线任务超过 8-10 个子项，应再按主题拆新 plan。
