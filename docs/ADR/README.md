# Architecture Decision Records (ADR)

> 每一个**会影响整体架构方向**或**未来可能被质疑**的决策都应留一份 ADR。
>
> 每条 ADR 必须包含：
> 1. **背景**（Context）：为什么需要做这个决策
> 2. **决策**（Decision）：选了什么
> 3. **替代方案**（Alternatives）：考虑过的其他选项
> 4. **后果**（Consequences）：付出的代价、未来想反悔时要做什么

## 索引
- [0001 — 双语言架构（Python 控制面 + Go 数据面）](0001-dual-language.md)
- 0002 — Kafka vs RabbitMQ — 待写
- 0003 — Sink 不暴露分阶段 API — 待写
- 0004 — 多租户隔离策略（仓储层过滤 vs PG RLS） — 待写
- 0005 — Workspace 权威 vs 依赖 sink 自身权限 — 待写
- 0006 — Dedup 范围限定 (owner_tenant_id, sink_id) — 待写
- 0007 — 删除走 ref_count 异步 GC — 待写
- 0008 — 下载走 302 直连 sink — 待写
- 0009 — 元数据 DB 是真相，不查对端 — 待写
- [0010 — 项目起源叙事 & 首版后端选 S3/MinIO](0010-pivot-to-generic-object-storage.md)
