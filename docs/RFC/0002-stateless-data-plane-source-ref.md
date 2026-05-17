# RFC 0002 — Stateless data-plane 与 source reference 迁移

| 字段 | 内容 |
|---|---|
| Status | Draft |
| Date | 2026-05-17 |
| Related | RFC 0001, Phase 2, Phase 3 |

## Problem

Phase 2 已经把上传执行从 Python control-plane 拆到 Go data-plane，并通过 file-spool / Kafka transport 连接两边。

当前任务消息仍携带本地路径：

- `DeliveryTask.temp_dir`
- `DeliveryItem.src_path`

Go worker 通过 `temp_dir + src_path` 打开文件。这对单机开发和本地桥接足够，但会阻碍后续 worker 集群化：

- worker 必须和 control-plane 共享同一份本地文件系统。
- Kafka 可以横向分发任务，但任意 worker 不一定能读到对应文件。
- worker 实例重启、迁移、跨机器部署时，任务输入不具备 durable reference。
- 本地临时目录的生命周期和消息重试语义不一致。

## Goals

- data-plane worker 可以无状态横向扩展。
- 任意 worker 实例只依赖任务消息和共享对象存储即可执行任务。
- Kafka consumer group 可以安全分配任务给多个 worker。
- 源文件引用具备持久化、可重试和可审计语义。
- 保留 file-spool 作为本地 transport，但不再依赖本地路径作为生产输入模型。

## Non-Goals

- 本 RFC 不实现 S3 multipart / resume。
- 本 RFC 不引入 Kubernetes / Service Mesh。
- 本 RFC 不改变 classification profile 的业务规则。
- 本 RFC 不要求立刻删除 Phase 2 的 `temp_dir` 兼容字段。

## Decision

引入 source reference 模型。

control-plane 在创建或确认任务时，把源文件放入 durable object storage，例如 MinIO / S3 staged bucket。任务消息不再要求 worker 读取 control-plane 的本地临时目录，而是携带可被任意 worker 解析的 source reference。

建议消息形态：

```json
{
  "schema_version": 2,
  "task_id": "task_xxx",
  "source": {
    "type": "object",
    "bucket": "auto-upload-staging",
    "key": "staged/tasks/task_xxx/archive.zip",
    "sha256": "optional",
    "size": 123456
  },
  "items": [
    {
      "item_id": "item_xxx",
      "source_path": "acme/report.xlsx",
      "dst_path": "reports/monthly/report.xlsx",
      "severity": "ok",
      "upload_status": "pending"
    }
  ]
}
```

`source_path` 表示 archive 内路径或 staged object 内的逻辑路径，不再表示 control-plane 本地文件路径。

## Proposed Flow

```text
HQ upload
  ▼
control-plane
  ├─ store original archive or extracted files to staging object storage
  ├─ classify and persist task / task_item
  ├─ publish delivery.tasks.v2 with source reference
  ▼
Kafka delivery.tasks.v2
  ▼
data-plane worker group
  ├─ consume task
  ├─ fetch source from staging object storage
  ├─ open item stream by source_path
  ├─ upload to target sink
  └─ publish delivery.results.v1 or v2
  ▼
control-plane result consumer
  └─ update task / item status
```

## Migration Plan

1. Add message model v2 while keeping v1 compatibility.
2. Add a `SourceResolver` abstraction in data-plane.
3. Implement object-storage source resolver for MinIO / S3 staging objects.
4. Add control-plane staging upload before task publication.
5. Publish v2 messages when `DELIVERY_SOURCE_MODE=object`.
6. Keep v1 `temp_dir` path for local fallback during migration.
7. Add integration test: control-plane stages source -> Kafka task -> multiple worker-compatible object source -> result apply.
8. After v2 is stable, deprecate `temp_dir` from production path.

## Semantics

- Kafka remains at-least-once.
- Worker commits task offset only after result event is produced.
- Control-plane commits result offset only after DB state is updated.
- Duplicate task execution must be tolerated by idempotent sink keys and DB state updates.
- Staged source objects require retention / GC policy independent from local temp directory cleanup.

## Alternatives

| 方案 | 结论 |
|---|---|
| 共享 NFS / volume | 简单，但把 worker 集群绑定到共享文件系统，故障域和部署复杂度更高 |
| 把文件 bytes 放进 Kafka | 不合适；Kafka 不应承载大文件 payload |
| 每个 worker 访问 control-plane HTTP 下载 | 可行，但 control-plane 会重新进入大文件数据路径 |
| Object storage source reference | 推荐；和现有 MinIO / S3 sink 经验一致，适合重试、审计和横向扩展 |

## Consequences

正向影响：

- data-plane worker 可以跨机器、跨容器横向扩展。
- Kafka consumer group 的扩展能力真正可用。
- 源文件生命周期从临时目录变成可管理的 staging object。
- 后续 multipart、resume、dedup 更容易落到统一对象模型上。

代价：

- 需要维护 v1 / v2 消息兼容。
- control-plane 需要新增 staging 写入和 GC 逻辑。
- data-plane 需要新增 source resolver 和对象存储读取路径。
- 测试需要覆盖 staging object、Kafka、worker、result consumer 的跨组件闭环。

## Acceptance Criteria

- 不共享本地目录的情况下，Go worker 可以处理 control-plane 发布的任务。
- 至少两个 worker 实例使用同一个 consumer group 时，任务可被任意 worker 执行。
- worker 重启后，未 commit 的任务可以重新消费并从 staged source 恢复读取。
- result consumer 能继续回写 task / item 状态。
- v1 file-spool 本地开发路径在迁移期仍可用。
