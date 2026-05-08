# ADR 0010 — 项目起源叙事 & 首版后端选 S3/MinIO

- **状态**：Accepted（2026-05）
- **决策者**：Rocio
- **影响范围**：项目定位、首版 sink 选择、整体叙事

## 背景

本项目源自一份在职期间的小型工具脚本 `smh_uploader/`：
- 公司用腾讯企业网盘（SMH，本质是腾讯云 COS 之上的协作产品）做总部 → 子公司文件分发
- 订阅成本高、自动化能力弱（手工上传 / 拖拽 / 点击）
- 我抓包逆向了其 RESTful API，写了一个 Python CLI 自动化批量上传，期间踩过：
  - 三段 hash 协商上传（content-addressed dedup 协议）
  - 大文件 multipart 分块
  - 嵌套并发（团队级 × 文件级）+ 目录创建去重缓存
  - 断点续传与失败重试
- 这套 CLI 即 `control-plane/_legacy/smh_uploader/`

离职后我希望把这段经验整理成一个完整的、能体现后端能力的项目，同时考虑：
- 不能再依赖 SMH（闭源、需订阅、本地不易起）
- 但抓包学到的设计模式（流式、内容寻址 dedup、协议适配、嵌套并发）非常通用
- 要让"项目起源故事"能在面试中自然展开，而不是堆技术栈

## 决策

### A. 项目叙事

把 SMH 定位为：**项目灵感来源 + 未来可选扩展的一种 sink**，而不是首版要支持的对端。

讲法（也是 README 顶部要写的版本）：

> 工作中要把总部文件批量分发给数十家子公司，公司原方案是腾讯企业网盘（SMH），订阅成本高、自动化能力弱。我抓包逆向了它的 RESTful API，写了个自动化批量上传 CLI 替代手工操作，期间踩过三段 hash 协商、大文件 multipart、并发限速、断点续传等坑。
>
> 离职后我意识到这套"流式上传 + 内容寻址 dedup + 异构 sink 适配"的模式不是 SMH 独有的——任何"总部 → 多子公司分发"场景都需要。于是我把它做成一个通用对象存储为后端的多租户文件分发平台：首版用 S3/MinIO，未来插拔 OSS/SFTP/webhook 都不动核心。

这个故事让以下设计决策**有真实经历支撑**而不是空抽象：
- "为什么平台层做 dedup？" → 抓包发现 SMH 这么做，但 S3/OSS 都不做，提到平台层才一致
- "为什么 Sink 接口不暴露分阶段？" → 写过三段协商也写过 S3 单段，强抽是漏抽象
- "为什么要 Capability 矩阵？" → 亲历三种协议差异才有的设计
- "为什么 Workspace 不依赖对端权限？" → SMH 内置、S3 没有、OSS 又是另一套，必须我们这层做权威

### B. 首版 sink = S3 / MinIO

理由：
1. **本地能起**：MinIO 单 docker container 即可跑，开发摩擦最小
2. **协议主流**：S3 API 是事实标准，AWS / 阿里 OSS / 腾讯 COS / GCS / 各大云存储都兼容或部分兼容
3. **生态资源最丰富**：aws-sdk-go-v2 / boto3 / minio-go 都成熟
4. **特性覆盖**：multipart、presigned URL、IAM-based 权限、ETag、生命周期管理都齐全，能驱动整个项目骨架的设计
5. **没有内置 dedup**：反而成为优势——逼着平台层做 dedup（§ 6.10 Stage 1），这是简历最大的故事

### C. 历史代码定位

`control-plane/_legacy/`：
- `smh_uploader/`（v0 CLI）—— 保留作为**通用流式上传 + 嵌套并发**的参考实现；Phase 1 移植到 Python S3 sink 时直接借鉴 `api_client.py` 和 `uploader.py` 的设计模式
- `smh_v0_cosdrive.py`（cosdrive 半成品的 SMH 客户端）—— 保留作为协议参考；Phase 7 stretch 真做 SMH adapter 时复用

Phase 1 完成后**不删** `_legacy/`：它们是项目起源故事的实物证据，对面试叙事有价值。

## 替代方案

### A1. 首版直接做 SMH（保持业务连续性）
- ❌ 闭源协议，需要订阅腾讯云账号才能跑通
- ❌ 本地无法 demo（无 staging 环境）
- ❌ 项目"通用性"卖点立不住——成了"为某家闭源服务定制的 CLI"
- 拒绝

### A2. 首版做 GCS / Azure Blob
- 部分账号注册流程复杂；国内访问受限
- 拒绝

### A3. 首版做阿里云 OSS（国内主流）
- ✅ 国内开发友好
- ❌ SDK 风格偏离 S3 标准（不能复用 aws-sdk）
- ❌ 本地起 mock 不如 MinIO 简单
- 留给 Phase 7

### A4. 首版做"纯本地文件系统"
- ✅ 零依赖，最快上手
- ❌ 没有 multipart / presigned URL / IAM 等关键概念，整个 § 6.11 / § 6.9 立不住
- 拒绝

## 后果

### 好的
- **项目叙事完整**：从抓包到通用化，每个抽象都有实战来源
- **本地易跑**：MinIO 一行 docker 命令起来
- **面试可深讲**：S3 multipart、presigned URL、IAM 都是高频面试题，且每个都能扯到本项目的具体实现
- **未来扩展自然**：Phase 7 加 OSS / SMH / SFTP 都是骨架不动只加 adapter
- **`_legacy/` 仍有价值**：作为故事实物证据 + Phase 7 stretch 时的协议参考

### 不好的
- **首版没有"内置秒传"对比基线**：要等到 Phase 7 stretch 接 SMH 才能做"平台 dedup vs sink 内置 dedup"对比；如果不做 stretch，这块缺一组数字
- **§ 4.2 Capability 矩阵首版只有一种 sink**：`SupportsInstantUpload = false` 看起来像个空字段；mock sink 可以补一个 `true` 的实现来证明矩阵确实驱动调度

### 反悔成本
- 想加回 SMH 作为首版 sink：协议代码已存在 `_legacy/smh_v0_cosdrive.py` 和 `_legacy/smh_uploader/`，封成 Sink interface 大约 1-2 天
- Sink 接口设计本身因为这次决策**反而被验证为正确**——它在 S3 / SMH / 未来 OSS 三种协议下都不需要改

## 相关 ADR
- 0001 双语言架构 —— 与本 ADR 共同定义项目起点
- 0003 Sink 不暴露分阶段 API（待写）—— 由本决策验证
- 0006 Dedup 范围限定 (owner_tenant_id, sink_id)（待写）—— 平台层 dedup 的具体边界
