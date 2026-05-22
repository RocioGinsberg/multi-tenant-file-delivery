# Data Model

> 本文记录目标态数据模型。当前 Phase 1/2 只实现了任务链路的简化表；Phase 3 之后逐步迁移到真实数据库并补齐多租户、审计，以及后续 workspace / dedup 扩展表。

## 当前实现

控制面当前使用 SQLAlchemy 模型维护任务闭环和 Phase 6 身份基线：

| 表 | 用途 |
|---|---|
| `tenant` | HQ / subsidiary 租户基线 |
| `app_user` | 平台内置用户基线，记录所属租户和角色 |
| `task` | 分发任务主表，记录 owner tenant/user、状态、源归档、分类摘要、确认和完成时间 |
| `task_item` | 单文件投递项，记录源路径、目标、目标路径、sink 状态和错误 |
| `task_event` | 任务事件流，用于审计状态变化和调试；关键写操作在 payload 中记录 actor attribution |

Phase 2 增加了 Go data-plane result consumer，数据面写出 `delivery.results.v1` 后由控制面消费并回写 `task` / `task_item`。Phase 3 / 3.x 已把 MySQL 作为本地 compose 主数据库目标，并通过 source reference 让 worker 从 staging object storage 读取源文件。Phase 4 / 5 新增 Redis 能力层和 observability，不改变当前业务表结构。

Phase 6 已把 `tenant` / `app_user` / `role` 基线、task owner tenant/user 和 repo tenant filter 落到控制面写路径。Workspace、dedup、sink credential 加密和完整 audit_log 仍留到 Phase 6.5 / Phase 7。

## 目标态模型

### 身份与租户

| 表 | 说明 | 关键字段 |
|---|---|---|
| `tenant` | HQ 和子公司租户 | `id`, `name`, `type`, `parent_tenant_id` |
| `app_user` | 平台用户，属于某个租户 | `id`, `tenant_id`, `email`, `role` |
| `sink_credential` | sink 凭证，仅 HQ 持有 | `id`, `tenant_id`, `sink_type`, `encrypted_blob` |

### 分发规则与 Workspace

| 表 | 说明 | 关键字段 |
|---|---|---|
| `registry_version` | 分类规则版本 | `id`, `tenant_id`, `version_no`, `status`, `config_json` |
| `workspace` | 平台逻辑容器，由 HQ 拥有，服务一个子公司 | `id`, `name`, `owner_tenant_id`, `target_tenant_id` |
| `workspace_sink_binding` | workspace 到物理 sink 的绑定 | `workspace_id`, `sink_id`, `root_path` |
| `physical_object` | 物理字节，内容寻址 dedup 的核心表 | `id`, `owner_tenant_id`, `sink_id`, `sink_path`, `size`, `hash`, `ref_count` |
| `workspace_object` | workspace 内的逻辑文件 | `id`, `workspace_id`, `physical_object_id`, `display_name`, `uploaded_by_user_id`, `uploaded_at` |
| `multipart_session` | 大文件分块上传和断点续传 | `id`, `task_item_id`, `sink_id`, `sink_session_id`, `part_size`, `completed_parts_json`, `expires_at` |

### 写路径任务

| 表 | 说明 | 关键字段 |
|---|---|---|
| `task` | 分发任务 | `id`, `owner_tenant_id`, `owner_user_id`, `status`, `registry_version_id`, `idempotency_key` |
| `task_item` | 单文件投递项 | `id`, `task_id`, `src_path`, `target_workspace_id`, `dst_path`, `file_hash`, `delivery_status`, `sink_name` |
| `task_event` | 状态事件流 | `id`, `task_id`, `attempt_id`, `event_type`, `payload`, `actor_user_id`, `actor_tenant_id`, `actor_role`, `created_at` |

### 审计与通知

| 表 | 说明 | 关键字段 |
|---|---|---|
| `audit_log` | 所有读写操作审计 | `id`, `actor_user_id`, `actor_tenant_id`, `action`, `resource_type`, `resource_id`, `ts`, `ip` |
| `notification` | 子公司通知 | `id`, `recipient_tenant_id`, `workspace_id`, `payload`, `delivered_at` |

## 关键约束

- `workspace.target_tenant_id` 隐含子公司读权限，不单独建 `workspace_share`。
- 仓储层必须强制 `tenant_id` / `owner_tenant_id` / `target_tenant_id` 过滤；是否使用数据库 RLS 另行 ADR 决策。
- `task.idempotency_key` 防止重复提交。
- `task_item` 应对 `(task_id, src_path)` 建唯一约束，避免同一任务重复项。
- `physical_object` 应对 `(owner_tenant_id, sink_id, hash, size)` 建唯一约束。
- Dedup 范围限制在 `(owner_tenant_id, sink_id)` 内，避免跨租户数据泄露和跨 sink 语义不一致。
- `workspace_object` 删除只减少 `physical_object.ref_count`；当引用数为 0 时标记 orphan，由后台 GC 异步删除 sink 字节。
- `sink_credential.encrypted_blob` 必须加密存储，主密钥来自环境变量或密钥管理服务。
- `audit_log` 写入应走异步链路，避免阻塞主写路径。
- `multipart_session.expires_at` 用于清理超期未完成 multipart session，避免对端长期计费。

## Phase 6 落地情况

- 已新增 `tenant` / `app_user` 基线表，并把 HQ / subsidiary 角色落到可测试模型。
- `task` 已持久化 owner tenant/user；`task_item` / `task_event` 通过 task join 做 tenant-aware 查询。
- 默认开发 actor 只用于本地测试兼容，生产模式需要显式身份。
- task_event payload 已覆盖 create / classify / confirm / upload / retry / queue 等关键写操作的 actor attribution；完整读审计随 Phase 6.5 子公司读视图补齐。
- Workspace / `workspace_object` / `physical_object` / dedup 不进入 Phase 6 主线，避免把多租户鉴权和读路径模型合并成一个过大的阶段。
