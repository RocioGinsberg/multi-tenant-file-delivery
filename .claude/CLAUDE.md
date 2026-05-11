# 派工纪律（Claude Code 项目级 CLAUDE.md）

> 每轮自动加载，强化 Claude Code 的派工纪律。**完整协作规范**见 [DISPATCH.md](../DISPATCH.md)。
>
> 核心原则：架构权留主对话，执行权下放执行池，安全/承重墙不委托。

---

## 执行池（Claude Code 语法）

| 层级 | 执行方 | 工具调用 |
|---|---|---|
| L1 | aider+DeepSeek | `Skill("aider-dispatch")` 优先；备选 `Agent(model="haiku")` |
| L2 | Sonnet subagent | `Agent(subagent_type="general-purpose", model="sonnet", prompt="...")` |
| L3 | 主对话亲自写 | — |

**模块对应**：
- `control-plane/app/` Python → `Agent(model="sonnet")`
- `control-plane/tests/` → `Skill("aider-dispatch")`
- `data-plane/` Go → Codex 宿主执行（`Skill("codex:rescue")` 或用户在 Codex 侧操作）

---

## Plan Mode 审批门

**以下情况调用 `EnterPlanMode`，等用户确认后再执行**：
- L2 或 L3 实现任务（非纯问答/研究）
- 涉及超过 2 个文件的修改
- 跨模块任务
- Phase 启动

**不需要 plan mode**：单行修复、用户明确说"直接做"、L3 任务主对话亲自写。

---

## 三个核心规则

### 第一：L1 任务不允许直接动手

L1（机械型，定义见 DISPATCH.md § 三）：改字段名 / 写 table-driven 单测 / 补 docstring / scaffold / 翻译注释。

**遇到 L1 → `Skill("aider-dispatch")`；备选 `Agent(model="haiku")`**

### 第二：L2 任务派 Sonnet subagent

L2（模式型）：写仓储层 / Pydantic schema / Alembic migration / FastAPI 路由 / 具体 sink。

**→ `Agent(subagent_type="general-purpose", model="sonnet", prompt="...")`**

派工 prompt 遵循 DISPATCH.md § 五 6 段模板（目标 / 约束 / 上下文 / 验收 / 输出格式 / 失败行为）。

### 第三：L3 只由主对话动手

L3（设计型）：改 BLUEPRINT / 写 ADR / 改接口签名 / 安全代码 / debug / Phase 规划 / review 产出。

---

## Skill 调用（优先于手写 Bash）

| 场景 | Skill |
|---|---|
| L1 批量任务（测试、docstring、改名） | `Skill("aider-dispatch")` |
| TDD 任务（8 步 spec→red→green） | `Skill("tdd-flow")` |
| 代码 review 后质量收敛 | `Skill("simplify")` |

---

## aider 强制参数（直接用 Bash 而非 skill 时）

```
--model deepseek/deepseek-chat \
--no-auto-commits \
--yes \
--no-stream \
--message "..." \
<显式文件路径>
```

---

## TDD 派工纪律

TDD 任务（phase 文档中标记 ✅）→ `Skill("tdd-flow")`，8 步不可跳：
spec → 用户 review → 写测试 → 测试 commit(red) → 写实现 → 实现 commit(green)

**硬底线**：
- ❌ 测试与实现混 commit
- ❌ 承重墙级 TDD 测试派 aider（用 Sonnet subagent）
- ❌ 跳过用户 review spec
- ❌ 实现阶段改测试

---

## 成本透明

每次派工后报告：执行方 / 模型 / 估算成本。

例：
> 派 `Agent(model="sonnet")` 实现 control-plane/app/repos/task_repo.py。
> 派 `Skill("aider-dispatch")` 写了 5 个 pytest（估计 < ¥0.05）。

---

## 参考

- 完整 6 段派工模板 → DISPATCH.md § 五
- Phase 启动 8 步流程 → DISPATCH.md § 六
- 失败模式与对策 → DISPATCH.md § 十
- 不委托清单 → DISPATCH.md § 四
