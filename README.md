# 多租户文件分发与观测平台

总部 HQ 给数十家子公司分发业务文件、子公司在自己权限内观测/下载属于自己数据的平台。

> **完整设计**：[BLUEPRINT.md](./BLUEPRINT.md) — 项目北极星，所有重大决策的来源
> **架构详解**：[docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)
> **架构决策记录**：[docs/ADR/](./docs/ADR/)
> **Sink 协议规范**：[docs/SINK_PROTOCOL.md](./docs/SINK_PROTOCOL.md)
> **性能基准**：[docs/BENCHMARKS.md](./docs/BENCHMARKS.md)

## Monorepo 布局

```
.
├── control-plane/         Python FastAPI 控制面（业务逻辑、规则引擎、读路径）
├── data-plane/            Go 数据面（高并发流式投递、sink 适配）
├── web/                   前端（HQ 上传台 + 子公司观测窗口）
├── deploy/                docker-compose、Grafana、Prometheus、OTel 配置
├── proto/                 跨语言消息/接口定义（备用，主用 Kafka JSON）
├── docs/                  架构文档与 ADR
└── BLUEPRINT.md           项目蓝图
```

## 当前阶段

**Phase 0（完成）**：清理半成品脚手架，建立 monorepo 骨架。

**下一步：Phase 1** —— Python 单体 MVP，FastAPI + SQLite + 进程内 worker，把 HQ 写路径 + SMH 上传跑通。

详细阶段计划见 [BLUEPRINT § 十](./BLUEPRINT.md#十实施阶段每阶段都有完成定义)。

## 历史代码

`control-plane/_legacy/`：v0 单人 CLI 脚本与 cosdrive 半成品的 SMH 协议客户端，供 Phase 1 移植参考。Phase 1 完成后会删。

回到 Phase 0 之前的状态：`git checkout pre-phase-0`。
