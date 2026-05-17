# Data Model

> 本文记录目标态数据模型。当前 Phase 1/2 只实现了任务链路的简化表；Phase 3 之后逐步迁移到真实数据库并补齐多租户、workspace、dedup、审计。

## 当前实现

控制面当前使用 SQLAlchemy 模型维护任务闭环：

| 表 | 用途 |
|---|---|
| `task` | 分发任务主表，记录状态、源归档、分类摘要、确认和完成时间 |
| `task_item` | 单文件投递项，记录源路径、目标、目标路径、sink 状态和错误 |
| `task_event` | 任务事件流，用于审计状态变化和调试 |

Phase 2 增加了 Go data-plane result consumer，数据面写出 `delivery.results.v1` 后由控制面消费并回写 `task` / `task_item`。

## 目标态模型

### 身份与租户

| 表 | 说明 | 关键字段 |
|---|---|---|
| `tenant` | HQ 和子公司租户 | `id`, `name`, `type`, `parent_tenant_id` |
| `user` | 用户，属于某个租户 | `id`, `tenant_id`, `email`, `role` |
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
| `task` | 分发任务 | `id`, `owner_tenant_id`, `user_id`, `status`, `registry_version_id`, `idempotency_key` |
| `task_item` | 单文件投递项 | `id`, `task_id`, `src_path`, `target_workspace_id`, `dst_path`, `file_hash`, `delivery_status`, `sink_name` |
| `task_event` | 状态事件流 | `id`, `task_id`, `attempt_id`, `event_type`, `payload`, `created_at` |

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

## Phase 3 关注点

- SQLite 迁移到 MySQL 或 PostgreSQL。
- Alembic migration 成为 schema 变更唯一入口。
- 本地开发 compose 需要提供真实数据库。
- 测试保留 SQLite 还是使用容器数据库，需要在 Phase 3 RFC 中确认。
