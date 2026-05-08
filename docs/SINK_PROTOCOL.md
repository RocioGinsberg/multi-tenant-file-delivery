# Sink Protocol（接口规范 + 各 adapter 协议说明）

> 详细记录每个 sink adapter 的协议适配细节。
> 每加一个 sink，在这里补一节。

## 通用接口

参见 BLUEPRINT § 4.1 / § 4.2 / § 4.3。

```go
type Sink interface {
    Name() string
    Capability() Capability
    Upload(ctx context.Context, src Source, meta Meta) (Receipt, error)
    HealthCheck(ctx context.Context) error
    Close() error
}
```

## 各 Sink 协议

### SMH / 腾讯 COS（Phase 1）
- 三段协商上传：`noHash → beginningHash → fullHash`
- 命中任意阶段 `200/201` 即返回 `isInstantUpload`
- 未命中 → 返回 `domain` + `path` + `headers`，PUT 到 COS
- 完成后 `confirmKey` 调 confirm 接口
- 详见 `control-plane/_legacy/smh_v0_cosdrive.py` 与 `_legacy/smh_uploader/api_client.py`

### S3 / MinIO（Phase 7）
- 待写

### 阿里云 OSS（Phase 7）
- 待写

### Webhook / SFTP（Phase 7+）
- 待写
