# Phase 6 — 多租户 + 鉴权

> **状态**：Done（Phase 6 / 6.5 plan PR 已合并；tag-prep review 已修复 result apply 任务绑定、confirm 状态检查和启动迁移边界）
> **目标**：补齐 tenant / app_user / role / request actor context，让 HQ 与子公司用户隔离成为平台默认边界。
> **完成定义**：控制面 API 可识别当前 actor；task / item / event 访问走 tenant-aware repo；task_event 至少记录最小 actor attribution；HQ 与子公司角色有最小 RBAC；测试覆盖跨租户不可见、越权写入拒绝、默认开发 actor 兼容路径。
> **前序计划**：[Phase 5 — 可观测三件套](./phase-5-observability.md)

## Summary

Phase 5 已经提供 trace、metrics、dashboard 和 smoke，后续改动可以用观测基线定位跨组件问题。Phase 6 不直接做 workspace 文件浏览，也不做完整 SSO；它先把开发 header / 默认 actor、tenant / app_user、task owner tenant/user、repo tenant filter 和最小 task_event actor attribution 落到控制面写路径，为 Phase 6.5 的子公司读视图提供前提。

Phase 6 的原则：先做平台内置的最小身份模型和仓储层隔离；默认开发环境仍可用本地 actor，避免所有测试突然依赖外部 IdP。

## 一、先决决策

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| D1 | 身份来源 | 先用开发 header / 默认 local actor，SSO 留后续 | 当前 demo 不引入 OIDC / SAML；先验证权限边界 |
| D2 | 租户模型 | HQ tenant + subsidiary tenant | 匹配 PRD 角色和后续 workspace target tenant |
| D3 | RBAC 粒度 | `hq_admin`、`hq_uploader`、`subsidiary_admin`、`subsidiary_viewer` | 足够覆盖写路径和后续读路径入口 |
| D4 | 隔离位置 | repo/service 层强制 tenant filter | 当前 SQLite/MySQL 测试都能覆盖；数据库 RLS 另行 ADR |
| D5 | 兼容策略 | 默认开发 actor 映射到 HQ uploader | 不破坏现有无鉴权 smoke；生产模式再要求显式 actor |

## 二、范围

### In Scope

- 新增 tenant / app_user schema、SQLAlchemy model、Alembic migration 和 seed/dev helper。
- control-plane request actor context：从 header 或本地默认 actor 构造 `CurrentActor`。
- API dependency：区分 HQ uploader/admin 与 subsidiary 角色。
- task / task_item / task_event repo 增加 tenant-aware 查询和写入边界。
- create/classify/confirm/upload/retry/detail/list/progress 路由补权限检查。
- 采用最小 task_event payload attribution；至少记录关键写操作 actor。
- README、DATA_MODEL、ARCHITECTURE、ROADMAP、Phase plan 同步。

### Out of Scope

- OIDC / SAML / SSO federation。
- 子公司 workspace 文件浏览与下载视图；放到 Phase 6.5。
- sink credential 加密管理。
- physical_object / workspace_object / dedup 落地。
- 管理后台 UI 全量实现；本阶段可先用 API / seed 数据。

## 三、任务清单

状态标记：`[ ]` 未开始 / `[~]` 进行中 / `[x]` 已完成 / `[!]` 阻塞

### 6.1 Tenant / app_user schema baseline

- **状态**：`[x]`
- **L 等级**：L2
- **范围**：
  - 新增 tenant / app_user SQLAlchemy models。
  - 新增 Alembic migration。
  - Settings 增加 dev actor 开关和默认 HQ tenant/app_user。
  - 单测覆盖 model defaults 和 settings。
- **验收**：
  - SQLite test schema 和 MySQL migration 都可创建。
  - 默认测试不需要外部身份服务。

### 6.2 CurrentActor dependency

- **状态**：`[x]`
- **L 等级**：L2
- **范围**：
  - 新增 actor service / FastAPI dependency。
  - 支持开发 header 或默认 actor。
  - 提供 role check helper。
- **验收**：
  - 单测覆盖 header actor、默认 actor、禁用默认 actor时缺失身份失败。

### 6.3 Tenant-aware task write path

- **状态**：`[x]`
- **L 等级**：L3
- **范围**：
  - task create 写入 owner tenant / app_user。
  - classify / confirm / upload / retry / detail / list / progress 全部按 actor tenant 和 role 过滤。
  - 现有 smoke 保持默认 actor 兼容。
- **验收**：
  - 集成测试证明跨 tenant task 不可读、不可修改。
  - HQ uploader 可执行写路径；subsidiary viewer 不可触发上传。

### 6.4 Task event actor attribution

- **状态**：`[x]`
- **L 等级**：L2
- **范围**：
  - 选择最小 actor attribution 方案：优先扩展 task_event payload / 字段，必要时再补 audit_log。
  - 关键写操作记录 actor app_user / tenant / role。
- **验收**：
  - 测试覆盖 create / confirm / upload 至少一条 actor attribution。

### 6.5 Phase 6 smoke and docs

- **状态**：`[x]`
- **L 等级**：L1
- **范围**：
  - 增加 opt-in 或普通 integration smoke，覆盖两个 tenant 的隔离边界。
  - 更新 README、ARCHITECTURE、DATA_MODEL、ROADMAP。
  - Phase 6 完成后把 Phase 6.5 标记为 Current。
- **验收**：
  - 新开发者能按 README 用默认 actor 跑通原有写路径。
  - 权限测试能清楚说明 actor、tenant、role。

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
.venv/bin/python -m alembic upgrade head
```

跨组件回归：

```bash
cd control-plane
RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest tests/integration/test_observability_docker.py
```

本轮本地验证：

```bash
cd control-plane
.venv/bin/python -m ruff check app tests
.venv/bin/python -m pytest
DATABASE_URL=sqlite+aiosqlite:////tmp/phase6_alembic_check_20260522.db .venv/bin/python -m alembic upgrade head
```

tag-prep review 验证：

```bash
cd control-plane
.venv/bin/python -m pytest tests/unit/test_auth.py tests/integration/test_task_repo.py tests/integration/test_item_repo.py tests/integration/test_event_repo.py tests/integration/test_api_tasks.py tests/integration/test_delivery.py tests/e2e/test_upload_flow.py
.venv/bin/python -m ruff check app tests

cd ../data-plane
GOTOOLCHAIN=local GOPATH=/tmp/smh_go_path GOMODCACHE=/tmp/smh_go_mod_cache GOCACHE=/tmp/smh_go_cache go test ./...
```

## 五、风险与降级

- **隐式越权**：repo 层必须默认 tenant-aware；不能只在路由层过滤。
- **测试兼容性**：默认 actor 只用于开发和测试；生产模式需要显式身份。
- **迁移边界**：应用启动不自动建表；新库必须先跑 Alembic，默认 `hq` / `local-user` seed 由 migration 写入。
- **模型膨胀**：workspace、physical object、sink credential 不进入 Phase 6 主线。
- **审计不完整**：先覆盖关键写操作，读审计在 Phase 6.5 子公司读视图中补齐。
