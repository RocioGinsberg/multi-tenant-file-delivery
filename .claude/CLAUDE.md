# 派工纪律（项目级 CLAUDE.md）

> 这份 CLAUDE.md 在主对话每轮都会自动加载到上下文。专门用来**强化派工纪律**——防止主对话偷懒直接做 L1 任务、烧 Opus 的钱。
>
> 完整协作规范见 [AGENTS.md](../AGENTS.md)。

---

## 我（主对话）必须遵守的派工规则

### 第一规则：不允许直接做 L1 任务

**L1 = 机械型任务**，定义见 AGENTS.md § 三：
- 改字段名 / 重命名变量 / 统一 import 路径
- 写 table-driven 单测（被测函数已定）
- 补 docstring / 加 type hint
- scaffold 重复模式（"按这个写 5 个类似的"）
- 翻译注释 / 中文化文档

**遇到 L1 任务，禁止用 Edit/Write 直接动手**。必须委托：
- **首选**：派 aider+DeepSeek（Bash 调用，命令模板见 AGENTS.md § 7.4.1）
- **备选**：派 Haiku subagent（`Agent(subagent_type=general-purpose, model=haiku, ...)`）

### 第二规则：L2 任务派 subagent，不自己写

**L2 = 模式型任务**：写具体 sink、写仓储层、Pydantic schema、Alembic migration、FastAPI 路由。

按 AGENTS.md § 八 模块主力模型表派工：

- `data-plane/` 下的 Go 代码 → Codex（默认 medium，按 § 八.5 升降档信号）
- `control-plane/app/` 下的 Python 代码 → Sonnet subagent
- `tests/` 下的测试 → aider+DeepSeek

### 第三规则：只对 L3 自己动手

**L3 = 设计型任务**，主对话亲自写：
- 改 BLUEPRINT.md / 写新 ADR
- 改 Sink 接口 / Source 接口 / 抽象层签名
- 安全/鉴权/加密代码
- 跑测试看日志改 BUG（debug 需要项目历史上下文）
- Phase 拆分与子任务规划
- review subagent 的输出

---

## Codex 派工默认行为（方案 B）

- **常规任务**：按模块默认档位（见 AGENTS.md § 八）直接派，事后告诉用户用了哪档
- **升档信号触发**（任务里出现"并发/race/死锁/状态机/一致性/性能优化"等词，或接口变更面广，或首次实现某算法）→ 派之前先和用户确认档位
- **降档信号触发**（明确模板复制、批量补样板、纯机械改名）→ 派之前先和用户确认档位
- 用户明说"用 xhigh"或"用 low"时立即覆盖，不重新评估

---

## aider 派工的强制参数

任何调用 aider 的 Bash 命令**必须**带：

```
--model deepseek/deepseek-chat \
--no-auto-commits \
--yes \
--no-stream \
--message "..." \
<显式列出可改文件路径>
```

**理由**：
- `--no-auto-commits` 防止 aider 绕过主对话 commit 控制权（失败模式 I）
- `--yes --no-stream` 防止交互式提示卡住主对话
- 显式文件路径防止 aider 自由发挥改无关文件

**首选用 aider-dispatch skill** 调起，避免手抄长命令出错。

---

## 关于成本透明

每次派工后，在汇报里说明用了什么模型/档位。让用户能持续校准"哪些任务值得 Opus、哪些该委托"。

例：
> 已派 Sonnet subagent 实现 control-plane/app/repos/workspace_repo.py。
> 派 aider+DeepSeek 写了 5 个 pytest（成本 < ¥0.05）。

---

## TDD + contract-first 派工纪律

某些任务标记为 **TDD 任务**（参见 `docs/plans/phase-N-*.md` 每个子任务的"TDD?"列）。
对 TDD 任务，**严格遵守 8 步流程**，不允许任何步骤合并：

1. **主对话起草测试 spec**——写到 `docs/plans/` 对应 phase 文档的子任务下，包含：一句话验收标准 / 主要 case 列表 / 边界条件 / 不验证什么
2. **用户 review spec**——把 spec 给用户看，等用户明确"通过"或提出调整。**不可跳过**
3. **派工写测试代码**——首选 Sonnet subagent（**承重墙级 TDD 任务不能派 aider 写测试**——测试质量比代码还关键）；输入 spec + 必读上下文
4. **review 测试代码**——主对话读完整测试代码 + 跑一次（应**全部失败**，因为实现还没写）；用户 review 是否漏 case / 误测
5. **测试单独 commit**——commit message 写明"phaseN(X.Y): test spec for ...（red）"。**测试与实现绝不能在同一 commit 里**
6. **派工写实现**——传入"测试在 `tests/test_X.py`，必须让它过；禁止修改测试文件"
7. **review 实现**——跑测试应全部通过；主对话读 diff；如果有失败，回到 6（重派或主对话接手），**不允许通过修改测试让它过**（AGENTS.md § 十 失败模式 A）
8. **实现 commit**——commit message 写明"phaseN(X.Y): impl ...（green）"

### TDD 派工纪律的硬底线

- ❌ **测试与实现混 commit**——必须独立 commit，便于 git diff 看出测试是否被实现阶段偷改
- ❌ **派 aider 写承重墙级 TDD 任务的测试**——aider+DeepSeek 适合 L1 量产测试（如端到端补样板），但 TDD 任务的契约级测试派 Sonnet
- ❌ **跳过用户 review spec**——TDD 的核心价值是用户深度参与契约定义；跳过等于退化成"先写后写"，价值归零
- ❌ **实现阶段改测试**——除非用户明确批准并给出充分理由（如 spec 本身错了）；批准后应单独 commit "test spec adjustment"

### 调用 tdd-flow skill

任何 TDD 任务的启动应通过 **tdd-flow** skill（关键词触发：TDD / 先写测试 / contract-first）。
skill 会自动展开 8 步流程的各步派工模板，主对话不必手抄。

---

## 不在这里讨论的事

- 完整 6 段任务模板 → AGENTS.md § 五
- Phase 启动 8 步流程 → AGENTS.md § 六
- 失败模式与对策 → AGENTS.md § 十
- 不委托清单 → AGENTS.md § 四
