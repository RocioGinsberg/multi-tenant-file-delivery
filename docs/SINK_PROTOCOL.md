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

---

## 各 Sink 协议

### S3 / MinIO（Phase 1 首版 sink，Phase 2 移到 Go）

**当前实现状态（Phase 2）**：
- 已实现 Go `S3Sink` 单段 `PutObject`。
- 已支持 MinIO path-style endpoint 配置。
- 已接入 `cmd/worker -sink s3`。
- 单段上传 receipt 已返回 SHA-256，用作后续结果消息和平台层 dedup 的基础。
- 暂未实现 multipart、resume、平台层 dedup 和 DB 元数据写入。

**Capability**：
- `SupportsInstantUpload = false`（S3 没有内置 dedup；平台层 dedup 在 § 6.10 Stage 1 补）
- `SupportsResume = true`（multipart upload 支持 resume）
- `SupportsMultipart = true`
- `PrefersChecksum = "sha256"`（用于平台层 dedup；S3 自己 ETag 是 MD5 不能直接用）
- `MaxFileSize = 5 TiB`（S3 单对象上限）
- `MaxConcurrentParts = 8`

**单段 PUT（< multipartThreshold ~50MB）**：
- 直接 `PutObject`，body 用 `io.Reader` 流式
- 上传前流式计算 sha256，上传时重新打开 source，保持 AWS SDK 对 seekable body / SigV4 payload hash 的兼容性
- 上传完成 → 写 `physical_object` 表（hash, size, sink_path）

**Multipart（≥ threshold）**：
1. `CreateMultipartUpload` → 拿 `UploadId`
2. 把 `UploadId` + part 计划 写 `multipart_session` 表，便于断点续传
3. 用 `errgroup` + 并发信号量（4-8）并发上传 parts
   - 每个 part 用 `Source.OpenRange(offset, length)` 流式读
   - `UploadPart` → 拿 `ETag`
   - 写 part_no + ETag 到 session
4. 失败：**保留 session，不调 `AbortMultipartUpload`**——下次 resume 从未完成的 part 接着传
5. 全部成功 → `CompleteMultipartUpload`（按 part_no 排序提交所有 ETag）→ 删 session

**Presigned URL 下载（读路径）**：
- `s3.PresignClient.PresignGetObject(...)`，TTL 短（默认 5min）
- 控制面校验权限后返回 302 给浏览器，浏览器直连 S3，控制面不代理流量

**关键决策**：
- **不用 S3 IAM 做用户级权限**——所有用户级授权由我们这层判定，S3 凭证只是机器人账号
- **不依赖 S3 ETag 做 dedup**——因为 multipart 上传的 ETag 不是整文件 MD5（是各 part MD5 的 MD5），无法稳定识别同一份字节
- **MinIO 兼容**：endpoint 可配置（`https://minio.local:9000` 或 AWS endpoint），其他 API 100% 同 S3

---

### 阿里云 OSS（Phase 7）

**Capability**（计划）：
- 类似 S3，`SupportsMultipart = true`
- 阿里 OSS SDK 形态略不同（`oss.MultipartUploader`），但概念一致

详细协议待 Phase 7 实施时补。

---

### Webhook（Phase 7）

**Capability**：
- `SupportsInstantUpload = false`
- `SupportsResume = false`
- `SupportsMultipart = false`
- 一次性 stream → POST 到对端 endpoint

适合"投递事件 + 文件元数据 + 文件 URL"给下游 IM/工单系统。

---

### SFTP（未来）

**Capability**：
- `SupportsResume = true`（SFTP 协议支持 seek）
- `SupportsMultipart = false`
- 适合传统企业内网文件交换

---

### SMH / 腾讯企业网盘（未来扩展，Phase 7 stretch）

**项目灵感来源**：原始 v0 CLI（`control-plane/_legacy/smh_uploader/`）就是抓包逆向 SMH API 写的，启发了平台层 dedup（§ 6.10 Stage 1）和 Capability 矩阵（§ 4.2）的设计。

**Capability**：
- `SupportsInstantUpload = true` ← 三段协商秒传，是少数有内置 dedup 的对象存储
- `SupportsResume = false`
- `SupportsMultipart = false`（单段 PUT 到 COS 域名）

**协议要点**（仅记录，Phase 7 真做时再实现）：
- JWS（RS256）签名 → `userToken` → 每个 space `accessToken`（带缓存）
- `ensure_directory` 递归建目录（带去重缓存，已成功的不重复）
- 三段协商上传：
  - 阶段 1：filesize 空 payload PUT → 200/201 = 秒传 / 202 = 进入阶段 2
  - 阶段 2：补 first-64K hash → 200/201 = 秒传 / 202 = 阶段 3
  - 阶段 3：补 full hash → 拿到 `domain` + `path` + `headers`
- PUT 字节到 COS 域名（HTTPS）
- POST `confirmKey` 完成确认

**为什么不在首版做**：
- 闭源协议、依赖 SMH 订阅、本地不易起
- 设计模式（dedup、Capability、流式）已被 § 6.10/§ 4.2 抽象到平台层，**不做 SMH 也不影响项目完整性**
- 真做主要是 stretch goal：在 BENCHMARKS 里加一组"平台 dedup vs sink 内置 dedup"对比

详见 ADR 0010 — 后端选择决策。
