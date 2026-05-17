# 多租户文件投递平台 — 项目蓝图（v1.0 草案）

> 本文档作为后续学习/搭建的"北极星"。审计时请重点关注标记 ⚠️ **关键决策** 的段落——这些是会影响整体架构走向、不该轻易改的地方。

---

## 一、项目定位

### 起源故事

工作中需要把总部文件批量分发给数十家子公司。公司原方案是腾讯企业网盘（SMH），订阅成本高、自动化能力弱。我抓包逆向了它的 RESTful API，写了一个自动化批量上传 CLI（即 `_legacy/smh_uploader/`）替代手工操作，期间踩过：
- 三段 hash 协商上传（content-addressed dedup 协议）
- 大文件 multipart 分块
- 嵌套并发（团队级 × 文件级）+ 目录创建去重缓存
- 断点续传与失败重试

离职后回看这套模式——**"流式上传 + 内容寻址 dedup + 异构 sink 适配"**——并不是 SMH 独有的，任何"总部 → 多子公司分发"场景都需要。于是把它做成一个**通用对象存储为后端的多租户文件分发平台**。SMH 在这个项目里降级为"灵感来源 + 未来可扩展的一种 sink"，首版直接对接 S3/MinIO。

### 真实场景

**总部 HQ 给数十家子公司分发业务文件**（合规报送、政策模板、价格表、供应链文档……），每家子公司只能看到属于自己的那份；同一份文件常被同时分发给 N 家子公司。

### 产品形态

HQ → 子公司**文件分发与观测平台**，三块拼装：

| 路径 | 谁用 | 做什么 | 工程关键词 |
|---|---|---|---|
| **写路径（数据面）** | HQ uploader | 上传 → 规则分类 → 异构并发投递（首版 S3/MinIO，后续 OSS/SFTP/webhook） | 流式上传 / multipart / 断点续传 / AIMD 反压 / 平台层 dedup |
| **平台层（控制面）** | 系统编排 | Workspace 抽象作为权威真相；跨 sink 屏蔽差异；承担鉴权/审计/配额/dedup | 不对称多租户 / 内容寻址 / ref-count GC |
| **读路径（控制面）** | Subsidiary viewer | 受限视角下浏览/下载属于自己 workspace 的文件；绝不暴露 sink 凭证 | JWT-scoped authz / presigned URL / 异步审计 |

### 核心设计哲学

**Workspace 是平台层的权威真相，sink 只是它的实现方式**。即便对端有自己的权限/配额/dedup 系统（SMH 有 team space + 内置秒传，S3 有 IAM + 没有内置 dedup，OSS 又是另一套），**全部由我们这层判定/补齐**——这样跨 sink 语义一致，未来切换/新增后端不动权限和上层语义。

**分类器也遵循同一原则：Classifier Core 是平台能力，Classification Profile 是业务适配包**。Core 只理解文件 facts、target、document_type、dst_path、错误等级和安全约束；“文件名最后一段是团队”“描述映射到某个业务任务”“路径按 category/task 展开”等都属于 profile，不写死进平台核心。

### 学习/简历目标

贯穿后端核心栈（FastAPI / Go / Kafka / Redis / PostgreSQL / MinIO / OpenTelemetry），通过"**抓包逆向 → 提炼通用模式 → 工程化产品**"的故事线，把每个组件都讲成"问题驱动选型"。

---

## 二、业务场景

真实世界形态：**总部 HQ 把文件按子公司分发到企业网盘/对象存储，每个子公司在自己的权限范围内能浏览和下载属于自己的文件**。对标产品：监管报送门户、企业供应商门户、银行对客户文件分发。

**写路径（HQ 视角）**：
- HQ 用户上传一组文件（zip / 多文件 / 远端 URL）
- 平台运行 HQ 配置的 classification profile，识别每个文件的投递目标 target + 文档类型 document_type
- 并行投递到 sink（首版 S3/MinIO，后续 阿里云 OSS / HTTP webhook / SFTP / 闭源企业网盘等）
- 实时进度推送、完整审计

**读路径（子公司视角）**：
- 子公司用户登录后只能看到自己 tenant 对应的 workspace
- 列出 / 搜索 / 下载该 workspace 下的文件
- 新文件到达时 SSE 实时通知（可选 webhook 转给子公司 IM）
- 所有读操作都落审计

**角色**：
- **HQ admin**：管理子公司 tenant、workspace、sink 凭证、分类规则
- **HQ uploader**：提交分发任务、看进度、重试失败项
- **Subsidiary viewer**：浏览本公司 workspace、下载文件
- **Subsidiary admin**：管理本公司用户、收件 webhook 配置

⚠️ **关键决策（请审计）**：
1. **租户关系不对称**：HQ 是 owner_tenant，子公司是 consumer_tenant；workspace 由 HQ 拥有，但 `target_tenant_id` 决定哪个子公司能读。这避免了对称协作的所有复杂度（无邀请、无对称邀请、无对称权限）。
2. **MVP 子公司只读**：未来若需子公司回传，加一个 `workspace.allow_target_write` 字段即可，不动结构。
3. **认证统一**：HQ 与子公司用户共用同一套 auth 系统（同一登录页），JWT 带 `tenant_id` 决定边界。**不做 SSO/SAML federation**——那是 IDP 层的事。

---

## 三、整体架构

### 3.1 部署视图

```
HQ Uploader ──┐                                       ┌── Subsidiary Viewer
  HTTP+SSE    │                                       │   HTTP+SSE
              ▼                                       ▼
     ┌───────────────────────────────────────────────────────┐
     │  Control Plane  (Python + FastAPI)                    │
     │   ┌──────────────┐                  ┌──────────────┐  │
     │   │  Write API   │                  │  Read API    │  │
     │   │  upload /    │                  │  list ws /   │  │
     │   │  classify /  │                  │  list obj /  │  │
     │   │  confirm /   │                  │  download /  │  │
     │   │  retry       │                  │  notif SSE   │  │
     │   └──────┬───────┘                  └──────┬───────┘  │
     │          │                                 │          │
     │  ┌────────────────────────────────────────────────┐   │
     │  │   Workspace Service（平台权威层）              │   │
     │  │   • Authz（tenant + role + ws.target_tenant）  │   │
     │  │   • Workspace ↔ Sink binding                   │   │
     │  │   • Object metadata（DB 真相，不查对端）       │   │
     │  │   • Dedup query（physical_object 表）          │   │
     │  │   • Audit producer（异步）                     │   │
     │  │   • Download token signer                      │   │
     │  └────────────────────────────────────────────────┘   │
     └────┬───────────┬──────────┬──────────────────┬────────┘
          │           │          │                  │
      Kafka       Redis       Postgres              │ 302 →
      tasks/      pub/sub /                         │ presigned URL
      audit       limit /                           │ (子公司浏览器
          │       SETNX                             │  直连 sink)
          ▼          ▲                              │
     ┌────────────────────────────────────────┐     │
     │  Data Plane (Go workers)               │     │
     │  • Sink interface + adapters           │     │
     │  • io.Pipe streaming + checksum        │     │
     │  • Multipart + 断点续传                │     │
     │  • 平台层 dedup precheck                │     │
     │  • AIMD rate limiter + 进度            │     │
     └────┬───────────────────────────────┬───┘     │
          │ PUT 字节                      │         │
          ▼                               ▼         ▼
     ┌─────────────────────────────────────────────────┐
     │  Heterogeneous Sinks                            │
     │  S3/MinIO │ Aliyun OSS │ Webhook │ SFTP │ ...    │
     └─────────────────────────────────────────────────┘

  暂存 / 缓冲：MinIO（zip 暂存 + multipart 分块缓冲）
  可观测：OpenTelemetry Collector → Jaeger / Prometheus / Grafana / Loki
```

### 3.2 写路径（HQ uploader）

1. HQ 用户提交 zip / 多文件 → Write API
2. Workspace Service：鉴权 → 配额检查 → Classifier Engine 运行 classification profile → 推任务到 Kafka `delivery.tasks.v1`
3. Go worker 消费任务：
   - **Stage 1** 查 `physical_object` 表做平台层 dedup（命中则零字节出口）
   - **Stage 2** 进 sink adapter（S3/OSS 看 multipart 阈值；闭源 sink 如 SMH 走自己的协商秒传协议）
   - **Stage 3** 实际传输（流式或并发分块）
4. 成功后回写 `physical_object`（ref_count++）+ `workspace_object` 元数据
5. 进度通过 Redis Pub/Sub 实时推 SSE 给前端
6. Audit 异步落 PG（producer 写 Kafka，consumer 落库）

### 3.3 读路径（Subsidiary viewer）

1. Subsidiary 用户登录 → 拿到带 `tenant_id` 的 JWT
2. `GET /my/workspaces`：Workspace Service 查 `target_tenant_id == me.tenant_id` 的所有 workspace
3. `GET /workspaces/{id}/objects`：查 DB `workspace_object` 元数据（**不打对端**），返回列表
4. `GET /objects/{id}/download`：鉴权后翻译成 sink 临时凭证（S3/OSS presigned URL；其他 sink 各自的临时访问凭据），**返回 302 让浏览器直连 sink**——大文件不经过控制面，节省带宽
5. **新文件通知**：Worker 写完 `workspace_object` 后发 Redis Pub/Sub 事件，控制面订阅后通过 SSE 推子公司前端
6. 所有读操作异步写 `audit_log`

### 3.4 分层职责

| 层 | 语言 | 职责 | 不做 |
|---|---|---|---|
| 控制面 | Python | 业务逻辑、规则引擎、配置 UI、Workspace 抽象、读路径鉴权与签发 | 不直接做文件 I/O；不做高并发流式传输 |
| 数据面 | Go | 高并发文件搬运、流式传输、对端协议适配、dedup precheck | 不做业务逻辑；不做用户鉴权 |
| 数据层 | — | PG / Redis / Kafka / MinIO | — |
| 可观测层 | — | OTel / Jaeger / Prometheus / Grafana | — |

⚠️ **关键架构决策（请审计）**：
- **下载走 302 直连 sink**：控制面只签短 TTL 的 presigned URL，不代理流量。优点：扩展性、节省带宽；代价：审计粒度只能到"签发动作"而非"实际下载完成"——可接受。
- **元数据 DB 真相，不查对端**：list 操作只查 PG，不打 sink。优点：快、稳定、避免对端配额；代价：需要保证 worker 写入元数据和写入 sink 两件事的一致性（用唯一约束 + 先写 sink 再写 DB 兜底）。
- **新文件通知用 Redis Pub/Sub 而不是 PG LISTEN/NOTIFY**：解耦控制面订阅者和 worker 进程；多 web 实例时天然 fanout。

---

## 四、核心抽象

⚠️ **三层抽象，自上而下**：
- **Workspace 层**（控制面，平台权威）：逻辑容器；定义"谁能访问、配额多少、审计什么"；与具体存储后端无关。
- **Sink 层**（数据面，存储协议适配）：把字节搬到对端；屏蔽 S3/OSS/webhook 协议差异。
- **Source 层**（数据面，被传输物的封装）：让文件可寻址、可重读、可校验。

分类规则不是第四个存储层，而是控制面里的 **Classification Profile**：一个版本化业务适配包，描述如何从文件 facts 解析 target/document_type、规则优先级如何执行、目标路径如何渲染。Classifier Core 不包含具体业务词汇；profile 的发布、回滚、UI 编辑留到 Phase 6.5+ 的 registry 能力。

### 4.0 Workspace 层（最重要）

```
tenant ── owner_tenant_id ──┐
                            │
                          workspace ────┬── workspace_sink_binding ── sink
                            │           │
                            │           └── workspace_object（DB 内文件元数据）
                            │
                            └── target_tenant_id（哪个子公司能读，隐含读权限）
```

**关键不变量**：
1. **Workspace 是平台层的权威真相**——即便对端有自己的权限/配额/dedup 系统（如 SMH 的 team space + 内置秒传），授权与计费由我们这层判定，不依赖对端语义。这样跨 sink 一致，未来切换/新增后端不动权限和上层语义。
2. **每个 workspace 服务一个子公司**（一对一）。HQ 为每个子公司创建一个 workspace；子公司用户访问时按 `target_tenant_id` 自动匹配到自己的 workspace 列表。
3. **Workspace 的物理位置由 binding 决定**：可以背靠 S3 的 (bucket, prefix)，可以背靠 OSS 的同等结构，也可以未来背靠 SMH 的 team space。**对子公司用户无感**。
4. **元数据存我们 DB**（`workspace_object` 表）——子公司 list 文件不打对端，速度快、避免对端配额浪费、保证一致性。

### 4.1 Sink 接口

```go
type Sink interface {
    // 标识
    Name() string                 // "s3", "oss", "webhook", "sftp", ...
    Capability() Capability       // 声明能力，调度器据此优化

    // 核心动作（所有 sink 都只暴露这一个）
    Upload(ctx context.Context, src Source, meta Meta) (Receipt, error)

    // 生命周期
    HealthCheck(ctx context.Context) error
    Close() error
}
```

⚠️ **关键决策**：所有 Sink 都暴露 `Upload` 一个动作，**不暴露分阶段 API**。SMH 的三段协商、S3 的 multipart、GCS 的 resumable 全部封装在各自 adapter 内部状态机里。理由：这三种协议的"阶段"语义完全不同，强行抽统一接口 = 漏抽象。

### 4.2 Capability 矩阵

```go
type Capability struct {
    SupportsInstantUpload bool   // 内容寻址秒传（SMH ✓ / S3 ✗）
    SupportsResume        bool   // 字节级断点续传（GCS ✓ / SMH ✗）
    SupportsMultipart     bool   // 分片并发（S3 ✓ / webhook ✗）
    PrefersChecksum       string // "sha256" / "md5" / ""
    MaxFileSize           int64
    MaxConcurrentParts    int
}
```

调度器据此优化：
- 小文件优先走支持秒传的 sink（命中率高、几乎零带宽）
- 传输管道根据 `PrefersChecksum` 挂 `io.TeeReader` 边读边算 hash，不读两遍
- `SupportsResume=false` 的 sink 只能做"文件粒度"续传，不是字节粒度
- `MaxFileSize` 在上游就拦掉超大文件

### 4.3 Source 抽象

```go
type Source interface {
    Open(ctx context.Context) (io.ReadCloser, error)                          // 可多次调用
    OpenRange(ctx context.Context, offset, length int64) (io.ReadCloser, error) // 大文件分块用
    Size() int64
    Path() string                                                             // 仅日志/trace 用
    Checksum(ctx context.Context, algo string) (string, error)                // 内部缓存
}
```

⚠️ **为什么不是 `io.Reader`**：内容寻址 dedup（无论平台层还是某些 sink 的内置秒传）都需要预读 first-64K hash 和 full-file hash，单向 Reader 读完即空。`Source.Open()` 可重复打开、`Checksum()` 由 source 自己决定缓存策略——既支持本地文件多次读盘，也支持 S3 暂存对象按需 GET。

⚠️ **为什么需要 `OpenRange`**：S3 / OSS multipart 上传要求按 part offset 顺序或并发读；断点续传也需要从指定字节位置恢复。`OpenRange` 让 Source 实现自己决定怎么高效切片（本地用 `io.SectionReader`，远端用 HTTP Range）。

具体实现：
- `FileSource` ：本地文件，`os.Open + io.NewSectionReader`
- `S3StagedSource` ：MinIO 暂存对象，`GetObject` + `Range` header
- `MemorySource` ：压测/测试用，slice 切片
- `RemoteURLSource` ：远端 HTTP，`Range` 请求

### 4.4 Task 状态机

```
draft → classifying → classified → confirmed → queued
                  ↓                        ↓
                failed              uploading → succeeded
                                          ↓        ↑
                                    partial_failed─┘ (重试)
                                          ↓
                                        failed
```

每个文件（task_item）独立 `delivery_status`：`pending / uploading / delivered / failed / skipped / instant_hit`。

---

## 五、技术栈与选型理由（每条都是面试问答稿）

| 组件 | 解决什么问题 | 不选别的因为 |
|---|---|---|
| **FastAPI（Python）** | 业务逻辑/规则引擎/配置 UI 迭代快 | Go 写业务啰嗦；Pydantic 校验体验无敌 |
| **Go 数据面 worker** | I/O 密集、千级并发流式传输 | Python asyncio 也能但 Go 的 goroutine + io.Reader 更顺手；展示双语言能力本身是加分项 |
| **Kafka** | 控制面/数据面解耦、横向扩 worker、DLQ 重试 | RabbitMQ 也行；Kafka 在国内大厂面试高频，分区路由保证"同 task 同 worker" |
| **Redis** | 一物多用：① 分布式令牌桶限对端 QPS ② 进度 Pub/Sub 跨 web 实例 SSE ③ 幂等键 SETNX ④ 分布式锁 | 性价比最高的中间件 |
| **PostgreSQL** | 任务状态、租户/用户、规则版本、审计 | JSONB + `SELECT ... FOR UPDATE SKIP LOCKED` 模式成熟；多租户 RLS 备选 |
| **MinIO** | 多 worker 共享 zip 暂存；S3 兼容 | 本地能跑 demo，云上能跑 prod，无缝切 S3/OSS |
| **OpenTelemetry + Jaeger** | 跨 Python/Go/Kafka/外部 API 端到端 trace | 唯一能在 Demo 里画出完整 trace 的方案 |
| **Prometheus + Grafana** | RED/USE 指标、上传速率、错误率、长尾 | 标配 |
| **Vue/React + SSE** | 前端进度实时推送 | WebSocket 双向不需要；SSE 简单到 50 行 |

⚠️ **关键决策（明确不要的）**：ClickHouse / TiDB / Elasticsearch / Service Mesh / etcd / 自研共识。一旦加进来你讲不清就扣分。

---

## 六、关键技术亮点（每条都能在面试深讲 5-10 分钟）

### 6.1 项目起源 —— 抓包逆向 SMH 三段协商上传协议
- **背景**：在职期间用腾讯企业网盘做总部 → 子公司分发，订阅成本高、自动化能力弱；抓包逆向其 RESTful API 写了 CLI 替代手工
- **学到的协议模式**：内容寻址 dedup 的"分段 hash 协商"——先试无 hash → 失败再补 first-64K hash → 还失败再补 full hash；任意阶段命中后端 dedup 即触发"秒传"，零字节出口
- **从单点到通用的提炼**：这套模式不是 SMH 独有的，**任何对象存储后端（即便不内置）都可以在平台层补一份**——所以 § 6.10 才把"内容寻址 dedup"提到平台层做
- **设计取舍**：要不要把"分段协商"提到 Sink 通用接口？答：**不**。S3 的 multipart 是空间分片（不同语义）、SMH 的三段是协商（探测 dedup）、未来 GCS resumable 又是 session——抽象会变成漏抽象。每种协议自己在 adapter 内部状态机里实现
- 深讲：从抓包到提炼通用模式的过程；YAGNI 与漏抽象的边界；首版 sink 选 S3/MinIO 而非 SMH 的理由（开源、本地易起、社区资源最丰富，详见 ADR 0010）

### 6.2 `io.Pipe` 流式传输
- 一端是本地文件 reader，一端是对端 PUT writer，中间挂 `TeeReader` 算 hash
- G 级文件常驻内存几 MB
- 深讲：Go `io.Reader` 哲学 vs Python `BytesIO` 整文件入内存的反例

### 6.3 Redis 令牌桶 + AIMD 自适应并发
- Lua 脚本原子取令牌（避免竞态）
- 对端连续 429 → worker 数指数回退；429 消失 → 线性爬升
- 深讲：反压机制、为什么不是固定速率限流

### 6.4 至少一次 + 幂等 = exactly-once 工程实现
- Kafka 至少一次投递 + PG 唯一约束 `(task_id, file_path, file_hash)` + 分片 `confirmKey` 持久化
- 崩溃重启从中断点续传，不重传已成功项
- 深讲：分布式系统里 exactly-once 的真实含义

### 6.5 OpenTelemetry 跨语言端到端 trace
- Python 接收请求 → 注入 traceparent → Kafka header 透传 → Go 提取 → 外部 API → Jaeger 看完整链路
- 深讲：W3C trace context 规范、采样策略

### 6.6 优雅关停（SIGTERM 演示）
- 收到信号 → 停止 Kafka 消费 → in-flight 任务做完或快照 → 上报 metrics → 退出
- 深讲：drain 模式、为什么不直接 SIGKILL、K8s preStop hook

### 6.7 Capability-driven 调度
- 调度器读各 sink 的 Capability，对小文件优先走支持秒传的、超大文件路由到支持 multipart 的
- 深讲：策略模式 + 能力声明 vs 隐式假设

### 6.8 跨 Sink 一致的 Workspace 语义
- 平台层维护 workspace 权威真相；SMH 的 team space 只是其中一种实现方式
- 即使 SMH 内置权限/配额系统，授权仍由我们这层判定；为什么——保证跨 sink 语义一致、审计统一、未来切换 sink 不动权限模型
- 深讲：抽象层级选择 + 异构后端"最小公共子集" vs "我方权威"两种设计哲学

### 6.9 受限读路径 + 临时下载凭证
- 子公司用户读文件流程：JWT 校验 → workspace.target_tenant_id 校验 → 查 DB 元数据 → 翻译成 sink 临时凭证（S3 presigned URL / SMH access_token）→ 302 重定向或 stream
- 关键：**绝不把 sink 凭证暴露给子公司**；每次下载临时签发，带最小权限和有限 TTL
- 深讲：信任边界、最小权限原则、审计可追溯链

### 6.10 智能上传链路（平台 dedup → 可选 sink dedup → 实际传输）
- **Stage 1 — 平台层 dedup（首版默认开启）**：先算 sha256，查 `physical_object` 表；命中则零字节出口，多个 workspace_object 共用一份物理字节（HQ 一份政策文件分发给 N 家子公司，节省 N 倍带宽）。**这是首版能讲出最大数字的地方**，因为 S3/MinIO 不内置 dedup
- **Stage 2 — Sink 内置秒传（可选，仅部分 sink 支持）**：未命中平台 dedup 时，若 sink 自身有 dedup 协议（如未来扩展的 SMH 三段协商），由 sink adapter 内部状态机处理；S3/OSS 这一阶段直接跳过
- **Stage 3 — 实际传输**：单段流式 PUT 或 multipart 并发上传
- 深讲：cache-like 三层思维 + 跨 sink 一致性（**对端有没有 dedup 我们都能保证语义统一**——这是 § 6.8 的具体落地）+ Capability 矩阵驱动调度（小文件优先选有秒传的 sink）

### 6.11 大文件分块上传 + 断点续传
- size ≥ threshold（~50MB）时，sink adapter 内部走 multipart：part size 8MB、并发 4-8 part、ETag 落 `multipart_session` 表 + Redis 缓存
- 失败保留 session（不 abort），下次任务从最后成功的 part 续传；后台 GC 清理超期 session
- Source 通过 `OpenRange` 高效切片读
- 深讲：part size 取舍、并发度、失败语义、resume 状态机、对端协议适配（S3 ETag vs OSS upload_id）

### 6.12 内容寻址 dedup 的引用计数与 GC
- `physical_object.ref_count` 跟踪逻辑引用数；删 workspace_object → ref_count--；ref_count == 0 → 标记 orphan，后台 GC 异步删 sink 物理字节
- 选 ref_count + 异步 GC 而不是 mark-and-sweep：简单、可观测、错了只会泄露空间不会丢数据
- 深讲：分布式 GC 设计权衡、跨 tenant/sink 隔离边界、SHA256 + size 联合防碰撞

---

## 七、推荐目录结构

```
file-delivery-platform/   ← 建议项目改名（更通用）
├── control-plane/                # Python FastAPI
│   ├── app/
│   │   ├── api/                  # routers
│   │   │   ├── auth.py
│   │   │   ├── tasks.py
│   │   │   ├── registry.py
│   │   │   └── tenants.py
│   │   ├── core/                 # config, security, db, telemetry
│   │   ├── models/               # SQLAlchemy 模型
│   │   ├── schemas/              # Pydantic
│   │   ├── services/             # rule engine, classifier, dispatcher
│   │   ├── repos/                # 数据访问层
│   │   └── main.py
│   ├── alembic/                  # DB migrations
│   ├── tests/
│   ├── pyproject.toml
│   └── Dockerfile
│
├── data-plane/                   # Go worker
│   ├── cmd/worker/main.go
│   ├── internal/
│   │   ├── sink/                 # 核心抽象
│   │   │   ├── sink.go           # interface + Capability + Source
│   │   │   ├── s3/               # S3 / MinIO adapter (multipart)  ← 首版
│   │   │   ├── oss/              # 阿里云 OSS adapter（Phase 7）
│   │   │   ├── webhook/          # HTTP webhook（Phase 7）
│   │   │   └── mock/             # 压测用（Phase 2）
│   │   │   # 未来扩展：sftp/、smh/（闭源企业网盘，三段协商）等
│   │   ├── source/               # File / S3 / Memory / RemoteURL source 实现
│   │   ├── pipeline/             # io.Pipe 编排
│   │   ├── ratelimit/            # Redis 令牌桶
│   │   ├── kafka/                # consumer
│   │   ├── progress/             # Redis pub/sub
│   │   ├── resume/               # 断点续传持久化
│   │   └── observability/        # OTel + Prometheus
│   ├── go.mod
│   └── Dockerfile
│
├── web/                          # 前端（保留现有 + 改造）
│   ├── public/index.html
│   ├── js/
│   └── css/
│
├── deploy/
│   ├── docker-compose.yml        # 一键起本地全栈
│   ├── docker-compose.obs.yml    # 可观测组件
│   ├── grafana/dashboards/
│   ├── prometheus/prometheus.yml
│   └── otel/otel-config.yaml
│
├── proto/                        # 可选：gRPC 定义（备用，主用 Kafka）
├── docs/
│   ├── ARCHITECTURE.md           # 架构详细
│   ├── SINK_PROTOCOL.md          # Sink 接口规范 + 各 adapter 协议说明
│   ├── BENCHMARKS.md             # 压测结果
│   └── ADR/                      # 架构决策记录
│       ├── 0001-dual-language.md
│       ├── 0002-kafka-vs-rabbit.md
│       ├── 0003-sink-not-staged.md
│       └── 0004-tenant-strategy.md
└── README.md
```

⚠️ **关键决策**：单仓 monorepo（Python + Go 同仓），用 GitHub Actions 跑两套 CI。理由：跨语言改动可一次提交、ADR 集中、面试方便看完整。

---

## 八、数据库设计要点

核心表（PG schema）：

**身份与租户**
| 表 | 说明 | 关键字段 |
|---|---|---|
| `tenant` | 租户（HQ + 多个子公司） | id, name, type ('hq'/'subsidiary'), parent_tenant_id |
| `user` | 用户（属于某 tenant） | id, tenant_id, email, role ('admin'/'uploader'/'viewer') |
| `sink_credential` | sink 凭证（仅 HQ 持有） | id, tenant_id, sink_type, encrypted_blob |

**分发规则与 Workspace**
| 表 | 说明 | 关键字段 |
|---|---|---|
| `registry_version` | 分类规则版本 | id, tenant_id (HQ), version_no, status, config_json |
| `workspace` | 逻辑容器（HQ 拥有，服务一个子公司） | id, name, owner_tenant_id (HQ), target_tenant_id (子公司) |
| `workspace_sink_binding` | workspace 物理落地 | workspace_id, sink_id, root_path |
| `physical_object` | **物理字节（内容寻址 dedup）** | id, owner_tenant_id, sink_id, sink_path, size, hash, ref_count, UNIQUE(owner_tenant_id, sink_id, hash, size) |
| `workspace_object` | workspace 内的逻辑文件（指向 physical_object） | id, workspace_id, physical_object_id, display_name, uploaded_by_user_id, uploaded_at |
| `multipart_session` | 大文件分块上传断点续传 | id, task_item_id, sink_id, sink_session_id, part_size, completed_parts_json, expires_at |

**写路径（任务）**
| 表 | 说明 | 关键字段 |
|---|---|---|
| `task` | 分发任务 | id, owner_tenant_id, user_id, status, registry_version_id, idempotency_key |
| `task_item` | 单文件 | id, task_id, src_path, target_workspace_id, dst_path, file_hash, delivery_status, sink_name |
| `task_event` | 状态事件流 | id, task_id, attempt_id, event_type, payload, created_at |

**审计与通知**
| 表 | 说明 | 关键字段 |
|---|---|---|
| `audit_log` | 所有读写操作落审计 | id, actor_user_id, actor_tenant_id, action, resource_type, resource_id, ts, ip |
| `notification` | 子公司通知（新文件到达） | id, recipient_tenant_id, workspace_id, payload, delivered_at |

⚠️ **关键决策**：
- **不再有 `workspace_share` 表**：子公司读权限隐含在 `workspace.target_tenant_id` 里，一行 SQL 查"我能看的所有 workspace"。
- 所有"用户数据"表都带 `tenant_id`/`owner_tenant_id`/`target_tenant_id`，**仓储层强制过滤**——不用 PG RLS（在 ADR 0004 里说明取舍）。
- `idempotency_key` 唯一约束防止重复提交。
- `sink_credential.encrypted_blob` 用 Fernet 加密（主密钥从环境变量读）。
- `task_item` 唯一约束 `(task_id, src_path)` 保证幂等。
- **`physical_object` 与 `workspace_object` 解耦**：前者是物理字节（按 `(owner_tenant_id, sink_id, hash, size)` 去重），后者是逻辑文件（每个 workspace 各一行，指向同一 physical_object 实现 dedup）。
- **Dedup 范围严格在 `(owner_tenant_id, sink_id)` 内**：跨 tenant 不共享物理字节（隐私），跨 sink 不共享（物理字节本就不一样）。
- **删除走 ref_count 异步 GC**：删 workspace_object → ref_count--；==0 时标记 orphan，后台 job 异步删 sink 字节。错了只泄露空间，不丢数据。
- `audit_log` 写入路径是**异步**（producer 写 Kafka，consumer 落库），写路径不要被审计阻塞。
- `multipart_session.expires_at` 用于 GC 超期未完成的 session，避免对端长期堆积未完成 multipart upload（对端会按 part 计费）。

---

## 九、Kafka 主题设计

| Topic | Partition Key | 用途 |
|---|---|---|
| `delivery.tasks.v1` | `task_id` | 控制面 → 数据面任务分发；同 task 落同 partition 保顺序 |
| `delivery.results.v1` | `task_id` | 数据面 → 控制面任务完成回报 |
| `delivery.tasks.v1.dlq` | `task_id` | 重试耗尽进死信 |

消费组：`worker-{env}`，多实例同组消费实现横向扩。

---

## 十、实施阶段（每阶段都有"完成定义"）

### Phase 0：清理与骨架（1-2 天）
- 删除 cosdrive 半成品中不要的部分（Prefect、portal_state、registry draft/publish）
- 建立 monorepo 目录结构
- **完成定义**：目录骨架建好，README 链接到本蓝图

### Phase 1：Python 单体 MVP（4-6 天）
- FastAPI + SQLite + asyncio 进程内 worker
- 移植 `_legacy/` 分类器与 aiohttp 流式上传逻辑（v0 是 SMH 协议，Phase 1 改为对接 S3/MinIO，保留流式 + 嵌套并发的核心模式）
- SSE 进度推送（进程内 pub/sub）
- 前端跑通端到端
- 本地用 docker 起 MinIO 作为对端
- **完成定义**：本地起 FastAPI 进程 + MinIO 容器，浏览器能上传 zip → 看见分类预览 → 确认 → 看进度 → 文件到达 MinIO bucket

#### Phase 1 完成 Changelog（2026-05-11）

| 模块 | 产出 |
|---|---|
| 项目骨架 | `control-plane/`，FastAPI + uv + ruff，`/healthz` |
| 配置层 | `app/core/settings.py`，pydantic-settings，`.env` 支持 |
| 数据库 | SQLAlchemy 2.0 async + aiosqlite，alembic migration，3 张表（task / task_item / task_event） |
| Repos（TDD） | TaskRepo / ItemRepo / EventRepo，async session，flush-only 契约 |
| Classifier（TDD） | `classify_zip(zip_bytes, profile)` + ProfileConfig dataclass；26 个 TDD case；strategy: directory_or_filename / broadcast |
| S3 上传 | aioboto3 流式上传，>50MB multipart，嵌套 semaphore 并发，progress callback |
| Progress Bus（TDD） | 进程内 asyncio fanout，subscribe context manager，sentinel close |
| Task Runner | 状态机编排：confirmed → uploading → uploaded/partial_failed，BackgroundTask 驱动 |
| API 路由 | 9 个端点，`/api/v1` 前缀，SSE progress 流 |
| Pydantic Schemas | 9 个 response schema，extra=forbid |
| S3 单测 | moto mock，4 个 case |
| docker-compose | MinIO + minio-init（自动建 bucket） |
| E2E 集成测试 | 4 个 case，内存 SQLite，mock S3 |
| 前端改造 | 去掉注册表配置段，API 改 `/api/v1`，进度改 SSE |
| 测试总数 | **100 个 test case，全部通过** |

### Phase 2：拆 Go 数据面（5-7 天）
- 实现 Go worker 骨架：Sink interface + Source + 一个 mock sink + 一个 S3 sink（aws-sdk-go-v2 + MinIO endpoint）
- Kafka 把控制面和数据面串起来
- Python 端发任务到 Kafka，Go 端消费
- **完成定义**：相同的端到端流程，但上传环节由 Go worker 完成

### Phase 3：换上"真"数据层（3-4 天）
- SQLite → MySQL（用 alembic 做 schema migration）
- 本地磁盘 → MinIO 暂存
- **完成定义**：`docker compose up` 起全栈，跑通端到端

### Phase 4：Redis 一物多用（3-5 天）
- 令牌桶限速
- Pub/Sub 跨进程进度（为多 web 实例铺路）
- 幂等键 + 分布式锁
- **完成定义**：模拟对端 429，worker 自动降速；模拟重复提交相同任务，被幂等键拦截

### Phase 5：可观测三件套（3-4 天）
- OpenTelemetry SDK 接入 Python + Go
- Jaeger 展示跨语言 trace
- Prometheus exporter + Grafana 面板
- **完成定义**：Jaeger 里能看到一条 trace 横跨 Python → Kafka → Go → 外部 API；Grafana 有 RED 面板

### Phase 6：多租户 + 鉴权（4-6 天）
- 租户表（HQ + subsidiary 两类）、用户表 + JWT
- 仓储层强制 `tenant_id` / `owner_tenant_id` / `target_tenant_id` 过滤
- RBAC：admin / uploader / viewer
- **完成定义**：HQ 用户和子公司用户的数据完全隔离；权限测试用例覆盖

### Phase 6.5：Workspace + 子公司读视图（5-7 天）
**这一阶段是把"投递管道"升级为"分发平台"的关键**。
- 新增 `workspace` / `workspace_sink_binding` / `workspace_object` / `audit_log` / `notification` 表
- 写路径增量：task_item 投递成功后写 `workspace_object` 元数据
- 读路径全新：
  - `GET /my/workspaces` — 子公司用户列出自己能看的 workspace
  - `GET /workspaces/{id}/objects` — 列文件元数据（查 DB，不打对端）
  - `GET /objects/{id}/download` — 校验后翻译成 sink 临时凭证返回
  - `GET /my/notifications/stream` — SSE 推新文件到达
- 审计：所有读写操作异步写 `audit_log`（经 Kafka）
- **完成定义**：HQ 用户上传一批文件，子公司用户登录后能看到属于自己的文件并下载；管理后台能查到完整审计链。

### Phase 7：扩 Sink + 压测（5-7 天）
- S3 已在 Phase 1/2 实现；这里加阿里云 OSS sink 和 webhook sink
- 加 mock sink 模拟各种异常（429、超时、随机失败）
- 可选 stretch：把 `_legacy/smh_uploader/api_client.py` 抓包逆向出来的三段协商上传协议封装成 SMH adapter，作为"已知有内置秒传的 sink"压测样本，验证 § 6.10 Stage 2 路径能跑通
- k6 / wrk 压测，记录数字
- **完成定义**：BENCHMARKS.md 写好；至少 3 张性能对比图

### Phase 8（可选）：HA 改造
- Redis 替换进程内 pub/sub
- 多 web 实例 + nginx
- worker 拆独立进程
- **完成定义**：能 rolling restart 而不掉任务

---

## 十一、压测与基准（练手 + 简历数字）

| 实验 | 目的 | 期望产出 |
|---|---|---|
| **平台层 dedup 命中率** | HQ 一份 100MB 文件分发 30 家子公司（首版重头戏） | 朴素 3GB 出口流量 → dedup 后 100MB；总耗时从 X → Y |
| 平台 dedup vs sink 内置 dedup | 如果 Phase 7 stretch 接入 SMH，两种 dedup 命中率 + 出口流量对比 | 验证"S3 没有内置 dedup 也能达到接近 SMH 的节省率"——即 § 6.8 跨 sink 一致性的有效性 |
| 流式 vs 整文件入内存 | Go pipeline vs Python BytesIO | 1GB 文件，内存峰值 30MB vs 1GB |
| **multipart vs 单段** | 单文件吞吐 + 断点续传可靠性 | 1GB 单段 X MB/s，8MB 分块 4 并发 Y MB/s（提升 Z%）；模拟中断恢复成功率 100% |
| AIMD vs 固定限流 | 反压机制有效性 | 同样对端 QPS 限制下，AIMD 吞吐高 X%、错误率低 Y% |
| 单 worker vs N worker | 横向扩缩 | 线性度 / 拐点在哪 |
| 优雅关停 vs 强杀 | drain 价值 | 强杀丢 X 任务，优雅关停丢 0 |

每条都能成简历 bullet。

---

## 十二、明确不做的事

- ❌ K8s 部署：除非真懂调优，docker-compose 已足够（想加可以做一份 Helm chart 备查 + 一段 ADR 说明为何不在 demo 里用）
- ❌ Service Mesh / Istio：完全不必要
- ❌ 自研共识 / Raft：用现成的，能讲清 Redlock 局限就够
- ❌ ClickHouse 做"分析"：你没数据
- ❌ 微前端 / Nx monorepo 工具：scope 不对
- ❌ "全用 Go 重写"：异构本身就是亮点
- ❌ 测试覆盖率为 0：Go table-driven test + Python pytest，目标 70%+
- ❌ **OIDC / SAML / SSO federation**：那是 IDP 层的事，与本项目核心无关；HQ 和子公司用户共用一套 auth 即可
- ❌ **对称协作功能**（评论 / 共编 / IM / wiki）：明确分发场景不需要，scope 失控的开端
- ❌ **Object 子树共享**：MVP 只做 workspace 整体共享给 target_tenant；如果未来真要细到子目录授权再说
- ❌ **依赖 SMH 自身的权限/配额系统做授权决策**：必须由我们这层判定，否则跨 sink 不一致

---

## 十三、自我审视清单（提交每个 Phase 时检查）

- [ ] 这个抽象是不是为多个实现而做？只有一个实现的 interface 是不是噪声？
- [ ] 这个组件能不能讲清楚"解决什么问题"，"为什么不是 X"？
- [ ] 错误路径有没有覆盖？（超时、对端异常、本地崩溃、网络抖动）
- [ ] 测试在不在？（Go 用 table-driven test，Python 用 pytest）
- [ ] 这一步加的复杂度对应了什么真实需求？还是 over-engineering？
- [ ] 对应的 ADR 写没写？

---

## 十四、参考资源（只列真正有用的）

- 《Designing Data-Intensive Applications》—— 多租户、消息系统、事务、一致性
- Kafka 官方 KIP / Confluent blog —— partition、消费组语义
- Go `io` 包源码 —— Reader / Writer / Pipe / TeeReader 的设计哲学
- 阿里云 OSS Go SDK 源码 —— 看 multipart upload 是怎么写的
- W3C Trace Context spec —— OpenTelemetry 跨服务透传
- 原仓库 `git show HEAD:smh_uploader/*.py` —— 原脚本的流式上传与团队匹配是好参考

---

*v1.5 — 由主编排 Agent 协助迭代 — 待审计与迭代。*

**v1.1 改动（基于"总部 → 子公司分发"真实场景澄清）**：
- § 一/二：定位从"通用 SaaS"具化为"HQ → 子公司文件分发与观测平台"；明确不对称租户关系
- § 四：核心抽象插入 4.0 Workspace 层；明确三层（Workspace / Sink / Source）
- § 六：新增 6.8（跨 sink 一致 Workspace 语义）、6.9（受限读路径 + 临时下载凭证）
- § 八：数据模型重构——增加 workspace / workspace_sink_binding / workspace_object / audit_log / notification；删除（不再需要）workspace_share
- § 十：插入 Phase 6.5（Workspace + 子公司读视图）
- § 十二：明确不做的事新增 SSO federation、对称协作、object 子树共享、依赖 sink 自身权限

**v1.2 改动（平台层 dedup + 大文件分块）**：
- § 四：Source 接口加 `OpenRange` 支持分块/断点续传
- § 六：新增 6.10（三级智能上传链路）、6.11（multipart + 断点续传）、6.12（ref_count GC）
- § 八：物理/逻辑解耦——`workspace_object` 拆出 `physical_object` 表（内容寻址 dedup）；新增 `multipart_session` 表
- § 十一：压测增加"平台层 dedup 价值"和"multipart vs 单段"两组对比

**v1.3 改动（定位与架构图重写）**：
- § 一：定位重写——从"通用 SaaS"叙事转向"HQ 写路径 + 平台权威层 + 子公司读路径"三块拼装；明确"Workspace 是权威真相，sink 是实现方式"的核心设计哲学
- § 三：架构图重画——HQ uploader 与 Subsidiary viewer 作为两类用户分别从两侧入口；Workspace Service 显式画为控制面的中央权威模块；新增 3.2/3.3 写路径与读路径的步骤化描述；3.4 关键架构决策（302 直连 / 元数据 DB 真相 / Redis Pub/Sub）

**v1.4 改动（SMH 降级为灵感来源，首版后端 = S3/MinIO）**：
- § 一：加"起源故事"段落——抓包逆向 SMH → 提炼通用模式 → 工程化产品；首版 sink 改为 S3/MinIO；学习目标改为"抓包逆向 → 提炼通用模式 → 工程化产品"叙事线
- § 二：写路径 sink 列表中 COS/SMH 不再首发，改为 S3/MinIO 首发
- § 三：架构图 Heterogeneous Sinks 列表更新；§ 3.2 Stage 2 措辞改为"S3/OSS 看 multipart 阈值；闭源 sink 走自己的协商秒传协议"；§ 3.3 下载凭证示例去掉 SMH access_token
- § 四：4.0 不变量改写为通用版"即便对端有自己的权限/配额/dedup 系统..."；4.1 Sink Name 注释更新；4.3 OpenRange 注释改写
- § 六：6.1 改为"项目起源 — 抓包逆向 SMH 三段协商上传协议"，强调起源故事 + 提炼过程；6.10 改为两阶段强制 + 一阶段可选（Stage 2 sink 内置秒传标为"未来 SMH 等 sink 的可选适配"）
- § 七：data-plane/internal/sink/ 树更新——s3 排首位、smh 移除（空目录已删）；闭源 sink 作为未来扩展注释
- § 十：Phase 1 完成定义改为"MinIO 上传成功"；Phase 2 改为 S3 sink；Phase 7 加 SMH 作为 stretch goal
- § 十一：压测删掉"SMH 三段协商命中率"，改为"平台层 dedup 命中率"为首版重头戏；新增"平台 dedup vs sink 内置 dedup"对比作为 Phase 7 stretch
- 新增 ADR 0010：项目起源叙事 & 首版后端选 S3/MinIO
- docs/SINK_PROTOCOL.md 重写：S3/MinIO 提到首节详写 multipart 协议；SMH 移到"未来扩展"
- 顶层 README 加"起源"段落

**v1.5 改动（Classifier Core / Classification Profile 分层）**：
- § 一：核心设计哲学补充分类器分层原则——Classifier Core 是平台能力，Classification Profile 是业务适配包
- § 二/三：写路径从“解析分类规则”改为“运行 classification profile”，输出 target + document_type
- § 四：明确 Classification Profile 属于控制面业务适配，不是存储层；profile 发布/回滚/UI 留 Phase 6.5+
- 新增 ADR 0011：Classifier Core 与业务 Profile 分层

后续每一次重大决策请在 `docs/ADR/` 下记录。
