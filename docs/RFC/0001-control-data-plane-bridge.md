# RFC 0001 — 控制面 / 数据面分离与消息桥接

| 字段 | 内容 |
|---|---|
| Status | Accepted |
| Date | 2026-05-17 |
| Related | ADR 0001, Phase 2 |

## Problem

上传链路既包含业务编排，也包含高并发文件传输：

- 控制面需要快速迭代分类规则、状态机、API 和配置。
- 数据面需要处理高并发 I/O、流式上传、sink 协议适配和后续 backpressure。

如果全部放在 Python 控制面，后续大文件传输和多 sink 适配会拖累业务层；如果全部放在 Go，又会降低业务规则迭代效率。

## Decision

采用双平面架构：

- Control Plane：Python + FastAPI，负责业务状态、分类、确认、发布任务和消费结果。
- Data Plane：Go worker，负责读取文件、调用 sink、生成上传 receipt。
- Message Contract：JSON `delivery.tasks.v1` / `delivery.results.v1`。
- Transport：
  - file-spool：默认本地开发路径。
  - Kafka：生产形态和横向扩展路径。

## Message Flow

```text
control-plane
  ├─ build DeliveryTaskMessage
  ├─ publish delivery.tasks.v1
  ▼
data-plane worker
  ├─ consume task
  ├─ source.Open()
  ├─ sink.Upload()
  ├─ produce DeliveryResultMessage
  ▼
control-plane
  ├─ consume delivery.results.v1
  └─ apply_delivery_result() -> task / task_item
```

## Semantics

- Kafka 消费使用至少一次语义。
- Go worker 处理成功并写出 result 后再 ack/commit task offset。
- Control-plane result consumer 落库成功后再 commit result offset。
- 幂等最终依赖 DB 唯一约束和任务状态机。

## Alternatives

| 方案 | 结论 |
|---|---|
| 全 Python | MVP 可行，但不适合后续高并发数据面 |
| 全 Go | 业务规则和 API 迭代成本更高 |
| 只用 file-spool | 本地开发简单，但不支持横向扩展和消费组 |
| RabbitMQ | 可行；本项目选择 Kafka 是为了 partition、消费组和面试高频可解释性 |

## Consequences

正向影响：

- 控制面和数据面可以独立演进。
- Kafka / file-spool 通过同一 transport 接口切换。
- JSON 契约让 Python / Go 解耦。

代价：

- 需要维护跨语言消息模型。
- 需要显式处理 ack、重试、幂等和 trace context。
- 本地开发需要 Docker Kafka 才能验证真实 broker 路径。
