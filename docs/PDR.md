# PDR — 多租户文件分发与观测平台

> PDR = Product Design Requirements。本文描述“要解决什么业务问题、服务谁、边界在哪里”。技术方案细节放到 `ARCHITECTURE.md` / `RFC` / `ADR`。

## 1. 背景

总部 HQ 需要把业务文件批量分发给数十家子公司。原始场景来自腾讯企业网盘 SMH 的自动化上传实践：手工操作成本高、批量投递弱、缺少统一进度和审计。

项目将这类场景抽象成一个通用平台：

- HQ 上传和分类文件。
- 平台按规则识别投递目标和文档类型。
- 数据面把文件投递到异构 sink，首版为 S3 / MinIO。
- 子公司只能看到自己 workspace 中的文件。
- 平台提供进度、审计、重试和后续 dedup 能力。

## 2. 用户与角色

| 角色 | 目标 |
|---|---|
| HQ admin | 管理子公司、workspace、sink 凭证、分类规则 |
| HQ uploader | 提交文件分发任务、确认分类、观察进度、重试失败项 |
| Subsidiary viewer | 浏览和下载属于本公司的文件 |
| Subsidiary admin | 管理本公司用户和通知配置 |

## 3. 核心场景

### 写路径

1. HQ 选择文件夹上传，多文件路径保持相对目录结构。
2. 控制面运行 classification profile。
3. HQ 预览分类结果并确认。
4. 控制面发布 `delivery.tasks.v1`。
5. Go 数据面消费任务并上传到 sink。
6. 数据面发布 `delivery.results.v1`。
7. 控制面回写 task / task_item 状态。

### 读路径

1. 子公司用户登录。
2. 只列出 `target_tenant_id == user.tenant_id` 的 workspace。
3. 查询 DB 元数据展示文件列表。
4. 下载时由控制面鉴权并签发短 TTL 临时凭证或 presigned URL。
5. 审计读操作。

### Worker 集群业务场景

后续 data-plane 需要支持 worker 集群，不是为了增加架构复杂度，而是为了覆盖真实批量分发压力：

- 月末 / 季末 HQ 集中上传大量报表，一个任务可能包含数千到数万份文件，需要多个 worker 分摊上传。
- 财务、人事、法务、运营等部门可能同时发起分发任务，控制面应保持响应，上传执行由数据面异步消化。
- 部分 sink 可能很慢，例如跨区域对象存储、SFTP、Webhook 或限流的第三方接口，需要避免慢任务阻塞整个队列。
- 不同租户可能使用不同 sink、region 或凭证，后续可按 sink 类型、tenant、region 拆分 worker pool。
- worker 实例宕机或重启时，未完成任务应能由其他 worker 接管，前提是源文件不依赖某台机器的本地临时目录。

产品层面的目标是：control-plane 继续负责业务状态和用户体验，data-plane worker 集群负责可恢复、可扩容的大文件和批量文件投递。

## 4. 产品范围

### MVP 范围

- HQ 文件夹上传。
- 分类预览、确认、上传。
- S3 / MinIO 作为首个真实 sink。
- Go 数据面接管上传执行。
- file-spool 本地桥接和 Kafka transport。
- task / item 状态回写。
- 基础前端和 SSE 进度。
- 本地 Docker 依赖：Kafka / MinIO。

### 后续范围

- 多租户鉴权和子公司只读视图。
- Workspace / workspace_object 元数据模型。
- 可横向扩展的 data-plane worker 集群。
- 去除 data-plane 对 control-plane 本地临时目录的运行时依赖。
- 源文件暂存到 durable object storage，再由 worker 按 source reference 拉取。
- 平台层 dedup。
- S3 multipart / resume。
- Redis 进度广播、限流和幂等。
- OTel / Prometheus / Grafana 可观测。
- OSS / SFTP / Webhook 等新 sink。

## 5. 非目标

- 不做通用网盘协作功能：评论、共编、IM、知识库。
- 不依赖具体 sink 的权限系统做平台授权。
- 不在首版做 OIDC / SAML / SSO federation。
- 不在 demo 阶段引入 Kubernetes / Service Mesh。
- 不把所有服务改成同一种语言；控制面 Python、数据面 Go 是刻意设计。

## 6. 成功标准

- HQ 可完成上传 -> 分类 -> 确认 -> 数据面上传 -> 状态回写闭环。
- 数据面支持 file-spool 和 Kafka 两种 transport。
- 本地可通过 Docker 启动 Kafka / MinIO 进行集成验证。
- 关键设计有 RFC / ADR 可追溯。
- 每个 Phase 有明确完成定义和测试记录。

## 7. 下一阶段草案：Redis 能力层

Phase 3 / 3.x 已完成 MySQL、source reference、Kafka source-reference e2e、GC、幂等和最小 DLQ。下一阶段不替代 Kafka，而是在 Kafka durable transport 旁边补 Redis 共享能力层。

下一阶段目标：

- 进度事件从单进程内存 bus 演进到 Redis pub/sub，可支持多个 control-plane 实例。
- 任务创建和上传触发增加短 TTL 幂等保护，减少重复点击或并发请求造成的重复副作用。
- 关键临界区引入 Redis lease，给后续多 consumer / 多调度器留出安全边界。
- worker 或发布入口引入 Redis limiter，先提供最小跨实例 backpressure 能力。
- Redis Docker tests 保持 opt-in，默认开发测试仍可不启动 Redis。
