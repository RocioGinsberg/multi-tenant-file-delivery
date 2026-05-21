# RFC 0003 — Kafka retry / DLQ / idempotency semantics

| 字段 | 内容 |
|---|---|
| Status | Accepted |
| Date | 2026-05-18 |
| Related | RFC 0001, RFC 0002, Phase 3.x |

## Problem

Phase 3.x 已验证 control-plane 可以通过 Kafka 发布 source reference task，Go worker 可以从 staging object storage 读取源文件并把 result 写回 Kafka。

Kafka transport 是 at-least-once 语义，因此生产化前必须明确：

- task message 什么时候 ack。
- worker 失败时如何区分可重试和不可重试错误。
- result message 重复消费时 control-plane 如何保持最终状态稳定。
- 什么时候需要 DLQ，而不是无限重试。

## Decision

当前阶段采用保守语义：

- data-plane worker 只有在 result event 成功写入 Kafka 后，才 commit task offset。
- control-plane result consumer 只有在 DB 状态更新成功后，才 commit result offset。
- worker 对单个 item 的业务失败写入 result item failed，不把整个 Kafka message 直接丢弃。
- sink key 必须由 task/item/dst_path 稳定推导，重复执行同一 task 时目标对象 key 不漂移。
- control-plane result apply 必须允许重复 result 进入，最终 task/item 状态保持稳定。

## Error Classes

| 类型 | 例子 | 处理 |
|---|---|---|
| Invalid message | JSON 不合法、缺 task_id、schema 不兼容 | 写入 DLQ，DLQ 写入成功后 ack 原 task |
| Missing source | staging object 不存在、zip 内 source_path 不存在 | 产出 failed result item |
| Transient infra | Kafka/S3 暂时不可用、网络超时 | 不 ack，依赖 Kafka 重新投递 |
| Sink business failure | 目标权限、路径非法、对象存储拒绝 | 产出 failed result item |
| Worker bug/panic | 程序异常退出 | 不 ack，依赖重启和重新投递 |

## DLQ Plan

DLQ 在 Phase 3.x 先实现最小闭环：

- 新增 `delivery.tasks.dlq.v1` topic。
- DLQ payload 包含原始 message、error_class、error_message、worker_id、failed_at、原 task topic/key。
- 只把不可恢复的 message 级错误写入 DLQ；当前实现覆盖 invalid JSON。
- DLQ 写入成功后才 commit 原 task offset；DLQ 写入失败时保留原 task 未 ack，依赖 Kafka 重投。
- item 级失败仍走 normal result topic，便于 control-plane 展示 partial_failed。

## Acceptance Criteria

- source reference Kafka e2e 覆盖 task/result 双向 Kafka。
- duplicate result apply 有回归测试，最终 DB 状态稳定。
- staged source 有 retention cleanup，避免 Kafka 重试之外的对象泄露。
- invalid JSON task 写入 `delivery.tasks.dlq.v1` 后 commit 原 task offset。
