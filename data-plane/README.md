# Data Plane（Go）

高并发流式文件投递、多 sink 协议适配、平台层 dedup precheck。

## 当前状态
**Phase 0 骨架**。Phase 2 起开始填充。

## 目录
```
cmd/worker/           main 入口
internal/
  sink/               Sink interface + 各 adapter
    smh/              腾讯 SMH/COS（三段协商）
    s3/               S3 / MinIO（multipart）
    oss/              阿里云 OSS
    webhook/          HTTP webhook
    mock/             压测/测试
  source/             File / S3 / Memory / RemoteURL Source 实现
  pipeline/           io.Pipe 编排
  ratelimit/          Redis 令牌桶 + AIMD
  kafka/              consumer
  progress/           Redis pub/sub
  resume/             multipart 断点续传持久化
  observability/      OTel + Prometheus
```

## 接口规范
参见 [../docs/SINK_PROTOCOL.md](../docs/SINK_PROTOCOL.md)。

## 启动方式
（Phase 2 完成后填）
