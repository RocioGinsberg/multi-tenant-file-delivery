# Technical Notes

> 本文保留从原 BLUEPRINT 拆出的工程要点、面试解释点和开发自检清单。已经确定的架构决策仍以 `docs/ADR/` 为准；待评审方案进入 `docs/RFC/`。

## 核心抽象

| 抽象 | 所属平面 | 作用 |
|---|---|---|
| Workspace | 控制面 | 平台权威逻辑容器，定义谁能访问、配额、审计和对象元数据 |
| Sink | 数据面 | 存储协议适配，把字节写入 S3 / MinIO / OSS / Webhook / SFTP 等后端 |
| Source | 数据面 | 被传输对象的可重复读取封装，支持 checksum 和 range read |
| Classification Profile | 控制面 | 业务分类配置包，把文件 facts 映射成 target、document_type、dst_path |

关键原则：Workspace 是平台权威真相，sink 只是实现方式。权限、审计、dedup、读路径语义不能依赖某个 sink 自己的权限系统。

## 技术亮点

### 抓包逆向到通用平台

项目起源于 SMH 自动化上传脚本。SMH 的三段 hash 协商展示了内容寻址 dedup 的典型模式：先尝试基础上传，再补 first-64K hash，再补 full hash；命中后可实现零字节出口。

本项目把它抽象成通用能力：平台先做自己的 dedup，再由 sink adapter 决定是否利用对端内置秒传协议。

### 流式传输

数据面优先使用 `io.Reader` / `io.Pipe` 风格管道，避免大文件整体读入内存。上传链路应在读取时顺带计算 checksum，减少重复 I/O。

### 至少一次与幂等

Kafka transport 采用至少一次语义。工程上的“接近 exactly-once”依赖：

- result 落库成功后再 commit offset。
- DB 唯一约束防止重复写。
- 任务状态机拒绝非法回退。
- 可恢复状态持久化，例如 multipart session 和 completed parts。

### Capability-driven 调度

Sink 通过 capability 声明能力，例如是否支持 multipart、resume、checksum、最大文件大小和并发 part 数。调度器不硬编码具体 sink，而是基于能力做路由和优化。

### 受限读路径

子公司读文件时只查平台 DB 元数据。下载由控制面鉴权后签发短 TTL presigned URL 或临时凭证，浏览器直连 sink，控制面不代理大文件流量。

代价是控制面只能准确审计“凭证签发”，不一定知道浏览器是否完整下载；后续可用 sink access log 或回调补齐。

### 大文件 multipart 与 resume

大文件达到阈值后由 sink adapter 内部走 multipart。`Source.OpenRange()` 负责按 offset 读取，`multipart_session` 持久化 upload id、part size 和 completed parts。

失败时不立即 abort session，优先保留恢复能力；后台 GC 清理过期 session。

### Dedup 与 GC

`physical_object` 表示物理字节，`workspace_object` 表示逻辑文件。多个 workspace object 可以引用同一个 physical object。

删除逻辑文件时只减少引用计数；`ref_count == 0` 后标记 orphan，再由后台 GC 删除物理字节。这个策略的失败模式偏向空间泄露，而不是误删数据。

### OTel 跨语言 trace

目标链路是 Python 接收请求，向 Kafka 注入 trace context，Go worker 提取上下文，再写 sink。Jaeger 中应该能看到 Python -> Kafka -> Go -> sink 的完整 trace。

## 明确不做

- Demo 阶段不引入 Kubernetes / Service Mesh。
- 不自研共识、Raft、复杂调度系统。
- 不做通用网盘协作功能，例如评论、共编、IM、知识库。
- 不把所有服务改成同一种语言。
- 不依赖 sink 自身权限系统作为平台授权来源。
- 不在首版做 OIDC / SAML / SSO federation。

## 开发自检

- 这个抽象是否至少服务两个实现，否则是否只是噪声。
- 这个组件能否讲清“解决什么问题”和“为什么不是另一个方案”。
- 错误路径是否覆盖：超时、对端异常、本地崩溃、网络抖动、重复消息。
- 测试是否覆盖对应层级：单元、集成、端到端。
- 新增复杂度是否对应真实需求。
- 重大取舍是否进入 RFC 或 ADR。
