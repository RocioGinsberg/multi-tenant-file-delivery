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
```

## 当前阶段

**Phase 0（完成）**：清理半成品脚手架，建立 monorepo 骨架。

**Phase 1（完成）**：Python 控制面 MVP，包含文件夹上传、分类、确认、Python 直传、repo/API/e2e 测试。

**Phase 2（完成）**：Go 数据面、file-spool / Kafka transport、S3 / MinIO 单段 PUT、结果回写、跨语言集成验证已完成。

**Phase 3（完成）**：MySQL 数据层已接入本地 compose；source reference 基础链路已打通，control-plane 可把上传文件夹生成的内部 archive 暂存到 MinIO staging bucket，Go worker 可用 `-source-mode object` 从 staged archive 读取源文件。

**Phase 3.x（完成）**：Kafka source-reference e2e、archive cache、GC、幂等、benchmark、配置 profile、worker startup check、最小 DLQ 和 review hardening 已落地。

**Phase 4（完成）**：Redis 能力层已闭合。Redis compose / health smoke、跨实例 progress pub/sub、短 TTL idempotency guard、result apply lease、data-plane Redis fixed-window limiter 和 Kafka/object source Phase 4 smoke 已落地；Redis 不替代 Kafka 的 durable task/result transport。

**Phase 5（完成）**：可观测三件套已闭合。control-plane / data-plane 都支持 Prometheus metrics；delivery task payload 注入 W3C `traceparent`；Go worker 能恢复 trace context；本地 compose 提供 OTel Collector、Prometheus、Grafana 和 Phase 5 smoke。

**Phase 6（当前）**：多租户 + 鉴权。目标是补齐 dev header / 默认 actor、tenant / app_user、task owner tenant/user、repo tenant filter 和最小 task_event actor attribution，让 HQ 与子公司用户隔离成为后续 workspace 读视图的前置条件。

详细阶段计划见 [docs/ROADMAP.md](./docs/ROADMAP.md) 和 [docs/plans/](./docs/plans/)。

## 回滚

回到 Phase 0 之前的状态：`git checkout pre-phase-0`。
