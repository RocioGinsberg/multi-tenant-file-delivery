# Roadmap

> Roadmap 记录阶段目标和完成状态；具体执行清单在 `docs/plans/`。

## 状态图例

| 状态 | 含义 |
|---|---|
| Done | 已完成并合并 |
| Current | 当前阶段 |
| Planned | 已规划，未开始 |
| Deferred | 从前序阶段拆出，等待独立实现 |

## Release / Tag 策略

- 阶段 tag 应打在已合并到主干的 commit 上，而不是仍在开发中的 feature branch 上。
- 打 tag 前应满足：Phase plan 全部 `[x]`、README / ARCHITECTURE / ROADMAP / component README 同步、关键 smoke 通过、远端分支或 PR 状态可追溯。
- 推荐用 annotated tag，命名按阶段或预览版本二选一：`phase-5-observability-done` 适合阶段里程碑，`v0.5.0` 适合对外 release 语义。
- 如果需要在合并前留检查点，可用 `phase-5-observability-rc1` 这类 RC tag；稳定 tag 不应反复移动。

## 阶段路线

| Phase | 状态 | 目标 | 完成定义 |
|---|---|---|---|
| Phase 0 | Done | 清理半成品脚手架，建立 monorepo | 目录骨架和基础文档完成 |
| Phase 1 | Done | Python 控制面 MVP | 选择文件夹 -> 分类 -> 确认 -> Python 上传 -> 前端进度闭环 |
| Phase 2 | Done | Go 数据面 + Kafka bridge | Go worker 接管上传执行；file-spool 和 Kafka transport 都可验证 |
| Phase 3 | Done | MySQL 数据层 | SQLite -> MySQL 主数据库；本地全栈 compose 跑通 |
| Phase 3.x | Done | 去本地文件依赖 + hardening | source reference 消息模型；Kafka worker 从 staging object storage 读取源文件；Kafka/GC/幂等/配置/readiness/DLQ 基础闭合 |
| Phase 4 | Done | Redis 能力层 | pub/sub 进度、限流、幂等和分布式锁 |
| Phase 5 | Done | 可观测 | Python -> Kafka -> Go -> sink 的 trace 和 RED 指标；本地 dashboard / smoke |
| Phase 6 | Done | 多租户 + 鉴权 | 控制面基线已落地并合并：dev header / 默认 actor，tenant / app_user，repo tenant filter，最小 task_event actor attribution；tag-prep review 修复 result apply 任务绑定和 confirm 状态检查 |
| Phase 6.5 | Done | Workspace + 子公司读视图 | workspace / physical_object / workspace_object 元数据、result apply 读模型、子公司只读 API、download URL 和最小前端视图已落地 |
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
| sink credential 加密 | Phase 6.5 / Phase 7 | 依赖更细的 workspace / sink 授权边界 |
| 去除 data-plane 本地文件依赖 | Phase 3.x | worker 集群化前需要 source reference 和 staging object storage |
| worker 并发调度 / backpressure | Phase 4 / Phase 7 | 依赖 Redis 限流和压测数据 |
| result trace propagation | Phase 5.x / Phase 6 | data-plane result message 尚未携带 traceparent，result consume/apply 当前是独立 trace |

## Phase 5 已完成能力

- 本地 compose 提供 OTel Collector、Prometheus、Grafana。
- control-plane 暴露 `/metrics`，覆盖 HTTP、task workflow、delivery publish/result apply RED 指标。
- data-plane worker 支持 `-metrics-enabled` 和独立 `/metrics` endpoint，覆盖 consume、source read、sink upload、result publish、limiter acquire RED 指标。
- delivery task payload 注入 W3C `traceparent`，Kafka publisher 同步写 header。
- Go worker 从 payload 恢复 trace context，并为 task process、source resolve、sink upload、result publish 创建 spans。
- Docker opt-in smoke 覆盖 control-plane -> Kafka -> data-plane -> result apply，并验证 collector debug logs 中 publish 与 data-plane spans 共享同一 trace ID。

## 当前下一步建议

Phase 6.5 已落地，首次 tag 前建议：

- 补 README 最终 demo GIF，展示 HQ 上传 -> data-plane 投递 -> 子公司 workspace 查看 / 下载。
- 跑 tag 前 smoke：control-plane full pytest / ruff、data-plane go test、SQLite + MySQL migration、Docker observability 或最小全链路 smoke。
- tag 打在 Phase 6.5 合并到 `main` 后的 commit 上。

Phase 7 启动输入：

- Phase 6.5 已提供 workspace / physical_object / workspace_object 元数据，可作为平台层 dedup、refcount GC、sink credential 和管理后台的基线。
- 当前 data-plane 仍是 S3 / MinIO 单段 PUT；OSS / Webhook / 异常 sink 和压测进入 Phase 7。
- 完整 audit_log 仍未落表，当前下载 URL 签发审计暂存在来源 task_event。
