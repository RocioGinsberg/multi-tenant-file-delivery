# ADR 0001 — 双语言架构（Python 控制面 + Go 数据面）

- **状态**：Accepted（2026-05）
- **决策者**：Rocio
- **影响范围**：整个项目骨架

## 背景

平台同时承担两类截然不同的工作负载：

1. **控制面**：业务逻辑迭代频繁——分类规则引擎、租户/Workspace/RBAC、配置 UI、读路径授权与签发、审计编排。这类工作 SLA 在百毫秒量级，吞吐不高，但**业务复杂度高、变更频繁**。
2. **数据面**：高并发、I/O 密集——单进程同时把成百上千个文件流式投递到对端 sink。这类工作 SLA 在文件吞吐量级（MB/s × 并发数），**性能敏感、变更很慢**（接口稳定后基本只调参数）。

把两种工作放在同一种语言里写，必然在某一头吃亏：要么控制面拖慢迭代，要么数据面性能上不去。

## 决策

- **控制面用 Python（FastAPI）**：业务表达力优先。Pydantic 校验、SQLAlchemy 仓储、依赖注入都是 FastAPI 生态的强项。
- **数据面用 Go**：高并发流式传输优先。`goroutine` + `io.Reader/Writer` + `io.Pipe` 是这类问题的最佳形态，配 `errgroup` / `context` 写出来的代码就是优雅的反压模型。
- **两者通过 Kafka 解耦**：控制面 produce 任务，数据面 consume；数据面 produce 结果事件，控制面 consume 后落库。任意一边横向扩展不影响另一边。

## 替代方案

### A. 全 Python（FastAPI + asyncio worker）
- ✅ 单语言、单技术栈，运维简单
- ❌ 大文件流式 + 数千并发 + 多 sink 适配下，asyncio 的心智成本和性能瓶颈都比 Go 高
- ❌ 写多 sink adapter 时，Python 缺少 Go 那种"`io.Reader` 串流"的优雅模式
- 结论：MVP 可以全 Python，**Phase 2 必须拆**——所以索性从一开始规划清楚

### B. 全 Go
- ✅ 性能、并发模型最强
- ❌ 业务逻辑层（规则引擎、ORM、Pydantic 校验、配置 UI 后端）写起来啰嗦 3-5 倍
- ❌ 团队/学习/迭代速度都会受影响

### C. Rust 数据面
- ✅ 性能更强，借用检查器在传输管道这种场景里能预防一些 bug
- ❌ 学习曲线 / 生态成熟度（尤其是云 SDK）不如 Go
- ❌ 项目目标是"贯穿主流后端栈"，Go 更对口

## 后果

### 好的
- 双语言本身是简历加分项（异构选型 + 跨语言 trace 故事）
- 各自用最适合的语言写最适合的事
- Kafka 解耦让两边可独立横向扩

### 不好的
- **运维复杂度 +1**：两套构建、两套 CI、两套依赖管理
- **跨语言序列化**：任务消息格式必须严格定义（Protobuf 或 JSON Schema），不能两边各自飘
- **trace context 透传**：W3C trace context 通过 Kafka header 传递，需要主动接 OpenTelemetry SDK 两次

### 反悔成本
- 想退回单语言：把 Go 的 sink/source/pipeline 用 Python asyncio + httpx/aiohttp 重写——大约 1-2 周工作量。
- Sink 接口设计如果保持稳定，重写代价主要是工程量，不是设计返工。

## 相关 ADR
- 0002 Kafka vs RabbitMQ（待写）：解释为什么选 Kafka 做跨语言桥
- 0003 Sink 不暴露分阶段 API（待写）：在两种语言下都成立的接口设计原则
