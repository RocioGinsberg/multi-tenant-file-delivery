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

1. HQ 上传 zip / 多文件。
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

## 4. 产品范围

### MVP 范围

- HQ 上传 zip。
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
