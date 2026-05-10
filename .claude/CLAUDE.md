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

## 不在这里讨论的事

- 完整 6 段任务模板 → AGENTS.md § 五
- Phase 启动 8 步流程 → AGENTS.md § 六
- 失败模式与对策 → AGENTS.md § 十
- 不委托清单 → AGENTS.md § 四
