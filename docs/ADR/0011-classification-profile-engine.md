# ADR 0011 — Classifier Core 与业务 Profile 分层

- **状态**：Accepted（2026-05）
- **决策者**：Rocio
- **影响范围**：分类器、规则配置、task_item 字段命名、未来 registry/workspace 接入

## 背景

旧版 `control-plane/_legacy/smh_uploader/classifier.py` 来自真实业务场景：总部按文件名里的团队名和描述，把文件分到某个企业网盘团队空间下的业务目录。它证明了“上传前规则分类”是写路径里的关键能力，但旧实现同时固化了几个具体业务假设：

- 文件名最后一段就是团队或子公司。
- 团队别名、任务描述、业务类别、上传目录都写在同一套配置里。
- 输出路径默认是某个企业网盘里的目录。
- 规则优先级由 Python 代码顺序隐式决定。

项目现在的目标不是复刻某个公司的企业网盘目录，而是做通用“总部 → 多接收方”的文件分发平台。分类器需要保留旧业务里的经验，但不能让“团队/任务/网盘路径”成为平台核心语义。

## 决策

分类能力拆成两层：

1. **Classifier Core / Engine**：平台核心代码，只负责读取文件、抽取 facts、执行 profile、生成 `ClassifiedItem` 与 summary，并强制安全约束。
2. **Classification Profile**：业务适配包，定义文件归属规则、描述映射、字典、优先级、路径模板和错误策略。

Core 不直接编码“团队”“订单”“考核”“网盘目录”等业务词。Core 使用通用语义：

- `target`：文件投递目标。Phase 1 是匹配出的接收方 key/name；未来映射到 `workspace`。
- `document_type`：文件类型或业务文档类型。
- `category`：文档类型的上层分类。
- `dst_path`：目标 workspace 内的相对路径，不包含 sink 物理语义。

Profile 是版本化业务包。Phase 1 只加载一个本地静态 profile，不做 draft/publish，不做 UI 配置台，不执行任意 Python 插件。未来 `registry_version.config_json` 保存 profile 内容，发布流程和 UI 留到 Phase 6.5+。

默认 Phase 1 profile 可以继续表达旧业务场景，例如“描述 - 接收方.ext”的文件名解析策略和 `{category}/{document_type}/{filename}` 的路径模板。但这些属于 profile，不属于 classifier core。

## 替代方案

### A. 直接移植旧 classifier

- ✅ 最快跑通原业务 demo。
- ❌ 继续固化团队/任务/网盘路径语义，违背通用平台定位。
- ❌ 未来接 workspace、registry、非企业网盘 sink 时要重构核心代码。
- 拒绝。

### B. 立刻做完整规则引擎或 Python 插件系统

- ✅ 表达能力最强。
- ❌ 安全边界复杂，业务插件能执行任意代码。
- ❌ 依赖隔离、版本管理、调试和 UI 都会拖慢 Phase 1。
- 拒绝；Phase 1 只做 JSON profile + 固定 engine。

### C. 只把 team/drive 改名，规则仍写死在 classifier.py

- ✅ 改动小。
- ❌ 只是表面通用，优先级和归属规则仍被代码固定。
- 拒绝。

## 后果

### 好的

- 业务语义被隔离在 profile 中，项目不会被单一公司场景锁死。
- 1.5 的 TDD 可以直接验证 engine/profile 契约，而不是只验证旧业务样例。
- 未来 registry draft/publish 可以自然保存 profile，而不需要重新设计分类器。
- `target_key` 将来可以映射到 `workspace_id`，不需要把接收方硬编码进路径。

### 不好的

- Phase 1.5 比“移植旧代码”多一层 profile schema 设计。
- 当前 `task_item` 字段需要从 `team/drive/task` 语义改为 `target/dst/document_type`，会触及模型、迁移和测试。
- 由于 Phase 1 不做 registry UI，profile 仍是本地配置文件，非技术用户暂时不能在线编辑。

### 反悔成本

- 如果只想回到旧业务分类器，可以提供一个 legacy profile，文件名解析和路径模板仍能复刻旧行为。
- 如果未来 JSON profile 表达力不够，可以在 Phase 6.5+ 引入受控插件协议，但必须先有 profile schema 和版本管理。

## 相关文档

- [ADR 0010 — 项目起源叙事 & 首版后端选 S3/MinIO](0010-pivot-to-generic-object-storage.md)
- [PDR](../PDR.md)
- [ARCHITECTURE](../ARCHITECTURE.md)
- [Phase 1 计划](../plans/phase-1-python-mvp.md)
