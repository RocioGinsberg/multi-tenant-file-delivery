# Phase 2 — Go 数据面

> 当前状态：实现中。Phase 1 的分类逻辑继续以 control plane 为准；Phase 2 只接管分类后的上传执行。

## Summary
- 控制面维持分类、确认、状态机和事件写入。
- 数据面负责消费 Phase 1 产出的任务消息，完成文件读取、sink 上传和结果回传。
- 本地开发先使用 outbox bridge 作为 Kafka 之前的可运行过渡层。

## Key Changes
- 新增任务消息模型：`delivery.tasks.v1` / `delivery.results.v1`。
- 新增 Go worker 主干：`cmd/worker`、`message`、`source`、`sink`、`pipeline`、`worker`。
- 控制面上传入口切到双模式：
  - `python`：保留 Phase 1 直传，便于回归。
  - `go-worker`：写入 outbox，交给 Go worker 消费。

## Test Plan
- 控制面单测：消息构建、outbox 写入、go-worker 模式路由。
- Go 单测：消息 JSON round-trip、mock sink pipeline。
- 集成验证：上传 zip -> 分类 -> 确认 -> outbox 生成 -> worker 消费 -> result 输出。

## Assumptions
- 分类实现继续参考 control plane 的 Phase 1 版本，不回退到 `_legacy` 业务耦合脚本。
- Kafka 主题命名先保留，但本地桥接先用文件 outbox，后续可平移为真实 Kafka producer/consumer。
