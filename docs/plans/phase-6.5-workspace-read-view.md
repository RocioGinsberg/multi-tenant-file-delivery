# Phase 6.5 — Workspace + 子公司读视图

> **状态**：Planned（等待 Phase 6 PR 合并后启动）
> **目标**：在 Phase 6 tenant / actor / RBAC 基线上补齐 workspace 元数据、投递结果映射和子公司只读浏览/下载路径，让平台形成最小完整产品闭环。
> **完成定义**：HQ 写路径上传成功后会生成 workspace_object 元数据；子公司 actor 只能列出 / 查看 / 下载自己租户可见的 workspace 文件；下载由控制面鉴权后签发短 TTL presigned URL；测试覆盖跨租户不可见、HQ 写入到 workspace、子公司读路径和默认 actor 兼容。
> **前序计划**：[Phase 6 — 多租户 + 鉴权](./phase-6-multitenancy-auth.md)

## Summary

Phase 6 已把 actor、tenant、role、task owner 和 repo tenant filter 落到控制面写路径。Phase 6.5 不再扩写路径权限模型，而是把“投递完成后子公司能看到什么”落成最小可用读视图。

本阶段只做 metadata-first 的 workspace 读路径：控制面保存 workspace / workspace_object / physical_object 元数据，子公司通过 API 浏览和下载；data-plane 仍负责把字节写入 sink，控制面根据 result receipt 建立读模型。

## 一、先决决策

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| D1 | Workspace 归属 | HQ owner + subsidiary target | 保持 HQ 分发控制权，同时让子公司读路径以 `target_tenant_id` 隔离 |
| D2 | 元数据写入时机 | result apply 后写入 | data-plane result 已有 `key/size/sha256` receipt，控制面是业务状态源 |
| D3 | 下载方式 | 控制面鉴权后返回短 TTL presigned URL | 避免控制面代理大文件，也不把 sink 权限暴露给用户 |
| D4 | dedup 范围 | 只建 `physical_object` 元数据，不做平台层 dedup 命中 | dedup 需要更完整的引用计数 / GC 语义，留 Phase 7 |
| D5 | UI 范围 | 先做 API + 最小静态/现有前端入口 | 不把管理后台、规则配置台和完整用户管理塞进本阶段 |

## 二、范围

### In Scope

- 新增 `workspace`、`physical_object`、`workspace_object` SQLAlchemy models 和 Alembic migration。
- 最小 seed/dev helper：HQ tenant 下创建 demo workspace，target 到一个 subsidiary tenant。
- result apply 根据 `task_item.target_name_matched` / profile target 选择 workspace，并写入 `physical_object` / `workspace_object`。
- 子公司读 API：
  - `GET /api/v1/workspaces`
  - `GET /api/v1/workspaces/{workspace_id}/objects`
  - `GET /api/v1/workspace-objects/{object_id}`
  - `POST /api/v1/workspace-objects/{object_id}/download-url`
- HQ 可读自己 owner workspace；subsidiary 只能读 `target_tenant_id == actor.tenant_id`。
- 下载 URL 先支持 S3 / MinIO presigned GET。
- 最小 task_event / audit attribution：下载 URL 签发记录 actor、object、workspace。
- README、DATA_MODEL、ARCHITECTURE、ROADMAP、Phase plan 同步。

### Out of Scope

- 平台层 dedup 命中、引用计数 GC 和物理对象删除。
- Multipart / resume。
- sink credential 加密管理和多 sink 凭证 UI。
- OIDC / SAML / SSO federation。
- workspace 管理后台和完整用户管理 UI。
- 子公司写入 / 删除 / 重命名文件。

## 三、任务清单

状态标记：`[ ]` 未开始 / `[~]` 进行中 / `[x]` 已完成 / `[!]` 阻塞

### 6.5.1 Workspace schema baseline

- **状态**：`[ ]`
- **L 等级**：L2
- **推荐执行方**：`gpt-5.5` medium worker，主对话审计
- **范围**：
  - 新增 `Workspace`、`PhysicalObject`、`WorkspaceObject` models。
  - 新增 Alembic migration。
  - 补 SQLite migration / model registration 测试。
- **验收**：
  - `alembic upgrade head` 可创建 workspace 表。
  - 外键表达 owner tenant、target tenant、physical object 和 uploaded_by user。

### 6.5.2 Workspace repository and access policy

- **状态**：`[ ]`
- **L 等级**：L2
- **推荐执行方**：`gpt-5.5` medium worker，主对话审计
- **范围**：
  - 新增 workspace / object repos。
  - 根据 `CurrentActor` 提供 HQ owner 视图和 subsidiary target 视图。
  - 禁止子公司跨 tenant 列表、详情和下载。
- **验收**：
  - repo integration tests 覆盖 HQ owner、subsidiary visible、cross-tenant hidden。

### 6.5.3 Result apply to workspace metadata

- **状态**：`[ ]`
- **L 等级**：L3
- **推荐执行方**：主对话或 `gpt-5.5` medium worker，小步提交后主对话审计
- **范围**：
  - `apply_delivery_result` 成功 item 写入 `physical_object` 和 `workspace_object`。
  - 映射策略先使用 profile target / `task_item.target_name_matched` 找 workspace。
  - 幂等处理重复 result apply，避免重复 workspace_object。
- **验收**：
  - result apply integration test 证明 uploaded item 生成读模型。
  - 重复 result apply 不重复创建同一 task_item 对应对象。

### 6.5.4 Workspace read APIs

- **状态**：`[ ]`
- **L 等级**：L2
- **推荐执行方**：`gpt-5.5` medium worker，主对话审计
- **范围**：
  - 新增 workspace API router 和 Pydantic schemas。
  - 列表、对象列表、对象详情全部接入 `CurrentActor`。
  - 保持默认 actor 兼容 HQ 本地开发。
- **验收**：
  - API tests 覆盖 HQ / subsidiary / cross-tenant 404。

### 6.5.5 Presigned download URL

- **状态**：`[ ]`
- **L 等级**：L2
- **推荐执行方**：`gpt-5.5` medium worker，主对话审计
- **范围**：
  - 新增 S3 / MinIO presign helper。
  - `download-url` API 在授权后返回短 TTL URL。
  - 记录 `workspace_object_download_url_issued` task_event 或 audit event。
- **验收**：
  - 单测 mock S3 presigner。
  - API 测试证明未授权 actor 不会触发 presign。

### 6.5.6 Phase 6.5 smoke and docs

- **状态**：`[ ]`
- **L 等级**：L1
- **推荐执行方**：mini worker 可做文档草稿；主对话跑 smoke
- **范围**：
  - 增加 opt-in 或普通 integration smoke：HQ 写入 -> result apply -> subsidiary list/download-url。
  - 更新 README、ARCHITECTURE、DATA_MODEL、ROADMAP、component README。
  - Phase 6.5 完成后再讨论最小完整平台 tag。
- **验收**：
  - 新开发者能按 README 跑通最小完整平台闭环。
  - Phase plan 全部 `[x]` 后才把 Phase 7 标为 Current。

## 四、验证矩阵

默认快速验证：

```bash
cd control-plane
.venv/bin/python -m ruff check app tests
.venv/bin/python -m pytest
```

迁移验证：

```bash
cd control-plane
DATABASE_URL=sqlite+aiosqlite:////tmp/phase65_workspace_check.db .venv/bin/python -m alembic upgrade head
```

读路径 smoke：

```bash
cd control-plane
RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest tests/integration/test_workspace_read_view_docker.py
```

## 五、风险与降级

- **workspace 映射错误**：先使用显式 seed / profile target 映射，找不到 workspace 时写 task_event warning，不伪造对象。
- **重复 result apply**：必须以 `task_item_id` 或 `(workspace_id, task_item_id)` 做唯一约束。
- **下载越权**：presign helper 只能在权限检查后调用，测试要断言未授权不会调用 presigner。
- **dedup 过早膨胀**：本阶段只记录 `sha256/size/key`，不实现 instant upload / refcount GC。
- **sink 耦合**：presigned URL 先只支持 S3 / MinIO；其他 sink 在 Phase 7 按 capability 扩展。

## 六、派工策略

- 中风险实现默认用 `gpt-5.5` medium worker；主对话负责审计、整合、迁移和 smoke。
- `gpt-5.4-mini` 仅用于文档、简单测试草稿和不跨模块的小改动。
- 升档触发：迁移失败、权限边界不清、result apply 幂等异常、download URL 越权风险、worker 连续失败。
- 每个 worker 必须声明改动文件、测试命令和是否偏离本计划。
