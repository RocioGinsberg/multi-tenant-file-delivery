# Plans

每个 Phase 一份派工计划文档，记录：
- 先决决策（每个 Phase 启动前必须拍）
- 子任务清单（带 L 等级、推荐执行方、依赖关系）
- 派工方案概览
- 预算档位与默认模型/执行方
- 升档触发条件（什么时候从低成本 worker 切到高档主 Agent / 高档模型）
- 可改文件上限（单次派工默认 1-3 个文件，超过 5 个文件先拆任务）
- 进度跟踪

执行过程中状态由主编排 Agent 用 `[x]` / `[~]` / `[ ]` 标记。

## 低成本编排字段

每份 Phase 文档应显式写清：

- **默认预算档位**：L1 优先 `aider+DeepSeek`；L2 优先 Codex `gpt-5.3-codex` + `medium` / `gpt-5.4-mini` + `medium`；L3 由主编排 Agent 亲自掌握。
- **主编排 Agent 职责**：默认只做规划、派工、review diff、跑测试、失败诊断和 commit draft；直接实现必须说明原因。
- **升档触发**：安全、接口签名、状态机、并发/race、跨模块协议、worker 连续失败、或用户显式要求。
- **报告要求**：每个子任务完成时记录实际执行方、模型/档位（如可见）、改动文件、测试结果、是否偏离计划。

## 索引

- [Phase 1 — Python 单体 MVP](./phase-1-python-mvp.md) ← 完成
- [Phase 2 — Go 数据面](./phase-2-go-data-plane.md) ← 完成
- [Phase 3 — MySQL 数据层与 source reference 迁移](./phase-3-data-layer-and-source-ref.md) ← 完成
- [Phase 3.x — Source reference 生产化与 worker 集群前置条件](./phase-3x-production-hardening.md) ← 完成
- [Phase 4 — Redis 能力层](./phase-4-redis-capabilities.md) ← 完成
- [Phase 5 — 可观测三件套](./phase-5-observability.md) ← 完成
- [Phase 6 — 多租户 + 鉴权](./phase-6-multitenancy-auth.md) ← 完成
- [Phase 6.5 — Workspace + 子公司读视图](./phase-6.5-workspace-read-view.md) ← 完成
- Phase 7 — 扩 Sink + 压测（待写）
- Phase 8（可选）— HA 改造（待写）

## 跨 Phase 决策记录

如果某个 Phase 启动时改变了未来 Phase 的设计（比如 Phase 1 提前做了 Phase 6.5 的某件事），应在受影响 Phase 的计划文档里加一条 "**Phase X 已提前完成**"。

## 提交要求

完成 Phase 子任务的 commit 应按根目录 `AGENTS.md` 的项目执行规则组织，并使用 [COMMIT_TEMPLATE](../COMMIT_TEMPLATE.md) 作为提交正文参考。
