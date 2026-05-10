# Plans

每个 Phase 一份派工计划文档，记录：
- 4 个先决决策（每个 Phase 启动前必须拍）
- 子任务清单（带 L 等级、推荐执行方、依赖关系）
- 派工方案概览
- 进度跟踪

执行过程中状态由主对话用 `[x]` / `[~]` / `[ ]` 标记。

## 索引

- [Phase 1 — Python 单体 MVP](./phase-1-python-mvp.md) ← 当前
- Phase 2 — 拆 Go 数据面（待写）
- Phase 3 — 换上"真"数据层（待写）
- Phase 4 — Redis 一物多用（待写）
- Phase 5 — 可观测三件套（待写）
- Phase 6 — 多租户 + 鉴权（待写）
- Phase 6.5 — Workspace + 子公司读视图（待写）
- Phase 7 — 扩 Sink + 压测（待写）
- Phase 8（可选）— HA 改造（待写）

## 跨 Phase 决策记录

如果某个 Phase 启动时改变了未来 Phase 的设计（比如 Phase 1 提前做了 Phase 6.5 的某件事），应在受影响 Phase 的计划文档里加一条 "**Phase X 已提前完成**"。
