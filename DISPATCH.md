# DISPATCH.md — 多 Agent 协作执行规范（宿主共用）

> 这是项目的"协作合同"——宿主无关的规则。各宿主的派工纪律在各自的加载文件里：
> - **Claude Code** → `.claude/CLAUDE.md`（每轮自动加载）
> - **Codex CLI** → `AGENTS.md`（Codex 自动加载）
>
> **核心原则**：架构权留在主编排层，执行权下放给执行池，安全/承重墙不委托。

---

## 一、为什么需要这份文档

代码实现可以委托，但**项目方向感不能委托**。多 agent 自动化最常见的失败模式是：执行者写出"看起来对、跑得通、但破坏承重墙决策"的代码——review 成本和自己写差不多，反而更慢。

这份文档定义"什么任务派给什么模型"和"派任务时必须带什么上下文"，确保委托是省时间的，不是省麻烦堆给未来。

本文档不绑定某一个宿主工具。当前主编排 Agent 可以是 Claude Code、Codex CLI、IDE 内的 coding agent，或其他具备读文件、改文件、跑命令、调用子代理能力的工具。只要它承担拆任务、写约束、调用执行者、review diff、控制 commit 的职责，就按本文档里的"主编排 Agent"执行。

---

## 二、角色与责任

```
┌─────────────────────────────────────────────────┐
│  你（产品 + 终审）                                │
│   ├─ 决定 Phase 节奏与优先级                     │
│   ├─ 拍架构决策（看 BLUEPRINT/ADR diff 后批准）  │
│   ├─ 最终 commit 权                              │
│   └─ 任何"是否上线/合并"的最终判断               │
└────────────────┬────────────────────────────────┘
                 │
┌────────────────▼────────────────────────────────┐
│  主编排 Agent（Orchestrator）                    │
│   架构权 + 编排权                                │
│   可由 Claude Code / Codex / 其他工具承担        │
│   ├─ 把 Phase 拆为可委托的子任务                 │
│   ├─ 写验收标准 + 关键约束 + 文件路径            │
│   ├─ 从执行池里选模型派工                        │
│   ├─ Review 子任务 diff（不只看摘要）            │
│   ├─ 写承重墙代码（接口、抽象、安全、配置 schema）│
│   ├─ 写 ADR 与 BLUEPRINT 改动                    │
│   └─ 失败时决定：重派 / 换模型 / 自己接手         │
└────────────────┬────────────────────────────────┘
                 │
                 ▼
   ┌─────────────────────────────────────────┐
   │  执行池（Execution Pool）               │
   │  全部归同一个派工层，由主编排 Agent 编排 │
   │                                         │
   │  宿主内置子代理：                       │
   │  ├─ Claude Agent / Sonnet / Haiku       │
   │  └─ Codex spawn_agent worker/explorer   │
   │                                         │
   │  Plugin / MCP 接入：                    │
   │  ├─ mcp__codex__*    ─ GPT-5 / Codex    │
   │  ├─ mcp__glm__*      ─ GLM-4.6/Coder    │
   │  ├─ mcp__deepseek__* ─ DeepSeek-V3/R1   │
   │  └─ ...                                 │
   │                                         │
   │  CLI 派工（主编排 Agent 调 Bash）：     │
   │  └─ aider + DeepSeek/GLM 后端           │
   │     L1 批量任务的省钱选项                │
   │                                         │
   │  外部独立工具（不经主编排 Agent）：    │
   │  ├─ IDE Codex Plugin  ─ 你 IDE 内打字   │
   │  └─ aider / continue  ─ 终端 REPL       │
   └─────────────────────────────────────────┘
```

**关键认知**：所有执行者（Claude subagent、Codex subagent、MCP plugin、aider CLI、GLM/DeepSeek worker）在**派工形态上是同等的**——都是主编排 Agent 调用的执行端点，差异只在模型能力、价格、速度、工具协议。**主编排 Agent 的责任不变**：拆任务、写约束、review、决定 commit 边界。

执行池里加新成员的成本接近于零（装 plugin、开 MCP、调用 `spawn_agent`、跑 aider），但**派工的质量门槛不降**——任何执行者都要按 § 五 的 6 段模板派任务，按 § 九 的质量底线 review。

**宿主适配规则**：
- 当前宿主有子代理能力时，按它的原生工具调用：Claude Code 用 `Agent`；Codex 用 `spawn_agent` / `worker` / `explorer`；MCP 环境用 `mcp__*` 工具。
- 当前宿主没有子代理能力，或系统权限不允许派子代理时，主编排 Agent 自己完成任务，或按 § 7.4.1 通过 aider 执行局部 L1 任务。
- 所有宿主都必须服从自身系统约束和项目安全边界；本文档提供项目级派工策略，不覆盖工具平台的安全规则。
- 如果当前环境普通 Bash 会报 `bwrap: setting up uid map: Permission denied` 之类沙箱初始化错误，主编排 Agent 应把任务拆小，并在派工提示里提醒执行者优先报告工具失败；不要把多文件大补丁一次性交给执行者。

---

## 三、任务认知负荷三档（决定派给谁）

### L1 — 机械型（高频、低决策）

**特征**：
- 答案唯一或近乎唯一
- 不需要项目历史上下文
- 改错了一眼能看出来

**举例**：
- 写一个 CRUD endpoint（schema 已定）
- 写 table-driven 单元测试（被测函数已定）
- 写 Pydantic schema（字段表已定）
- 改字段名、统一 docstring、补 type hint
- scaffold 重复模式（"按这个模板写 5 个类似的 sink mock"）

**派给**：L1 worker / aider+DeepSeek / IDE Codex Plugin
**Review 强度**：扫一眼 diff，跑测试

### L2 — 模式型（有标准答案，但要按规范）

**特征**：
- 答案有多个合理版本，需要按项目约束选一个
- 需要参考现有代码风格
- 需要懂一两层抽象（接口、ORM、DB 事务）

**举例**：
- 实现一个具体 Sink（接口已定）
- 写 SQLAlchemy 模型 + 仓储层（schema 已定）
- 写 Alembic migration（变更已规划）
- 写 S3 multipart 上传 Go 函数（协议已查清）
- 改造 cosdrive 旧前端对接新路由（路由已定）

**派给**：L2 worker / Sonnet / Codex worker
**Review 强度**：读完整 diff，重点看是否破坏接口/约束，跑测试 + 跑端到端

### L3 — 设计型（需要权衡和上下文）

**特征**：
- 答案不唯一，权衡之间影响后续多个决策
- 涉及"为什么这样设计"的深度
- 决策后会被写进 ADR 或 BLUEPRINT

**举例**：
- Sink 接口 v2 怎么改
- Workspace 抽象的边界在哪
- 这个 race condition 怎么解
- 多租户隔离用 PG RLS 还是仓储层强制过滤
- Phase 之间的拆分粒度
- Kafka 主题设计 / partition key 选择
- 写 ADR / 改 BLUEPRINT
- 安全相关代码（鉴权、加密、签名）

**派给**：**主编排 Agent（不要委托给执行池）**
**Review 强度**：你和主编排 Agent 一起拍

---

## 四、绝对不委托的事

无论执行者多智能，下列工作必须由主编排 Agent 或你完成：

1. **改 BLUEPRINT.md** —— 项目北极星，执行者没有完整历史
2. **写新 ADR** —— 决策记录的灵魂是"为什么不选别的"，执行者缺权衡上下文
3. **改 Sink 接口签名** —— 一个签名改动会传染所有 adapter
4. **改安全相关代码** —— JWT、加密、签名、tenant 过滤拦截器、SQL 注入防护
5. **改 schema migration（已发布的）** —— 数据库 schema 变更不可回滚
6. **删除任何 `_legacy/`** —— 项目起源的实物证据
7. **执行 `git push --force` / `git reset --hard` / 删除分支或 tag**
8. **决定一个 Phase 是否完成** —— 这是你和主编排 Agent 的判断

---

## 五、派任务的标准模板

主编排 Agent 派执行者时，prompt 必须包含以下 6 段。**少一段都会增加返工概率**。

```markdown
## 1. 任务目标
（一句话说清要做什么。例："实现 control-plane/app/repos/workspace_repo.py，
提供 list/get/create 三个方法，所有查询强制按 tenant_id 过滤"）

## 2. 关键约束（不可违反）
- 约束 1（例：所有 query 必须经过 BaseRepository.with_tenant() 注入 tenant_id）
- 约束 2（例：使用 SQLAlchemy 2.0 风格，不用 legacy Query API）
- 约束 3（例：必须流式，不能 read_bytes 整文件入内存）

## 3. 上下文与参考
- 现有相关文件：（路径列表）
- 必读文档：BLUEPRINT § 8（数据模型）/ ADR XXXX
- 风格参考：control-plane/_legacy/smh_uploader/api_client.py 的 io 模式

## 4. 验收标准
- 测试命令：`cd control-plane && pytest tests/test_workspace_repo.py -v`
- 期望结果：全部通过；新增至少 X 个 test case
- 静态检查：`ruff check` 无错误（如已配）

## 5. 输出格式约束
- 不要写 README / 解释性 markdown
- 不要主动 commit；写完报告 diff 摘要 + 测试结果
- 不要修改任务目标外的文件（明确列出可改的路径）

## 6. 失败时的行为
- 如发现约束 1-3 与任务目标冲突，停下来报告，不要自己拍
- 如测试始终不通过，报告最后一次错误，不要为了通过而修改测试
```

### 反例（不要这样派）

| ❌ 错误派法 | 为什么错 |
|---|---|
| "帮我写 Phase 1" | 太大；执行者上下文不够；缺验收标准 |
| "你看着办" | 执行者会过度发挥，引入未授权抽象 |
| "尽量复用现有代码" | 模糊；执行者不知道哪些是"应该复用"哪些是"v0 留着参考的" |
| "不要破坏现有逻辑" | 模糊；不如列具体不可改的文件路径 |
| "顺便补点测试吧" | 顺便做的事质量最差；测试要么是任务一部分要么单独派 |

---

## 六、一个 Phase 的标准启动程序

每开始一个 Phase（如 Phase 1.1：项目骨架），按以下顺序：

### 步骤 1：你说要开始
> "开始 Phase 1.1 — control-plane Python 项目骨架"

### 步骤 2：主编排 Agent 拆任务
主编排 Agent 读 BLUEPRINT 相关章节，列子任务清单。每条带：
- L 等级（L1/L2/L3）
- 推荐执行方（主编排 Agent / L1 worker / L2 worker / Codex / aider）
- 验收方式
- 依赖关系（哪些必须先做）

### 步骤 3：你确认拆分 + 选模型
你看清单后说："1, 2 派低成本 worker；3 派 Codex 中档 worker（Claude 宿主可用 Sonnet）；4 由主编排 Agent 亲自写。"

### 步骤 4：主编排 Agent 执行
- L3 任务：主编排 Agent 亲自写
- L1/L2 任务：派执行者，按 § 五 的模板组织 prompt
- 派完**等待**子任务完成，不要并行派多个有依赖的任务
- 独立无依赖的任务可以并行派（一次消息派多个 Agent）

### 步骤 5：Review
- 主编排 Agent 读完整 diff（不只看摘要）
- 跑测试 + 跑端到端验收
- 检查是否破坏接口、是否引入未授权抽象、是否漏掉关键约束

### 步骤 6：报告给你
- 通过：报告 diff 概要 + 测试结果，等你批准
- 不通过：报告失败原因 + 决定是执行者重试还是主编排 Agent 接手

### 步骤 7：commit
你批准后，主编排 Agent 做 commit（commit message 由主编排 Agent 起草）。

### 步骤 8：进入下一个子任务
回到步骤 4。Phase 全部完成后回到步骤 1 启动下一个 Phase。

### 低成本编排默认模式

当主编排 Agent 处于低成本编排模式时：

- 主编排 Agent 默认只做：读必要上下文、拆任务、写 § 五 派工 prompt、review diff、跑测试、诊断失败、起草 commit message。
- L1 任务优先派 `aider+DeepSeek` 或低档 worker；L2 任务优先派低/中档 worker；L3 / 安全 / 承重墙任务仍由主编排 Agent 亲自掌握。
- 主编排 Agent 不为了"了解全局"一次性读全仓库；先用 `rg` / `rg --files` 定位，再读取任务相关文件和必须文档。
- 单个执行者任务默认限制在 1-3 个可改文件；超过 5 个文件或 120 行以上预期 patch 时，先拆分再派。
- Plan mode 用作**执行前审批门**：Phase 启动、跨模块任务、升档、aider 直接写文件、L3 设计前先给计划和派工方案，等用户确认。
- 主编排 Agent 可以亲自写代码的情况：§ 四 绝对不委托事项、L3 设计型任务、执行者连续失败后的接手、用户明确要求、或修复 review 中发现的小问题。
- 每次报告必须写清：实际执行方、模型/档位（如可见）、改动文件、测试命令和结果、是否偏离本计划。

---

## 七、执行池：模型选择与外部 plugin 集成

执行池里的模型分三类（参见 § 二 角色图）：

### 7.1 宿主内置子代理（Claude Agent / Codex spawn_agent）

**默认派工对象**。主编排 Agent 优先使用当前宿主提供的子代理能力：Claude Code 里通常是 `Agent` 工具，Codex 里通常是 `spawn_agent` / `worker` / `explorer`。工具名称不同，但派工规则相同：任务必须小、边界必须清、可改文件必须列明、完成后必须由主编排 Agent review。

- **L2 worker** — 默认承担模式型实现（具体 sink、仓储层、Alembic migration、Pydantic schema、有一定推理深度的代码）
- **L1 worker** — 默认承担机械型任务（写测试、scaffold、改 docstring、统一字段名）
- **explorer / read-only agent** — 只回答代码库问题，不直接改文件；适合并行摸清上下文

无需特殊治理——只要按 § 五 模板派，按 § 九 review 即可。

### 7.2 Plugin 接入的外部模型（mcp__codex / mcp__glm / mcp__deepseek / ...）

通过 MCP server、IDE plugin 或宿主工具的外部模型接口，把 GPT-5、GLM、DeepSeek 等接入主编排 Agent，成为执行池里的额外成员。**派工形态和内置子代理完全相同**——主编排 Agent 调用 `mcp__codex__write_code`、`spawn_agent`、`send_input` 或等价工具，按同一套规则约束。

#### 评估一个新 plugin 是否值得纳入执行池

装之前，过这 5 个问题：

| # | 问题 | 不通过则不装 |
|---|---|---|
| 1 | 工具协议是否能调用 Bash/Read/Edit/Write 同等能力？ | 否 → 只能做 review/suggest，不能实现代码 |
| 2 | 这个模型在某类任务上是否有**经过验证**的优势？ | 否 → 与现有 Sonnet/Haiku 同质，徒增混乱 |
| 3 | 价格/速度是否优于已有选项？ | 否 → 没有理由切走 |
| 4 | Plugin 维护者可信吗？plugin 是否会读取/上传你的代码到不受控的地方？ | 否 → 安全风险，拒绝 |
| 5 | Plugin 是否支持指定模型版本？（避免被偷偷升级降级） | 否 → 行为不可控，谨慎 |

#### 推荐的 plugin 与适用场景

| Plugin（示例） | 模型 | 优势 | 适合任务 |
|---|---|---|---|
| `openai/codex-plugin-cc` | GPT-5 / Codex | Go/系统编程倾向、推理深 | data-plane Go worker、io.Pipe 编排 |
| GLM / GLM-Coder plugin | GLM-4.6 / Coder | 极便宜、中文场景友好 | 批量 L1（中文注释、文档翻译、scaffold） |
| DeepSeek plugin | V3 / R1 | 推理强、价格极低 | L2 复杂逻辑、二次 review、调试 |

⚠️ **数字会变**。具体选型时按 7.3 的判断维度自行评估，不要硬套这张表。

#### Plugin 命名约定（你能一眼看出谁在干活）

- 工具名前缀 `mcp__codex__*` → GPT-5 在写
- `mcp__glm__*` → GLM 在写
- `mcp__deepseek__*` → DeepSeek 在写
- `Agent(...)` / `spawn_agent(...)` → 宿主内置子代理在写

主编排 Agent 派工时**报告执行方是谁**（"我让 Codex worker 写 S3 sink"、"我让 aider+DeepSeek 写测试"），方便你判断是否需要换模型。

### 7.3 选模型的 6 个判断维度

不要按"哪个最强"选——按下面这套维度对任务的契合度选：

| 维度 | 含义 | 影响什么 |
|---|---|---|
| **代码任务质量** | 写 Go/Python 的能力 | 直接决定能不能用 |
| **指令服从度** | 是否会"自由发挥"超出 prompt | 决定 review 成本 |
| **上下文长度** | 能塞多少现有代码作参考 | 决定能不能处理大型重构 |
| **价格 / token** | 单次成本 | 决定能不能高频派 |
| **响应速度** | 等结果的体感 | 影响开发节奏 |
| **工具调用能力** | 能否多步调用 Bash/Read/Edit | 决定能否独立完成任务 |

### 7.4 外部独立工具（IDE/终端）

不通过主编排 Agent、你直接在 IDE / 终端用的工具：

#### IDE Codex Plugin（VS Code / Cursor 内）
- **用法**：你 IDE 里打字时直接用，commit 前自己看 diff
- **适合**：L1 打字加速——补全、改名、scaffold 几行
- **风险**：会"创造性发挥"，commit 前必须 review

#### aider / continue（终端 REPL，独立使用）
- **用法**：终端里和单个文件对话式编辑
- **适合**：L1/L2 单文件局部修改
- **风险**：会绕过主编排 Agent review 直接写文件 + 默认 auto-commit；注意工作区干净
- **不要**：让它跨多文件做大规模改动——这种该派给主编排 Agent 编排

### 7.4.1 主编排 Agent 通过 Bash 派工给 aider（推荐组合）

**这是性价比高、宿主无关的执行方式**——保留主编排 Agent 的架构权和 review 权，把局部编辑交给 aider，底层模型可用 DeepSeek、GLM 或其他便宜模型控制成本。

#### 为什么是 Bash 而不是 MCP plugin

社区有 `sengokudaikon/aider-mcp-server` 之类把 aider 包装成 MCP tool 的项目，但目前都是早期状态（star 数低、commit 少）。Bash 路径有三个优势：
1. 零依赖、零配置——`pip install aider-chat` 完事
2. 主编排 Agent 直接看到 stdout/stderr 和 git diff，反馈结构化
3. 想拔掉时 `unset` 环境变量即可，不污染项目

未来 MCP plugin 成熟（200+ star、活跃维护）再迁移。

#### 一次性环境配置

```bash
# 装 aider
pip install aider-chat

# 配 DeepSeek 凭证（写到 ~/.bashrc 或 ~/.zshrc）
export DEEPSEEK_API_KEY="sk-..."

# 验证
aider --model deepseek/deepseek-chat --no-stream --version
```

DeepSeek 当前定价 ~¥1/M input、¥2/M output（远低于 Anthropic 1-2 个数量级），适合 L1 批量。

#### 主编排 Agent 调用 aider 的标准模板

主编排 Agent 派 aider 任务时，Bash 命令必须满足以下结构：

```bash
aider \
  --model deepseek/deepseek-chat \
  --no-auto-commits \
  --yes \
  --no-stream \
  --message "<本次任务的具体指令，遵循 § 五 模板>" \
  <可改文件路径 1> \
  <可改文件路径 2> \
  ...
```

**关键参数说明**：

| 参数 | 作用 | 为什么必须 |
|---|---|---|
| `--model deepseek/deepseek-chat` | 指定后端模型 | 如不指定 aider 默认用 Anthropic，违背成本初衷 |
| `--no-auto-commits` | 禁止 aider 自己 git commit | **commit 权必须留给主编排 Agent + 你**（参见失败模式 I） |
| `--yes` | 不进交互确认 | 主编排 Agent 不能回答交互式提示 |
| `--no-stream` | 不流式输出 | 让 stdout 一次性给主编排 Agent 读 |
| `--message "..."` | 任务指令 | 沿用 § 五 6 段模板，写清约束和验收 |
| `<文件路径>` 显式列出 | 圈定可改范围 | 防止 aider 自由发挥改其他文件 |

#### 典型适用任务

| 任务 | 命令草稿 |
|---|---|
| 写 pytest 单测覆盖某个函数 | `aider ... --message "为 classifier.classify_files 写 5 个 table-driven test，覆盖 zip slip / 中文 / 空文件 / ignored / 不匹配团队" control-plane/app/services/classifier.py control-plane/tests/unit/test_classifier.py` |
| 给一个 service 加中文 docstring | `aider ... --message "给所有 public 方法加中文 docstring，说明参数和返回值" control-plane/app/services/foo.py` |
| 批量补 type hint | `aider ... --message "补全本文件所有 def/return 的 type hint，使用 from __future__ import annotations" <files>` |
| scaffold 5 个相似 endpoint | `aider ... --message "按 list_workspaces 的模式新增 list_objects, list_tasks, list_users, list_audit, list_notifications" <files>` |

#### 主编排 Agent 派 aider 的工作流

按 § 六 标准启动程序的修改版：

1. 主编排 Agent 拆任务到 L1 级别 + 列可改文件白名单
2. 主编排 Agent 写完整 `aider` Bash 命令（含 `--message` 完整指令）
3. **执行前先把命令展示给你确认**——比派子代理多一步，因为 aider 直接写文件无 diff preview
4. 执行 → 读 stdout + 跑 `git diff` → 跑测试
5. 通过则主编排 Agent 起草 commit message → 你批准 → `git commit`
6. 不通过则回滚本次 aider 变更，决定是 aider 重派、换子代理，还是主编排 Agent 自己接手

#### 不适合派 aider 的任务

- L3（架构、ADR、安全相关）——按 § 三 分级原则
- 跨 5+ 文件的修改——超出 aider 单次 context 能力
- 需要先读懂大量上下文再写少量代码——aider 不擅长"大读小写"，主编排 Agent + 子代理更合适
- 需要 review 别人代码并指出问题——这是模型推理任务，aider 是编辑工具不擅长

#### 成本预估

按 DeepSeek 当前定价，跑一个"为 classifier 写 5 个单测"任务大约消耗 ~10K input + ~3K output token，**总成本 < ¥0.02**。同样任务派高阶子代理大约 ¥1+。L1 批量场景 50-100x 价差。

### 7.5 不要做的集成

- ❌ **把主编排 Agent 替换成没有完整工具集成的纯聊天模型**。如果一个模型不能稳定读文件、改文件、跑命令、看 diff、调用执行者，它只能做 review/suggest，不能承担编排权。
- ❌ **多 agent 自动化**（执行者自己规划+实现+commit）。失去对架构和风格的控制。
- ❌ **让执行者再调执行者**。嵌套两层以上会丢上下文、扩大写入范围、增加 review 成本。
- ❌ **让 plugin agent 直接 push 代码**。所有 commit 必须经主编排 Agent review + 你批准。
- ❌ **同一文件长期混用多个 plugin**。会导致代码风格碎裂（参见 § 十 失败模式 F）。

---

## 八、模块选择速查表

每类任务给出**首选**和**备选**两个选项。备选用于：① 首选模型当时不可用 ② 想做"双人 review"对照 ③ 首选连续失败时换一个试试。

宿主内置子代理语法见各宿主的 host 文件（`.claude/CLAUDE.md` / `AGENTS.md`）。

最后一列写**判断要点**——为什么这类任务首选这个模型。

| 任务类型 | 首选 | 备选 | 判断要点 |
|---|---|---|---|
| 写 ADR | 主编排 Agent | — | L3，需要项目历史与权衡 |
| 改 BLUEPRINT | 主编排 Agent | — | L3，承重墙级文档 |
| 改 Sink 接口 | 主编排 Agent | — | L3，签名变动传染所有 adapter |
| 安全/鉴权/加密代码 | 主编排 Agent | DeepSeek 二次 review | 安全代码不委托；DeepSeek 推理强，适合"第二只眼" |
| **写 Go sink adapter（S3/OSS）** | Codex worker | Sonnet | Go/系统编程倾向，io.Pipe 与 errgroup 模式更熟 |
| **写 Go pipeline 编排** | Codex worker | Sonnet | io.Pipe + TeeReader 模板化 |
| **写 Go kafka consumer** | Codex worker | Sonnet | 标准消费循环 |
| **写 Go ratelimit (Lua + AIMD)** | Codex worker（高档） | — | Lua 脚本 + 反压算法需要推理 |
| **写 Go resume / 状态机** | Codex worker（高档） | — | 一致性边界要细想 |
| **调试 Go race / 死锁** | 主编排 Agent | Codex worker（高档）/ DeepSeek-R1 | L3 复杂推理 |
| 写 Go table-driven 测试 | aider+DeepSeek | Haiku | L1 模式化，价格敏感 |
| 写 Python sink adapter（Phase 1） | L2 worker（Sonnet 或 Codex） | — | Python 业务代码是 L2 |
| 写 Python 仓储层 + 单测 | L2 worker（Sonnet 或 Codex） | DeepSeek 二次 review | SQLAlchemy 2.0 风格需要完整 review |
| 写 FastAPI 路由 + Pydantic schema | L2 worker | Haiku | L2 中等复杂度，按路由契约小批量派 |
| 写 Alembic migration | L2 worker + 主编排 Agent review | — | 涉及 schema 安全，必须人工/主编排层读完整 diff |
| 写 table-driven 单元测试 | aider+DeepSeek (Bash) | Haiku | DeepSeek 价格低 50-100×；aider 自带 git 集成省事 |
| scaffold 重复模式 | aider+DeepSeek (Bash) | Haiku | 同上；批量场景成本敏感 |
| 改 docstring / 字段名 | aider+DeepSeek (Bash) | IDE Codex Plugin | 单文件局部修改 aider 强项 |
| 写中文注释 / 翻译文档 | aider+DeepSeek (Bash) | GLM / Sonnet | DeepSeek 中文好且便宜 |
| 跑测试 / 看日志 / 改 BUG | 主编排 Agent | — | debug 需要项目上下文，不委托 |
| 写 README / 子目录文档 | Sonnet | Haiku | L2，需指明边界（不能写承重墙） |
| 调研某技术（"S3 multipart 协议细节"） | Codex worker（必要时 WebSearch） | Sonnet | 调研结果必须落到接口约束和验收标准 |
| 复杂逻辑调试（race condition / 死锁） | 主编排 Agent | Codex worker（高档）/ DeepSeek-R1 | L3，需要深推理 |

### 同一模块固定主力模型

为避免风格漂移（见 § 十 失败模式 F），**同一目录下的代码长期由同一个主力模型实现**。具体档位见各宿主的 host 文件。

| 模块 | 主力模型 | 理由 |
|---|---|---|
| `data-plane/internal/sink/` | Codex (GPT-5) | Go sink adapter 模板化 |
| `data-plane/internal/pipeline/` | Codex (GPT-5) | io.Pipe 编排标准模式 |
| `data-plane/internal/kafka/` | Codex (GPT-5) | Kafka consumer 模板化 |
| `data-plane/internal/ratelimit/` | Codex (GPT-5) | Lua 脚本 + AIMD 算法需要推理 |
| `data-plane/internal/resume/` | Codex (GPT-5) | 状态机 + 一致性边界 |
| `data-plane/internal/progress/` | Codex (GPT-5) | Redis pub/sub 标准模式 |
| `data-plane/internal/observability/` | Codex (GPT-5) | OTel SDK 接入 |
| `data-plane/internal/source/` | Codex (GPT-5) | Source 实现 |
| `control-plane/app/services/` | Sonnet（Codex 宿主可用 Codex worker） | Python 业务逻辑 |
| `control-plane/app/repos/` | Sonnet（Codex 宿主可用 Codex worker） | SQLAlchemy ORM，完整 diff review |
| `control-plane/app/api/` | Sonnet（Codex 宿主可用 Codex worker） | FastAPI 路由，按 endpoint 分批 |
| `control-plane/app/core/` | Sonnet（Codex 宿主可用 Codex worker） | 配置/启动/中间件，涉及安全时不委托 |
| `control-plane/tests/` | aider+DeepSeek | L1 批量，价格敏感 |
| `data-plane/internal/*/test*.go` | aider+DeepSeek | 同上 |

特殊情况偏离时（如低成本 worker 失败后升高档），在 commit message 里写明。

---

## 九、质量底线（无论谁写，都必须达到）

1. **没有 mock 顶替真实实现**：测试可以 mock，主代码不能用 stub 假装实现
2. **没有 silent failure**：try/except 必须 log 或重新抛出，不能 `except: pass`
3. **没有硬编码凭证**：所有 secret 来自环境变量或 settings
4. **测试覆盖核心路径**：新加 service / repo 必须有测试，覆盖率目标 70%+
5. **不重复实现已有抽象**：发现"和 X 几乎一样"时，先复用 X 或抽取共同抽象，不要复制粘贴
6. **流式优先**：处理文件必须流式，禁止 `read_bytes()` / `BytesIO` 整文件入内存（除非文件 < 1MB 且明确说明）
7. **多租户安全**：任何查询用户数据的地方必须经过 tenant_id 过滤拦截器

---

## 十、当执行者出错时

### 失败模式 A：测试通过但代码错
**症状**：执行者为了让测试通过修改了测试。
**对策**：测试由主编排 Agent 起草或人审通过后才允许执行者实现；执行者不能改测试。

### 失败模式 B：破坏未列出的文件
**症状**：执行者把 task 范围外的文件也改了。
**对策**：派任务时显式列出可改路径（§ 五 模板第 5 段）。

### 失败模式 C：引入未授权抽象
**症状**：执行者在解决具体问题时新建一个 BaseXxx 抽象类。
**对策**：约束里明确写"不要引入新抽象层；如必要，停下来报告"。

### 失败模式 D：跨语言污染
**症状**：执行者在 Go 项目里写出 Python 风格代码（或反之）。
**对策**：派任务时指明语言习惯（"用 Go errgroup 模式"、"用 Python type hint + Pydantic"）。

### 失败模式 E：context lost
**症状**：执行者不知道项目历史决策，写出和 BLUEPRINT 冲突的代码。
**对策**：约束里明确链接相关 BLUEPRINT 章节和 ADR 编号；执行者必须读完再写。

### 失败模式 F：plugin 模型行为漂移 / 风格碎裂
**症状**：同一个目录的代码先后由 Sonnet、Codex、GLM 写过，命名风格、错误处理模式、日志格式、注释密度全部不一致；几个月后 review 像是不同人写的。
**对策**：
- § 八 表里给每个模块固定一个**主力模型**，长期由它实现
- 偏离主力时（如临时换 Codex），在 commit message 里说明原因
- 定期（如每个 Phase 结束）让主力模型扫一遍本模块统一风格

### 失败模式 G：plugin 工具协议不兼容
**症状**：装了某 plugin 后，主编排 Agent 调用 `mcp__xxx__write_code` 时输出格式不规整、不会调用 Read/Edit、丢失 context、无法多步操作。
**对策**：
- 装新 plugin 后**先派一个小任务测试**（如"读 README 然后写一段 hello world"），观察是否能完成完整 read → write 闭环
- 测试不通过的 plugin 只用于 review/suggest，不用于实现代码
- 在 § 七 评估清单的第 1 题（工具协议能力）卡住

### 失败模式 H：plugin 隐性数据上传
**症状**：某些 plugin 会把代码片段或 prompt 上传到不受控的中转服务（"加速"、"缓存"、"分析"），可能泄漏 secret 或私有逻辑。
**对策**：
- § 七 评估清单第 4 题必过：plugin 维护者可信、文档明确数据流向
- 给 plugin 派任务时不带凭证、不带客户数据
- 仓库根目录 `.env` / `*.pem` 已在 `.gitignore`，但要警惕 plugin 直接读文件系统

### 失败模式 I：aider 自动 commit 绕过主编排 Agent
**症状**：aider 默认 `--auto-commits=true`，每次成功编辑就直接 `git add + git commit`。结果：
- commit message 由 aider 自己写（不符合项目规范）
- 主编排 Agent review 流程被跳过
- 一个任务可能产生 3-5 个零碎 commit 而不是一个有意义的 commit
- 你失去对 git 历史的控制

**对策**：
- 主编排 Agent 调 aider 时**永远带 `--no-auto-commits`**（已写入 § 7.4.1 标准模板）
- 主编排 Agent 执行 aider 后，自己跑 `git diff` review，再起草 commit message → 等你批准 → 一次性 commit
- 偶尔 aider 不听话仍 commit 了，先 `git reset --soft HEAD~N` 把改动退回工作区，重新走流程

### 失败模式 J：执行者超时但已有半成品补丁
**症状**：执行者长时间不返回，主编排 Agent 以为没有产出；恢复诊断后发现执行者已经读完上下文并开始写补丁，但大补丁尚未完成或尚未同步到主工作区。典型诱因：
- 当前环境普通 Bash 被沙箱拦截，例如 `bwrap: setting up uid map: Permission denied`，执行者需要重试或申请更高权限读取上下文。
- 单次派工写入范围过大，例如一次新增 3 个测试文件、10+ 个测试 case、300+ 行补丁。
- 主编排 Agent 在超时后直接关闭执行者，没有先询问进度。

**对策**：
- 写入型任务按文件或子契约拆分：一次只让执行者改 1 个测试文件或 1 个实现文件；每个 patch 尽量控制在 60-120 行。
- 对 TDD red tests，优先拆成 `TaskRepo tests`、`ItemRepo tests`、`EventRepo tests` 这种单文件任务；不要一次性派完整模块测试。
- 执行者超时后，先 `send_input` 要求输出当前进度、已改文件、阻塞原因；确认没有可合并产物后再关闭。
- 如果执行者报告环境命令失败，主编排 Agent 先自己确认是否是宿主沙箱问题，再决定重派、缩小任务或接手。
- 主编排 Agent 接手前必须跑 `git status` / `git diff --name-only`，避免覆盖执行者或用户已经写出的半成品。

---

## 十一、版本

- **v1.0**（2026-05-08）：初版，配套 BLUEPRINT v1.4 的 Phase 1 启动。
- **v1.1**（2026-05-09）：扩展执行池治理。§ 二 角色图加"执行池"；§ 七 重构为"执行池：模型选择与外部 plugin 集成"，加 plugin 评估清单 + 命名约定 + 6 维选模型判断；§ 八 速查表升级为"首选 + 备选"双列，新增"同一模块固定主力模型"小节；§ 十 新增失败模式 F (风格碎裂)、G (plugin 工具协议不兼容)、H (plugin 隐性数据上传)。
- **v1.2**（2026-05-09）：集成 aider + DeepSeek via Bash 作为 L1 批量任务的省钱选项。§ 二 角色图加"Bash 派工"分类；新增 § 7.4.1 主编排 Agent 通过 Bash 派工给 aider，含环境配置 / 标准命令模板 / 关键参数说明 / 典型适用任务 / 不适合任务；§ 八 速查表 4 类 L1 任务首选改为 aider+DeepSeek，模块主力模型表 tests/ 目录改为 aider+DeepSeek；§ 十 新增失败模式 I (aider 自动 commit 绕过主编排 Agent)。
- **v1.3**（2026-05-10）：Codex 档位制度落地。§ 八 速查表 Go 任务列加档位（low/medium/high/xhigh），模块主力模型表加"默认档位"列，新增 § 八.5 升档/降档触发信号，确立方案 B 派工默认（按默认档自动派 + 触发信号才确认 + 用户随时覆盖）。
- **v1.4**（2026-05-10）：把协作规范从 Claude/Opus 绑定表述改为宿主无关的"主编排 Agent + 执行池"模型；明确 Codex 可作为主编排 Agent，并可用 `spawn_agent` / worker / explorer / aider 按同一套派工、review、commit 门禁执行。
- **v1.5**（2026-05-10）：记录执行者超时半成品补丁失败模式。§ 二 宿主适配规则补充 `bwrap`/沙箱失败提示；§ 十 新增失败模式 J，要求写入型任务按文件拆分、超时先问诊断再关闭。
- **v1.6**（2026-05-11）：确立 Codex 宿主下的低成本编排默认模式。§ 六 新增低成本编排默认模式，要求主编排 Agent 默认承担规划/派工/review/test/commit draft，L1/L2 优先交给低成本执行者；§ 八 新增 Codex 宿主默认梯度，更新 Python/control-plane 默认执行方；§ 八.5 改为低成本默认升降档规则。
- **v1.7**（2026-05-11）：将宿主特定规则从本文件拆分到各宿主 host 文件。原 `AGENTS.md` 改名为 `DISPATCH.md` 作为宿主无关共用规范；新建 `AGENTS.md`（Codex 宿主，含档位/spawn_agent 语法/模块梯度）；更新 `.claude/CLAUDE.md`（Claude Code 宿主，含 `Agent` 工具语法/plan mode/skills 优先级）。§ 八 速查表去除宿主语法细节，主力模型表保留模型归属，档位细节见 `AGENTS.md`。
- 每次发现新失败模式，更新 § 十；每次工作流改动，更新 § 六；每次执行池新成员，更新 § 七 与 § 八。
