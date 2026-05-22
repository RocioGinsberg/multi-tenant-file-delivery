# Architecture

> 当前实现架构说明。产品范围见 [PDR](./PDR.md)，阶段进度见 [ROADMAP](./ROADMAP.md)，方案评审见 [RFC](./RFC/)。

## 目录
- [写路径详细时序图](#写路径详细时序图)
- [读路径详细时序图](#读路径详细时序图) — Phase 6.5 后写
- [关键不变量与一致性边界](#关键不变量与一致性边界) — Phase 6.5 后写
- [失败模式与恢复策略](#失败模式与恢复策略) — Phase 4 后写

## 当前状态
**v1**：Phase 2 已完成。Go 数据面通过统一 transport 接口支持 file-spool 和 Kafka；本地默认 file-spool，Kafka adapter 已通过 Docker broker 集成测试。当前 sink 支持 mock 与 S3 / MinIO 单段 PUT，结果事件可回写控制面 task / item 状态。

**Phase 3 / 3.x Done**：MySQL 已作为主数据库目标接入本地 compose；source reference 基础链路、Kafka source-reference e2e、GC、幂等、readiness、最小 DLQ 和 review hardening 已完成。HQ 选择文件夹后，control-plane 生成内部 archive 并可暂存到 MinIO / S3 staging bucket，Go worker 可通过 `-source-mode object` 从 staged archive 读取 item bytes。

**Phase 4 Done**：Redis 能力层已完成 compose / 配置基线、control-plane Redis client / health smoke、`ProgressBus` memory / Redis backend 抽象、短 TTL idempotency guard、result apply lease，以及 data-plane Redis fixed-window limiter。`PROGRESS_BACKEND=redis` 时 SSE progress 可通过 Redis pub/sub 跨 control-plane 实例 fanout；`REDIS_IDEMPOTENCY_ENABLED=true` 时 create/upload trigger 会用 Redis claim 挡住正在处理的重复请求；`REDIS_LEASE_ENABLED=true` 时 result consumer 在 apply 前竞争 task 级 lease；`-redis-limiter-enabled` 时 Go worker 在 sink 上传前竞争全局配额。Redis 不替代 Kafka。

**Phase 5 Done**：本地可观测三件套已接入。`deploy/docker-compose.yml` 提供 OTel Collector、Prometheus 和 Grafana；control-plane `/metrics` 暴露 HTTP / task / delivery RED 指标，data-plane `-metrics-enabled` 暴露 task consume、source read、sink upload、result publish 和 limiter RED 指标。control-plane 在 delivery task payload 和 Kafka header 写入 W3C `traceparent`，Go worker 从 payload 恢复 remote parent，并为 task process、source resolve、sink upload、result publish 继续创建 spans。Phase 5 smoke 覆盖 control-plane -> Kafka -> data-plane -> result apply，并验证 collector 日志里的同 trace ID。

**Phase 6 Current**：多租户 + 鉴权进入当前阶段。Phase 6 会先落地 tenant / user / role 基线、request actor context、仓储层 tenant filter、HQ / 子公司权限边界和 audit 入口，为 Phase 6.5 workspace 读视图提供身份与隔离前提。

## 写路径详细时序图

当前 Phase 2 写路径：

```text
HQ user
  │
  ▼
control-plane FastAPI
  │  POST /tasks/{id}/upload
  │  OBSERVABILITY_ENABLED=true 时创建 HTTP / delivery spans
  │  METRICS_ENABLED=true 时记录 HTTP / task / delivery RED 指标
  │
  ├─ delivery_backend=python
  │    └─ 保留 Phase 1 Python 直传路径
  │
  └─ delivery_backend=go-worker
       │
       ├─ 查询 task / task_item
       ├─ 过滤 upload_status=pending 且 severity=ok/warning 的 item
       ├─ 构建 DeliveryTaskMessage
       ├─ 注入 W3C traceparent
       ├─ delivery_source_mode=file:
       │    └─ 消息携带 temp_dir + src_path（本地兼容路径）
       ├─ delivery_source_mode=object:
       │    ├─ uploaded folder -> internal archive -> staging object storage
       │    └─ 消息携带 source reference + source_path
       ├─ delivery_transport=file:
       │    └─ 写入 /tmp/auto_upload_outbox/delivery.tasks.v1/{task_id}.json
       ├─ delivery_transport=kafka:
       │    └─ publish delivery.tasks.v1 + traceparent header
       └─ task.status -> queued

Go data-plane worker
  │
  ├─ file-spool / Kafka transport 读取 delivery.tasks.v1
  ├─ transport decode -> DeliveryTask
  ├─ 从 DeliveryTask.traceparent 恢复 trace context
  ├─ 记录 consume / source / sink / result RED 指标
  ├─ pipeline.ProcessTask
  │    ├─ source-mode=file: FileSource(temp_dir, src_path)
  │    ├─ source-mode=object: S3 staged archive -> source_path
  │    └─ Sink.Upload（mock 或 S3/MinIO 单段 PUT）
  └─ file-spool / Kafka transport 写入 delivery.results.v1

control-plane result consumer
  │
  ├─ 读取 delivery.results.v1
  ├─ REDIS_LEASE_ENABLED=true: 竞争 delivery_result_apply:{task_id} lease
  ├─ apply_delivery_result()
  └─ 回写 task / task_item 状态
```

后续目标：
- 为 S3 / MinIO sink 补 multipart、resume 和平台层 dedup。
- 在真实负载下补 worker 并发调度、backpressure 和重试策略。
