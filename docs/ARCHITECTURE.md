# Architecture（详细设计）

> 这是 [BLUEPRINT.md](../BLUEPRINT.md) 的补充说明文件，记录架构层面的细节决策与图示。
> Phase 1 之后逐步填充。

## 目录
- [写路径详细时序图](#写路径详细时序图)
- [读路径详细时序图](#读路径详细时序图) — Phase 6.5 后写
- [关键不变量与一致性边界](#关键不变量与一致性边界) — Phase 6.5 后写
- [失败模式与恢复策略](#失败模式与恢复策略) — Phase 4 后写

## 当前状态
**v1**：Phase 2 已完成。Go 数据面通过统一 transport 接口支持 file-spool 和 Kafka；本地默认 file-spool，Kafka adapter 已通过 Docker broker 集成测试。当前 sink 支持 mock 与 S3 / MinIO 单段 PUT，结果事件可回写控制面 task / item 状态。

## 写路径详细时序图

当前 Phase 2 写路径：

```text
HQ user
  │
  ▼
control-plane FastAPI
  │  POST /tasks/{id}/upload
  │
  ├─ delivery_backend=python
  │    └─ 保留 Phase 1 Python 直传路径
  │
  └─ delivery_backend=go-worker
       │
       ├─ 查询 task / task_item
       ├─ 过滤 upload_status=pending 且 severity=ok/warning 的 item
       ├─ 构建 DeliveryTaskMessage
       ├─ delivery_transport=file:
       │    └─ 写入 /tmp/auto_upload_outbox/delivery.tasks.v1/{task_id}.json
       ├─ delivery_transport=kafka:
       │    └─ publish delivery.tasks.v1
       └─ task.status -> queued

Go data-plane worker
  │
  ├─ file-spool / Kafka transport 读取 delivery.tasks.v1
  ├─ transport decode -> DeliveryTask
  ├─ pipeline.ProcessTask
  │    ├─ FileSource(temp_dir, src_path)
  │    └─ Sink.Upload（mock 或 S3/MinIO 单段 PUT）
  └─ file-spool / Kafka transport 写入 delivery.results.v1

control-plane result consumer
  │
  ├─ 读取 delivery.results.v1
  ├─ apply_delivery_result()
  └─ 回写 task / task_item 状态
```

后续目标：
- 为 S3 / MinIO sink 补 multipart、resume 和平台层 dedup。
- 在真实负载下补 worker 并发调度、backpressure 和重试策略。
