# 多租户文件分发与观测平台

总部 HQ 给数十家子公司分发业务文件、子公司在自己权限内观测/下载属于自己数据的平台。

## 起源

工作中要把总部文件批量分发给数十家子公司。公司用腾讯企业网盘（SMH），订阅成本高、自动化能力弱。我抓包逆向了它的 RESTful API，写了个自动化批量上传 CLI（即 `control-plane/_legacy/smh_uploader/`）替代手工操作，期间踩过三段 hash 协商、大文件 multipart、并发限速、断点续传等坑。

离职后我意识到这套**"流式上传 + 内容寻址 dedup + 异构 sink 适配"**的模式不是 SMH 独有的——任何"总部 → 多子公司分发"场景都需要。于是把它做成一个通用对象存储为后端的多租户文件分发平台：

- **首版**：S3/MinIO，本地一行 docker 即可起
- **未来**：OSS / SFTP / Webhook / COS 等都是 Sink interface 的一种实现

详细决策见 [ADR 0010](./docs/ADR/0010-pivot-to-generic-object-storage.md)。

## 项目导览

> **文档总索引**：[docs/README.md](./docs/README.md)
> **产品需求（PDR）**：[docs/PDR.md](./docs/PDR.md)
> **路线图**：[docs/ROADMAP.md](./docs/ROADMAP.md)
> **架构详解**：[docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)
> **数据模型**：[docs/DATA_MODEL.md](./docs/DATA_MODEL.md)
> **技术方案 RFC**：[docs/RFC/](./docs/RFC/)
> **架构决策记录**：[docs/ADR/](./docs/ADR/)
> **协作规范**：[DISPATCH.md](./DISPATCH.md)；[AGENTS.md](./AGENTS.md)

## Monorepo 布局

```
.
├── control-plane/         Python FastAPI 控制面（业务逻辑、规则引擎、读路径）
│   └── _legacy/           v0 历史代码（CLI + cosdrive 半成品），项目起源故事的实物证据
├── data-plane/            Go 数据面（高并发流式投递、sink 适配）
├── web/                   前端（HQ 上传台 + 子公司观测窗口）
├── deploy/                docker-compose、Grafana、Prometheus、OTel 配置
├── proto/                 跨语言消息/接口定义（备用，主用 Kafka JSON）
├── docs/                  PDR / RFC / ADR / Roadmap / Data Model / Plans
└── BLUEPRINT.md           兼容入口，指向 docs/
```

## 当前阶段

**Phase 0（完成）**：清理半成品脚手架，建立 monorepo 骨架。

**Phase 1（完成）**：Python 控制面 MVP，包含任务创建、分类、确认、Python 直传、repo/API/e2e 测试。

**Phase 2（完成）**：Go 数据面、file-spool / Kafka transport、S3 / MinIO 单段 PUT、结果回写、跨语言集成验证已完成。

**Phase 3（进行中）**：MySQL 数据层已接入本地 compose；source reference 迁移已开始，control-plane 可把原始 zip 暂存到 MinIO staging bucket，Go worker 可用 `-source-mode object` 从 staged archive 读取源文件。

详细阶段计划见 [docs/ROADMAP.md](./docs/ROADMAP.md) 和 [docs/plans/](./docs/plans/)。

## 回滚

回到 Phase 0 之前的状态：`git checkout pre-phase-0`。
