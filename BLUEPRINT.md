# BLUEPRINT

`BLUEPRINT.md` 曾经是项目的总蓝图。现在项目文档已拆分到 `docs/`，后续不再在这里维护长篇设计。

## 新文档入口

| 文档 | 用途 |
|---|---|
| [docs/PDR.md](./docs/PDR.md) | 产品背景、用户、范围、需求、非目标 |
| [docs/ROADMAP.md](./docs/ROADMAP.md) | 阶段路线图和开发进度 |
| [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) | 当前实现架构和写路径 |
| [docs/DATA_MODEL.md](./docs/DATA_MODEL.md) | 当前和目标态数据库模型 |
| [docs/TECHNICAL_NOTES.md](./docs/TECHNICAL_NOTES.md) | 技术亮点和开发自检 |
| [docs/RFC/](./docs/RFC/) | 技术方案评审 |
| [docs/ADR/](./docs/ADR/) | 已确定的架构决策 |
| [docs/plans/](./docs/plans/) | Phase 执行计划和验收记录 |

## 当前状态

- Phase 1：完成，Python 控制面 MVP。
- Phase 2：完成，Go 数据面、file-spool / Kafka transport、S3 / MinIO 单段 PUT、结果回写。
- 下一阶段：Phase 3，真实数据层和全栈部署收口。

后续请以 `docs/ROADMAP.md` 作为开发进度入口。
