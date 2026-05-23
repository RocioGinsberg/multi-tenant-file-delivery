# Phase 6.5 Audit — DDD Review Hardening

> **状态**：In Review（stacked audit phase，仅用于 tag 前审计加固，不修改 Phase 6.5 的 Done / Not Done 状态）
> **目标**：针对 Phase 6.5 workspace read view 的 DDD review 发现做独立收敛，修正语义漂移、审计缺口和仓储边界过宽问题，让系统语义回到统一的 Ubiquitous Language、清晰的 Bounded Context 和更窄的 Repository 责任。
> **完成定义**：所有审计项有明确结论并落实到文档 / 代码 / 测试或迁移拆分建议；HQ 身份语义不再硬编码 `tenant_id == "hq"`；workspace read model 明确由 go-worker result apply 生成；download URL 审计可追溯到来源 task；`WorkspaceRepo` 的访问范围命名收敛；`record_uploaded_item` 的领域契约明确；术语统一为 canonical classification target / workspace target key；migration 0003 的 demo seed 被明确为本地 bootstrap 数据；`workspace_object.task_id/task_item_id` 非空且幂等约束语义被澄清；补齐 result apply -> subsidiary read/download 的真实 DB API smoke。
> **前序计划**：[Phase 6.5 — Workspace + 子公司读视图](./phase-6.5-workspace-read-view.md)

## Summary

这是一个 stacked audit phase，不是新的产品 Phase，也不把 Phase 6.5 改写成 Done 或 Not Done。它只承担 tag 前的 DDD 审计收敛：把已经实现的 workspace read view 重新对齐到领域语言、边界和仓储语义，避免“功能已通、语义漂移”的状态直接进入基线。

这次审计以 DDD 三个原则为主线：

- **Ubiquitous Language**：统一 target / profile / workspace / object / upload / audit 的命名，避免同一概念多套说法。
- **Bounded Context**：明确 control-plane、data-plane、workspace read model、audit attribution 各自负责什么，不让 read model 语义反向污染写路径。
- **Repository 语义**：Repository 只负责聚合持久化与检索，不混入策略选择、读模型组装或工厂逻辑。

## 一、先决决策

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| D1 | 计划性质 | 独立 stacked audit phase | 只做审计加固，不回写 Phase 6.5 状态定义 |
| D2 | 身份语义 | 用显式 tenant / actor 语义，不硬编码 `hq` | 避免把组织代号写死为领域规则 |
| D3 | 术语基线 | 统一采用 `classification_target_key` / `workspace_target_key` 等通用命名 | 降低 target / profile / workspace 混称造成的理解成本 |
| D4 | 审计边界 | Phase 6.5 的 workspace object 必须来自 task item，download URL 审计写回来源 task event | 完整 `audit_log` 留 Phase 7；本阶段通过非空来源约束避免无来源对象 |
| D5 | 迁移职责 | 0003 demo seed 暂保留并标注为本地 bootstrap 数据 | 拆 seed 脚本会扩大迁移面，留 Phase 7 / release hardening |

## 二、范围

### In Scope

- 审核并修正文档中的 HQ 身份语义，不再把 `tenant_id == "hq"` 当成领域真理。
- 审核 `DELIVERY_BACKEND=python` 的默认值承诺，明确它与 workspace read model 的生成责任之间是否存在漂移。
- 为 download URL 审计补上真实来源 task / source object 的追踪方式，并禁止 Phase 6.5 生成无来源 task item 的 workspace object。
- 收窄 `WorkspaceRepo` 的职责，拆清混合访问策略、读模型创建和 `PhysicalObject` factory 的边界。
- 明确 `record_uploaded_item` 的参数与返回契约，替代 `Any`。
- 统一 target/profile/workspace 相关术语到 `classification_target_key`、`workspace_target_key` 这类通用语言。
- 明确 migration 0003 中 demo seed 是本地 bootstrap 数据，后续再拆显式 seed 脚本。
- 澄清 `workspace_object.task_id/task_item_id` 非空且 `task_item_id` 参与幂等唯一约束。
- 增加缺失的 result apply -> subsidiary read/download 真实 DB API smoke。

### Out of Scope

- 新增 Phase 7 功能。
- 重写整个 workspace read model 设计。
- 迁移数据库大版本。
- 变更外部 sink / object store 协议。
- 对 Phase 6.5 已完成功能做产品范围扩张。

## 三、任务清单

状态标记：`[ ]` 未开始 / `[~]` 进行中 / `[x]` 已完成 / `[!]` 阻塞

### 6.5A.1 HQ 身份语义审计

- **状态**：`[x]`
- **L 等级**：L2
- **范围**：
  - 找出所有把 HQ 直接写成 `tenant_id == "hq"` 的地方。
  - 改成显式身份 / 租户语义或配置化 seed 语义。
  - 在文档中说明 HQ 是角色 / 租户 / seed 约定中的哪一层，不混写。
- **验收**：
  - 不再依赖硬编码字符串来表达 HQ 领域身份。
  - 相关文档能清楚区分 identity、tenant、role。

### 6.5A.2 Delivery backend 与 read model 承诺审计

- **状态**：`[x]`
- **L 等级**：L2
- **范围**：
  - 复核 `DELIVERY_BACKEND=python` 默认值与实际数据生成路径。
  - 明确 workspace read model 是否完全由 go-worker result apply 生成，还是存在其他来源。
  - 如果存在承诺漂移，写入审计结论和修正建议。
- **验收**：
  - 文档中不再出现“默认 backend”和“唯一生成来源”互相冲突的说法。
  - 所有读模型来源能被单一路径解释。

### 6.5A.3 download URL 审计链修正

- **状态**：`[x]`
- **L 等级**：L3
- **范围**：
  - 复核现有 download URL 审计事件字段。
  - 明确 Phase 6.5 不允许无来源 task 的 workspace object。
  - 通过非空 `task_id/task_item_id` 让审计链路可以写回来源 task event。
  - 明确 source task / source object / workspace object 的关系。
- **验收**：
  - 审计事件可以定位到真实来源，而不是只靠间接字段推断。
  - 无来源 task 对象不再是合法 Phase 6.5 状态；完整 audit_log 留 Phase 7。

### 6.5A.4 Repository / factory 边界收窄

- **状态**：`[x]`
- **L 等级**：L3
- **范围**：
  - 将 workspace API / repo 参数从 `is_hq` 布尔改为 `owner` / `target` access scope。
  - 用 Repository 的语义边界约束它围绕 workspace metadata 持久化与检索；完整 factory 拆分留 Phase 7。
  - 在文档里说明哪些逻辑应属于 service / application layer。
- **验收**：
  - Repository 责任可以用一句话描述，不再使用 HQ 布尔语义传递访问策略。
  - 读模型创建路径可单独说明，不混进 repository 说明里。

### 6.5A.5 领域契约与术语统一

- **状态**：`[x]`
- **L 等级**：L2
- **范围**：
  - 将 `record_uploaded_item` 的 `Any` 收敛为明确的领域类型或结构。
  - 统一 target / profile / workspace target 的术语。
  - 文档统一说明 `task_item.target_name_matched` 是 canonical classification target，`workspace.target_key` 是 workspace target key。
- **验收**：
  - API / service / doc 对同一概念使用一致词汇。
  - 关键函数签名表达出领域契约，而不是吞掉类型信息。

### 6.5A.6 migration 0003 demo seed 审计

- **状态**：`[x]`
- **L 等级**：L2
- **范围**：
  - 识别 migration 0003 中 demo seed 的职责边界。
  - 保留 0003 demo seed，并在 README / DATA_MODEL 中明确为本地 demo bootstrap 数据。
  - 在文档中明确迁移与 seed 的区别。
- **验收**：
  - schema migration 与 demo 数据的关系被明确说明；拆出独立 seed 脚本留后续 release hardening。
  - 新库初始化路径清晰。

### 6.5A.7 幂等约束语义审计

- **状态**：`[x]`
- **L 等级**：L3
- **范围**：
  - 复核 `workspace_object.task_item_id` nullable 与唯一约束的组合语义。
  - 将 `task_id/task_item_id` 改成非空，外键 `RESTRICT`。
  - 保留 `task_item_id` 唯一约束作为 result apply 幂等键。
- **验收**：
  - 幂等语义可解释、可验证，不留下“字段可空但又必须唯一”的模糊区。

### 6.5A.8 Phase 6.5 smoke 补洞

- **状态**：`[x]`
- **L 等级**：L3
- **范围**：
  - 补一条真实的 result apply -> subsidiary read/download API smoke。
  - 覆盖来源 task event 审计路径。
  - 确认读视图、下载签发和审计记录都能串起来。
- **验收**：
  - smoke 明确证明读模型、子公司列表、下载签发和来源 task_event 审计闭环。
  - 失败时可以定位是 upload、result apply、读视图还是下载审计哪一段出问题。

## 四、验证矩阵

默认快速验证：

```bash
cd control-plane
.venv/bin/python -m ruff check app tests
.venv/bin/python -m pytest
```

审计回归关注点：

```bash
cd control-plane
.venv/bin/python -m pytest tests/integration/test_workspace_repo.py tests/integration/test_api_workspaces.py tests/integration/test_delivery.py
```

迁移与 seed 复核：

```bash
cd control-plane
.venv/bin/python -m alembic upgrade head
```

读路径 smoke：

```bash
cd control-plane
.venv/bin/python -m pytest tests/integration/test_api_workspaces.py::test_workspace_read_view_smoke_result_apply_to_subsidiary_download
```

## 五、风险与降级

- **语义回改过大**：如果术语统一会牵出多个模块，先在文档和函数签名层收敛，再决定是否拆分实现。
- **HQ 身份重构风险**：不要把 HQ 变成新的硬编码常量；优先保留显式 seed / actor 语义。
- **审计链补全成本**：如果现有 event 结构不足以表达来源链，先补文档和最小字段，再考虑扩字段。
- **migration 与 seed 纠缠**：宁可把 demo seed 下放到显式初始化脚本，也不要继续让 schema migration 承担示例数据职责。
- **幂等约束不清**：在约束语义未完全收敛前，优先用测试和审计说明保底，不强行扩大数据库约束面。

## 六、派工策略

- 这是审计加固 phase，优先由主对话直接掌握结论，不拆太多并发任务。
- 若需要派工，只拆成文档审计、测试补洞、迁移语义确认三类小任务。
- 任何结论都必须回到 DDD 语言：先确认术语，再确认边界，最后确认 Repository 语义。
