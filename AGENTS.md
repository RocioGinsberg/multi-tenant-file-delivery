# AGENTS.md — 多模型协作执行规范

> 这是项目的"协作合同"——规定主对话（Claude Code Opus）、subagent（Sonnet/Haiku）、外部模型（Codex Plugin / GLM / DeepSeek）和你（产品负责人 + 终审）之间的责任划分。
>
> **核心原则**：架构权留在主对话，执行权下放给 subagent，安全/承重墙不委托。

---

## 一、为什么需要这份文档

代码实现可以委托，但**项目方向感不能委托**。多 agent 自动化最常见的失败模式是：subagent 写出"看起来对、跑得通、但破坏承重墙决策"的代码——review 成本和自己写差不多，反而更慢。

这份文档定义"什么任务派给什么模型"和"派任务时必须带什么上下文"，确保委托是省时间的，不是省麻烦堆给未来。

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
│  Claude Code 主对话（Opus）                      │
│   架构权 + 编排权                                │
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
   │  全部归同一个派工层，由主对话编排        │
   │                                         │
   │  内置（Agent 工具直接派）：             │
   │  ├─ Sonnet subagent ─ L2 默认           │
   │  └─ Haiku subagent  ─ L1 默认           │
   │                                         │
   │  Plugin 接入（mcp__*__* 工具）：        │
   │  ├─ mcp__codex__*    ─ GPT-5 / Codex    │
   │  ├─ mcp__glm__*      ─ GLM-4.6/Coder    │
   │  ├─ mcp__deepseek__* ─ DeepSeek-V3/R1   │
   │  └─ ...                                 │
   │                                         │
   │  外部独立工具（不经主对话）：           │
   │  ├─ IDE Codex Plugin  ─ 你 IDE 内打字   │
   │  └─ aider / continue  ─ 终端 REPL       │
   └─────────────────────────────────────────┘
```

**关键认知**：所有 plugin 接入的模型（Codex / GLM / DeepSeek / ...）和内置 Sonnet/Haiku 在**派工形态上是同等的**——都是主对话调用的一个工具，差异只在模型本身（质量、价格、长处）。**主对话作为编排者的责任不变**：拆任务、写约束、review、决定 commit。

执行池里加新成员的成本接近于零（装个 plugin），但**派工的质量门槛不降**——任何模型都要按 § 五 的 6 段模板派任务，按 § 九 的质量底线 review。

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

**派给**：Haiku subagent / IDE Codex Plugin
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

**派给**：Sonnet subagent
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

**派给**：**主对话 Opus（不要委托）**
**Review 强度**：你和主对话一起拍

---

## 四、绝对不委托的事

无论 subagent 多智能，下列工作必须由主对话或你完成：

1. **改 BLUEPRINT.md** —— 项目北极星，subagent 没有完整历史
2. **写新 ADR** —— 决策记录的灵魂是"为什么不选别的"，subagent 缺权衡上下文
3. **改 Sink 接口签名** —— 一个签名改动会传染所有 adapter
4. **改安全相关代码** —— JWT、加密、签名、tenant 过滤拦截器、SQL 注入防护
5. **改 schema migration（已发布的）** —— 数据库 schema 变更不可回滚
6. **删除任何 `_legacy/`** —— 项目起源的实物证据
7. **执行 `git push --force` / `git reset --hard` / 删除分支或 tag**
8. **决定一个 Phase 是否完成** —— 这是你和主对话的判断

---

## 五、派任务的标准模板

主对话派 subagent 时，prompt 必须包含以下 6 段。**少一段都会增加返工概率**。

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
| "帮我写 Phase 1" | 太大；subagent 上下文不够；缺验收标准 |
| "你看着办" | subagent 会过度发挥，引入未授权抽象 |
| "尽量复用现有代码" | 模糊；subagent 不知道哪些是"应该复用"哪些是"v0 留着参考的" |
| "不要破坏现有逻辑" | 模糊；不如列具体不可改的文件路径 |
| "顺便补点测试吧" | 顺便做的事质量最差；测试要么是任务一部分要么单独派 |

---

## 六、一个 Phase 的标准启动程序

每开始一个 Phase（如 Phase 1.1：项目骨架），按以下顺序：

### 步骤 1：你说要开始
> "开始 Phase 1.1 — control-plane Python 项目骨架"

### 步骤 2：主对话拆任务
主对话读 BLUEPRINT 相关章节，列子任务清单。每条带：
- L 等级（L1/L2/L3）
- 推荐执行方（Opus / Sonnet / Haiku / Codex）
- 验收方式
- 依赖关系（哪些必须先做）

### 步骤 3：你确认拆分 + 选模型
你看清单后说："1, 2 派 Haiku；3 派 Sonnet；4 你（Opus）自己写。"

### 步骤 4：主对话执行
- L3 任务：主对话亲自写
- L1/L2 任务：派 subagent，按 § 五 的模板组织 prompt
- 派完**等待**子任务完成，不要并行派多个有依赖的任务
- 独立无依赖的任务可以并行派（一次消息派多个 Agent）

### 步骤 5：Review
- 主对话读完整 diff（不只看摘要）
- 跑测试 + 跑端到端验收
- 检查是否破坏接口、是否引入未授权抽象、是否漏掉关键约束

### 步骤 6：报告给你
- 通过：报告 diff 概要 + 测试结果，等你批准
- 不通过：报告失败原因 + 决定是 subagent 重试还是 Opus 接手

### 步骤 7：commit
你批准后，主对话做 commit（commit message 由主对话起草）。

### 步骤 8：进入下一个子任务
回到步骤 4。Phase 全部完成后回到步骤 1 启动下一个 Phase。

---

## 七、执行池：模型选择与外部 plugin 集成

执行池里的模型分三类（参见 § 二 角色图）：

### 7.1 内置 subagent（Sonnet / Haiku）

**默认派工对象**。主对话用 `Agent` 工具直接派，工具集成（Bash/Read/Edit/Write）天然完整，无需配置。

- **Sonnet** — L2 默认（实现具体 sink、写仓储、Alembic migration、Pydantic schema、有一定推理深度的代码）
- **Haiku** — L1 默认（写测试、scaffold、改 docstring、统一字段名）

无需特殊治理——只要按 § 五 模板派，按 § 九 review 即可。

### 7.2 Plugin 接入的外部模型（mcp__codex / mcp__glm / mcp__deepseek / ...）

通过 MCP server 或 Claude Code plugin（如 `openai/codex-plugin-cc`）把 GPT-5、GLM、DeepSeek 等接入主对话，成为执行池里的额外成员。**派工形态和内置 subagent 完全相同**——主对话调用 `mcp__codex__write_code` 类工具，按同一套规则约束。

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
- `Agent(subagent_type=..., model="sonnet")` → 内置 Sonnet 在写

主对话派工时**报告执行方是谁**（"我让 Codex 写 S3 sink"），方便你判断是否需要换模型。

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

### 7.4 不进主对话的外部独立工具

不通过主对话、你直接在 IDE / 终端用的工具：

#### IDE Codex Plugin（VS Code / Cursor 内）
- **用法**：你 IDE 里打字时直接用，commit 前自己看 diff
- **适合**：L1 打字加速——补全、改名、scaffold 几行
- **风险**：会"创造性发挥"，commit 前必须 review

#### aider / continue（终端 REPL）
- **用法**：终端里和单个文件对话式编辑
- **适合**：L1/L2 单文件局部修改
- **风险**：会绕过 git 直接写文件，注意工作区干净
- **不要**：让它跨多文件做大规模改动——这种该派给主对话编排

### 7.5 不要做的集成

- ❌ **把 Claude Code 主对话替换成 GLM/DeepSeek**。它们没有 Claude Code 的工具集成（Bash/Read/Edit/Write/Agent），手动 wrap 工具调用得不偿失。
- ❌ **多 agent 自动化**（subagent 自己规划+实现+commit）。失去对架构和风格的控制。
- ❌ **让 subagent 调 subagent**。Claude Code Agent 工具支持嵌套，但 2 层以上几乎必出问题。
- ❌ **让 plugin agent 直接 push 代码**。所有 commit 必须经主对话 review + 你批准。
- ❌ **同一文件长期混用多个 plugin**。会导致代码风格碎裂（参见 § 十 失败模式 F）。

---

## 八、模型选择速查表

每类任务给出**首选**和**备选**两个选项。备选用于：① 首选模型当时不可用 ② 想做"双人 review"对照 ③ 首选连续失败时换一个试试。

最后一列写**判断要点**——为什么这类任务首选这个模型。

| 任务类型 | 首选 | 备选 | 判断要点 |
|---|---|---|---|
| 写 ADR | Opus 主对话 | — | L3，需要项目历史与权衡 |
| 改 BLUEPRINT | Opus 主对话 | — | L3，承重墙级文档 |
| 改 Sink 接口 | Opus 主对话 | — | L3，签名变动传染所有 adapter |
| 安全/鉴权/加密代码 | Opus 主对话 | DeepSeek 二次 review | 安全代码不委托；DeepSeek 推理强，适合"第二只眼" |
| **写 Go sink adapter（S3/OSS）** | Codex (GPT-5) | Sonnet | Go/系统编程倾向，io.Pipe 与 errgroup 模式更熟 |
| **写 Go pipeline / ratelimit / kafka** | Codex (GPT-5) | Sonnet | 同上，Go 数据面整体优先 Codex |
| 写 Python sink adapter（Phase 1） | Sonnet | Codex | Python 生态 Sonnet 更稳 |
| 写 Python 仓储层 + 单测 | Sonnet | DeepSeek | SQLAlchemy 2.0 风格 Sonnet 训练充分 |
| 写 FastAPI 路由 + Pydantic schema | Sonnet | Haiku | L2 中等复杂度 |
| 写 Alembic migration | Sonnet | — | 涉及 schema 安全，慎换模型 |
| 写 table-driven 单元测试 | Haiku | GLM | L1 高频，价格敏感优先 |
| scaffold 重复模式 | Haiku | GLM | L1，批量场景 GLM 更便宜 |
| 改 docstring / 字段名 | Haiku | IDE Codex Plugin | L1，IDE 内最快 |
| 写中文注释 / 翻译文档 | GLM | Sonnet | GLM 中文场景训练充分，价格低 |
| 跑测试 / 看日志 / 改 BUG | Opus 主对话 | — | debug 需要项目上下文，不委托 |
| 写 README / 子目录文档 | Sonnet | Haiku | L2，需指明边界（不能写承重墙） |
| 调研某技术（"S3 multipart 协议细节"） | Sonnet（+ WebSearch） | Codex | 调研 + 总结 Sonnet 平衡 |
| 复杂逻辑调试（race condition / 死锁） | Opus 主对话 | DeepSeek（让它分析输出） | L3，但 DeepSeek 推理强可作辅助 |

### 同一模块固定主力模型

为避免风格漂移（见 § 十 失败模式 F），**同一目录下的代码长期由同一个主力模型实现**。建议：

| 模块 | 主力模型 | 理由 |
|---|---|---|
| `data-plane/internal/sink/` | Codex (GPT-5) | Go 系统编程主场 |
| `data-plane/internal/pipeline/` | Codex (GPT-5) | 同上 |
| `data-plane/internal/kafka/` | Codex (GPT-5) | 同上 |
| `control-plane/app/services/` | Sonnet | Python 业务逻辑 |
| `control-plane/app/repos/` | Sonnet | SQLAlchemy ORM |
| `control-plane/app/api/` | Sonnet | FastAPI 路由 |
| `control-plane/tests/` | Haiku | L1 批量 |
| `data-plane/internal/*/test*.go` | Haiku | L1 批量 |

特殊情况偏离时（如 Sonnet 失败换 Codex），在 commit message 里写明。

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

## 十、当 subagent 出错时

### 失败模式 A：测试通过但代码错
**症状**：subagent 为了让测试通过修改了测试。
**对策**：测试由 Opus 起草或人审通过后才允许 subagent 实现；subagent 不能改测试。

### 失败模式 B：破坏未列出的文件
**症状**：subagent 把 task 范围外的文件也改了。
**对策**：派任务时显式列出可改路径（§ 五 模板第 5 段）。

### 失败模式 C：引入未授权抽象
**症状**：subagent 在解决具体问题时新建一个 BaseXxx 抽象类。
**对策**：约束里明确写"不要引入新抽象层；如必要，停下来报告"。

### 失败模式 D：跨语言污染
**症状**：subagent 在 Go 项目里写出 Python 风格代码（或反之）。
**对策**：派任务时指明语言习惯（"用 Go errgroup 模式"、"用 Python type hint + Pydantic"）。

### 失败模式 E：context lost
**症状**：subagent 不知道项目历史决策，写出和 BLUEPRINT 冲突的代码。
**对策**：约束里明确链接相关 BLUEPRINT 章节和 ADR 编号；subagent 必须读完再写。

### 失败模式 F：plugin 模型行为漂移 / 风格碎裂
**症状**：同一个目录的代码先后由 Sonnet、Codex、GLM 写过，命名风格、错误处理模式、日志格式、注释密度全部不一致；几个月后 review 像是不同人写的。
**对策**：
- § 八 表里给每个模块固定一个**主力模型**，长期由它实现
- 偏离主力时（如临时换 Codex），在 commit message 里说明原因
- 定期（如每个 Phase 结束）让主力模型扫一遍本模块统一风格

### 失败模式 G：plugin 工具协议不兼容
**症状**：装了某 plugin 后，主对话调用 `mcp__xxx__write_code` 时输出格式不规整、不会调用 Read/Edit、丢失 context、无法多步操作。
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

---

## 十一、版本

- **v1.0**（2026-05-08）：初版，配套 BLUEPRINT v1.4 的 Phase 1 启动。
- **v1.1**（2026-05-09）：扩展执行池治理。§ 二 角色图加"执行池"；§ 七 重构为"执行池：模型选择与外部 plugin 集成"，加 plugin 评估清单 + 命名约定 + 6 维选模型判断；§ 八 速查表升级为"首选 + 备选"双列，新增"同一模块固定主力模型"小节；§ 十 新增失败模式 F (风格碎裂)、G (plugin 工具协议不兼容)、H (plugin 隐性数据上传)。
- 每次发现新失败模式，更新 § 十；每次工作流改动，更新 § 六；每次执行池新成员，更新 § 七 与 § 八。
