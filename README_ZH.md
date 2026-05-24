# 多租户文件分发平台

[English](README.md) / 中文

[文档](docs/) · [路线图](docs/ROADMAP.md) · [架构](docs/ARCHITECTURE.md) · [变更日志](CHANGELOG.md)

***

## 概览

**谁该读这份文档？**

- **总部运营人员**：需要定期向外部合作方分发运营数据（订单考核、绩效报表、财务报表），但不能给他们开通内网权限。
- **外部合作方、外包商、子公司**：需要一个干净的、只包含自己数据的 workspace，用于浏览和下载文件。
- **平台工程师**：在评估一个多组件分布式系统的设计决策和工程纪律。

**场景。** 外包服务商、加盟商、合作企业通常不能接入总部内网。共享文件夹、邮件附件、临时脚本是默认工具——但它们不提供分类、投递确认、各方数据隔离或运行证据。

一份发错的报表——绩效评分误投给错误的外包商、订单汇总淹没在聊天记录里——可能触发合同纠纷、合规审查和数小时的人工核对。

这个仓库提供了一个面向上述跨边界场景的**多租户文件分发平台**的公开参考实现：

- HQ 直接选择文件夹，相对路径完整保留。
- 分类引擎按业务 profile 将每个文件匹配到正确的接收方和文档类型。
- HQ 在投递前预览并确认分发计划。
- Go 数据面异步将文件投递到 S3 兼容的对象存储。
- 每个外部合作方登录自己的 workspace，只看到属于自己的文件——不多不少。

### 产品缺口

现有工具各自解决了相邻问题，但这条业务链路始终是空的：

| 工具 | 能做什么 | **不能**做什么 |
|---|---|---|
| 共享文件夹 / 内网门户 | 内部文件共享 | 外部合作方没有账号，无法访问 |
| 邮件附件 | 一次性发送 | 无分类、无审计、无法隔离数十个接收方的数据 |
| 对象存储控制台（S3/MinIO） | 存储和提供字节 | 无业务工作流——无文件夹接收入口、无分类、无按接收方的 workspace |
| MFT / ETL 管道 | 定时批量搬运 | 面向系统间集成，不是面向操作员的文件夹选择、预览和确认 |
| 聊天附件 | 快速传递 | 面对定期批量分发脆弱不堪；无送达证据和重试语义 |

这些工具能把文件搬走，但不拥有业务流程。这个仓库填补它们之间的空白：一个面向操作员的产品，端到端地拥有文件夹接收、分类、分发计划复核、可恢复投递、按接收方的 workspace 和运行证据。

***

## Demo

![HQ upload to workspace demo](docs/media/demo.gif)

GIF 展示 HQ 选择文件夹上传、预览分类计划，以及子公司查看 workspace。

UI 变更后重新生成：

```bash
./examples/demo.sh
```

脚本创建样例文件夹，用 headless Chrome 渲染 HQ 上传台和子公司 workspace 视图，输出 `docs/media/demo.gif`。

***

## 平台能力

### 面向 HQ 操作员

- **文件夹上传台**。浏览器选择文件夹，文件逐项提交——无需打包 zip，也无需把批次抽象成集成 payload。
- **分类预览**。每个文件按业务 profile 匹配到目标合作方、文档类型和投递路径。阻断错误和警告在投递前可见。
- **投递前确认**。完整分发计划——哪些文件到哪些合作方——经人工复核后一次确认。
- **实时进度**。SSE 推送任务和文件级别的进度到浏览器。
- **失败重试**。单个文件或整个任务可重试，无需重新上传。

### 面向外部合作方

- **隔离 workspace**。每个外部合作方登录后只看到 `target_tenant == 自身 ID` 的文件。看不到其他合作方的数据。越权访问统一返回 404——不泄露文件存在性。
- **浏览和下载**。workspace 列表、文件列表、文件详情和短 TTL 的 presigned 下载 URL。无需开 bucket 权限，无需配置存储后端 ACL。

### 面向平台运维

- **可观测**。Prometheus RED 指标覆盖 HTTP、任务生命周期、投递发布、源文件读取、sink 上传、回写和限流。W3C `traceparent` 从 Python 发布经 Kafka 传递到 Go worker 各 span。
- **投递证据**。task_event 记录每次状态变更并标注操作者。trace context 将上传触发、worker 执行和 result apply 串联为一次分布式追踪。
- **本地依赖栈**。Docker Compose 提供 MySQL、Kafka、MinIO、Redis、OTel Collector、Prometheus 和 Grafana——运行和观测完整链路所需的一切。

***

## 功能矩阵

| 能力 | 当前状态 |
|---|---|
| 文件夹上传 | 浏览器文件夹选择器；不暴露 zip 上传 API |
| 分类 | Profile 驱动 target / document type 匹配，支持预览、警告、阻断错误和持久化 task item |
| 控制面/数据面桥接 | `delivery.tasks.v1` / `delivery.results.v1`，支持 file-spool 和 Kafka |
| 源文件暂存 | 控制面构建内部 archive 并暂存到 MinIO/S3；Go worker 按 object source reference 读取 |
| Sink | mock 和 S3/MinIO 单段 PUT。multipart/resume 和更多 sink 在 Phase 7+ |
| Redis | progress pub/sub、短 TTL 幂等、result apply lease、fixed-window 限流 |
| 可观测 | Prometheus 指标；W3C `traceparent` 从 Python publish 到 Go 处理、上传、结果发布 |
| 多租户 | dev actor header、tenant/user ownership、repo tenant filter、基于 role 的 workspace scope |
| 合作方读视图 | workspace 列表、对象列表、对象详情、短 TTL presigned download URL、静态前端 |

***

## 快速启动

### 1. 启动本地依赖

```bash
cd deploy
docker compose up -d mysql kafka minio minio-init redis otel-collector prometheus grafana
```

### 2. 准备控制面

```bash
cd ../control-plane
uv sync --dev
cp .env.example .env
.venv/bin/python -m alembic upgrade head
```

### 3. 启动 API

```bash
METRICS_ENABLED=true OBSERVABILITY_ENABLED=true \
  .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 4. 启动 worker

```bash
cd ../data-plane
GOTOOLCHAIN=auto GOCACHE=/tmp/smh_go_cache go run ./cmd/worker \
  -transport file \
  -source-mode file \
  -sink mock \
  -metrics-enabled \
  -startup-check=false
```

### 5. 打开 UI

静态提供 `web/public`，同时让 `web/css` 和 `web/js` 可访问，将 `/api/v1` 反向代理到 control-plane：

- HQ 上传台：`web/public/index.html`
- 合作方 workspace 视图：`web/public/workspaces.html`

组件细节见 [control-plane/README.md](control-plane/README.md)、[data-plane/README.md](data-plane/README.md)、[web/README.md](web/README.md)、[deploy/README.md](deploy/README.md)。

***

## 架构

```mermaid
flowchart LR
    hq[HQ upload desk] --> cp[FastAPI control plane]
    cp --> db[(MySQL / SQLite)]
    cp --> redis[(Redis)]
    cp -- source archive --> staging[(MinIO / S3 staging)]
    cp -- delivery.tasks.v1 --> transport[(file-spool / Kafka)]
    transport --> worker[Go data-plane worker]
    worker --> staging
    worker --> sink[(mock / S3 / MinIO sink)]
    worker -- delivery.results.v1 --> transport
    transport --> cp
    cp --> workspace[(workspace read model)]
    sub[Partner workspace view] --> cp
    cp -- presigned URL --> sub
```

**一句话**：Python 控制面负责业务真相、租户边界、任务状态和读路径授权；Go 数据面负责字节移动、source/sink 协议适配和 worker 侧执行观测。

设计不变量、失败模式和写/读路径细节：[ARCHITECTURE.md](docs/ARCHITECTURE.md)。

***

## 贡献者入口

### 开发检查

```bash
cd control-plane
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest

cd ../data-plane
GOTOOLCHAIN=auto GOCACHE=/tmp/smh_go_cache go test ./...
```

Docker opt-in 测试使用 `RUN_DOCKER_TESTS=1`；MySQL 测试使用 `RUN_MYSQL_TESTS=1`。

完整指南：[CONTRIBUTING.md](docs/CONTRIBUTING.md)。

### 仓库布局

```text
.
├── control-plane/   Python FastAPI 控制面
├── data-plane/      Go worker、transport、source、sink、limiter、metrics、tracing
├── web/             HQ 上传台和合作方 workspace 静态前端
├── deploy/          Docker Compose、Prometheus、Grafana、OTel Collector
├── profiles/        分类 profiles
├── docs/            PRD、架构、路线图、RFC、ADR、phase plans
├── examples/        Demo 辅助脚本
└── proto/           预留跨语言 contract 区域
```

***

## 文档

| 文档 | 用途 |
|---|---|
| [docs/PRD.md](docs/PRD.md) | 产品范围、用户、非目标、成功标准 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 当前架构、写/读路径、不变量和失败模式 |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | 数据实体、约束和迁移说明 |
| [docs/ROADMAP.md](docs/ROADMAP.md) | release/tag 策略、阶段状态、下一阶段和 deferred work |
| [docs/RFC/](docs/RFC/) | 重要技术方案评审 |
| [docs/ADR/](docs/ADR/) | 已接受的架构决策 |
| [docs/plans/](docs/plans/) | Phase 执行计划和验收记录 |
| [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) | 贡献、文档、PR 和验证规则 |

***

## 状态

Phase 0–6.5 已在 `main` 完成：最小端到端平台可用并通过本地 smoke。下一阶段 Phase 7：扩展 sink 和压测。

详见 [CHANGELOG.md](CHANGELOG.md) 和 [docs/ROADMAP.md](docs/ROADMAP.md)。

***

## License

本项目使用 [MIT License](LICENSE)。
