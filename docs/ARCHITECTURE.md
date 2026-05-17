# Architecture（详细设计）

> 这是 [BLUEPRINT.md](../BLUEPRINT.md) 的补充说明文件，记录架构层面的细节决策与图示。
> Phase 1 之后逐步填充。

## 目录
- [写路径详细时序图](#写路径详细时序图)
- [读路径详细时序图](#读路径详细时序图) — Phase 6.5 后写
- [关键不变量与一致性边界](#关键不变量与一致性边界) — Phase 6.5 后写
- [失败模式与恢复策略](#失败模式与恢复策略) — Phase 4 后写

## 当前状态
**v1**：Phase 2 本地 outbox bridge 已落地。当前写路径还不是 Kafka，也没有真实 sink；Go 数据面先通过本地 JSON 文件完成控制面到 worker 的可运行闭环。

## 写路径详细时序图

当前 Phase 2 本地 bridge：

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
       ├─ 写入 /tmp/auto_upload_outbox/delivery.tasks.v1/{task_id}.json
       └─ task.status -> queued

Go data-plane worker
  │
  ├─ 扫描 delivery.tasks.v1 inbox
  ├─ json.Unmarshal -> DeliveryTask
  ├─ pipeline.ProcessTask
  │    ├─ FileSource(temp_dir, src_path)
  │    └─ Sink.Upload
  └─ 写入 delivery.results.v1/{task_id}.json
```

后续目标：
- 用 Kafka 替换本地目录扫描。
- 用真实 S3 / MinIO sink 替换 mock sink。
- 控制面消费 `delivery.results.v1` 后统一更新 task / item 状态。
