# Data Plane（Go）

高并发流式文件投递、多 sink 协议适配、平台层 dedup precheck。

## 当前状态
**Phase 2 scaffolded**。当前已具备 worker 入口、任务消息模型、文件 source、mock sink 和本地 outbox bridge。

## 目录
```
cmd/worker/           main 入口
internal/
  message/            delivery task/result JSON schema
  sink/               Sink interface + mock sink
  source/             File source
  pipeline/           文件处理编排
  worker/             inbox -> sink -> result 的 worker 主循环
```

## 接口规范
参见 [../docs/SINK_PROTOCOL.md](../docs/SINK_PROTOCOL.md)。

## 启动方式
```bash
cd data-plane
go run ./cmd/worker
```
