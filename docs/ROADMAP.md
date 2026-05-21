# Roadmap

> Roadmap 记录阶段目标和完成状态；具体执行清单在 `docs/plans/`。

## 状态图例

| 状态 | 含义 |
|---|---|
| Done | 已完成并合并 |
| Current | 当前阶段 |
| Planned | 已规划，未开始 |
| Deferred | 从前序阶段拆出，等待独立实现 |

## 阶段路线

| Phase | 状态 | 目标 | 完成定义 |
|---|---|---|---|
| Phase 0 | Done | 清理半成品脚手架，建立 monorepo | 目录骨架和基础文档完成 |
| Phase 1 | Done | Python 控制面 MVP | 选择文件夹 -> 分类 -> 确认 -> Python 上传 -> 前端进度闭环 |
| Phase 2 | Done | Go 数据面 + Kafka bridge | Go worker 接管上传执行；file-spool 和 Kafka transport 都可验证 |
| Phase 3 | Done | MySQL 数据层 | SQLite -> MySQL 主数据库；本地全栈 compose 跑通 |
| Phase 3.x | Done | 去本地文件依赖 + hardening | source reference 消息模型；Kafka worker 从 staging object storage 读取源文件；Kafka/GC/幂等/配置/readiness/DLQ 基础闭合 |
| Phase 4 | Current | Redis 能力层 | pub/sub 进度、限流、幂等和分布式锁 |
| Phase 5 | Planned | 可观测 | Python -> Kafka -> Go -> sink 的 trace 和 RED 指标 |
| Phase 6 | Planned | 多租户 + 鉴权 | HQ / 子公司用户隔离，RBAC 覆盖 |
| Phase 6.5 | Planned | Workspace + 子公司读视图 | 子公司登录后可浏览和下载自己的 workspace 文件 |
| Phase 7 | Planned | 扩 sink + 压测 | OSS / Webhook / mock 异常 sink，BENCHMARKS 写入实测数据 |
| Phase 8 | Optional | HA 改造 | 多实例和 rolling restart 不丢任务 |

## Phase 2 已完成能力

- Go data-plane worker 主干。
- task/result JSON message contract。
- file source。
- mock sink。
- S3 / MinIO 单段 PUT sink。
- SHA-256 upload receipt。
- item 级 result event。
- control-plane result consumer 回写 task / item 状态。
- file-spool transport。
- Kafka transport adapter。
- control-plane Kafka producer / consumer adapter。
- Docker Compose Kafka / MinIO 本地依赖。
- control-plane 测试按 `unit / integration / e2e` 分层。

## Deferred Work

以下能力不再算 Phase 2 未完成项，后续单独立项：

| 能力 | 建议阶段 | 原因 |
|---|---|---|
| S3 multipart / resume | Phase 7 或独立 Phase 3.x | 需要 session 表、part 状态和恢复语义 |
| 平台层 dedup | Phase 6.5 / Phase 7 | 依赖 physical_object / workspace_object 模型 |
| 去除 data-plane 本地文件依赖 | Phase 3.x | worker 集群化前需要 source reference 和 staging object storage |
| worker 并发调度 / backpressure | Phase 4 / Phase 7 | 依赖 Redis 限流和压测数据 |
| trace context 透传 | Phase 5 | 依赖 OTel 统一接入 |

## 当前下一步建议

Phase 4 当前状态：

- Redis 不替代 Kafka；Kafka 继续承载 durable task/result transport。
- Redis compose / 配置基线、control-plane Redis client / health smoke、`ProgressBus` Redis pub/sub backend、短 TTL idempotency guard 已落地。
- 下一步优先实现 lease，然后继续限流。
- 默认测试继续走 memory/fake；Redis Docker tests 使用 `RUN_DOCKER_TESTS=1` opt-in。
