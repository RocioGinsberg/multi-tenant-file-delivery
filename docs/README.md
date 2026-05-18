# Docs

项目文档按用途分层维护，避免把产品范围、架构决策和阶段进度都塞进一个 `BLUEPRINT.md`。

## 文档索引

| 文档 | 用途 |
|---|---|
| [PDR](./PDR.md) | Product Design Requirements：产品背景、用户、范围、需求、非目标 |
| [ROADMAP](./ROADMAP.md) | 阶段路线图、当前状态、后续优先级 |
| [ARCHITECTURE](./ARCHITECTURE.md) | 当前实现架构与写路径细节 |
| [DATA_MODEL](./DATA_MODEL.md) | 当前和目标态数据库模型、约束、迁移关注点 |
| [TECHNICAL_NOTES](./TECHNICAL_NOTES.md) | 技术亮点、工程解释点、开发自检清单 |
| [COMMIT_TEMPLATE](./COMMIT_TEMPLATE.md) | 提交说明模板，要求记录改动内容和测试 |
| [RFC](./RFC/) | 重要技术方案评审，记录问题、方案、取舍、状态 |
| [ADR](./ADR/) | 已确定的架构决策记录 |
| [plans](./plans/) | 每个 Phase 的执行计划和验收记录 |
| [SINK_PROTOCOL](./SINK_PROTOCOL.md) | Sink 接口和各存储协议适配说明 |
| [BENCHMARKS](./BENCHMARKS.md) | 压测目标和结果 |

## 使用规则

- 产品范围变更：先改 `PDR.md`。
- 架构方案变更：先新增/更新 `docs/RFC/*.md`，方案稳定后沉淀到 `ADR`。
- 阶段排期和完成状态：更新 `ROADMAP.md` 和对应 `docs/plans/phase-*.md`。
- 当前实现说明：更新 `ARCHITECTURE.md` / 模块 README。
- 数据模型变更：先更新 `DATA_MODEL.md`，落地后补 migration。
