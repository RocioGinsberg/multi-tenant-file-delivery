---
name: tdd-flow
description: Use this skill when the user wants to drive a task using test-driven development or contract-first design. Triggers when the user says "TDD"、"先写测试"、"contract-first"、"先定测试再写实现"、"用 TDD 跑 X". The skill expands the 8-step TDD workflow with templates for spec drafting, test-writing dispatch (Sonnet, never aider for contract-level tests), and implementation dispatch with the "no test modification" hard constraint. Use this for tasks marked as TDD in docs/plans/phase-*.md. NOT for already-passing tests, retroactive test additions, or end-to-end test suites.
---

# tdd-flow

驱动一个 TDD（测试驱动开发）/ contract-first 任务的标准 8 步流程。

## 何时调用此 skill

- 用户明示"用 TDD 跑 X"、"先写测试再写实现"、"contract-first X"
- `docs/plans/phase-*.md` 中某子任务标记为 TDD（"TDD?" 列为 ✅）
- 主对话主动判断这个任务是承重墙级契约（接口稳定性高、未来多模块依赖、契约清晰），建议走 TDD

**不要在这些情况调用**：
- 仅给已有实现补测试（这是回归测试，不是 TDD）
- L1 量产单测（如 1.11 classifier 5 个 case 的批量补测）—— 直接派 aider-dispatch
- 端到端 E2E 测试（这是 Phase 验收，不是单模块契约）
- 框架/配置类代码（如 1.1 项目骨架、1.2 settings）—— 没有"行为契约"可测

## 8 步流程

### 步骤 1：起草测试 spec

主对话写一份"测试 spec"，写入对应 `docs/plans/phase-N-*.md` 的子任务下。spec 必须包含：

```markdown
#### TDD spec — <任务名>

**一句话验收标准**：<例：能正确按 tenant_id 过滤所有 list 查询>

**主要 case**：
1. <case 1：happy path>
2. <case 2：边界 / 异常>
3. <case N>

**边界条件**（必测）：
- <空输入 / 空集合 / 空字符串>
- <并发场景如适用>
- <跨进程/跨事务边界如适用>

**不验证什么**（明确 scope-out）：
- <这一层不该测的实现细节>
- <留给上层的集成测试>

**测试基础设施**：
- 用 in-memory SQLite / pytest fixture / mock 对象等
```

### 步骤 2：用户 review spec（不可跳过）

把 spec 完整展示给用户，**明确等用户回复"通过"或提出调整**。

**不要默认通过**——这是 TDD 价值的核心。用户至少要看：
- case 列表是否覆盖关心的场景
- 是否漏了边界
- "不验证什么"是否合理（不要把活推到上层）

用户可能：
- 通过 → 进步骤 3
- 加 case → 修改 spec 重新 review
- 改方向 → 调整 spec
- 觉得不该 TDD → 退出 skill，走普通派工

### 步骤 3：派工写测试代码

**首选 Sonnet subagent**（不是 aider！）—— 承重墙级测试质量比代码还关键，便宜模型不可控。

派工 prompt 模板（按 AGENTS.md § 五 6 段）：

```markdown
## 1. 任务目标
按 docs/plans/phase-N-*.md 中 <任务 X.Y> 的 TDD spec 写测试代码。

## 2. 关键约束（不可违反）
- 测试**应该全部失败**（因为实现还没写）——你写完只跑测试确认它们 fail，不要为了让它通过而 mock 实现
- 不要写实现代码，只写 tests/test_X.py
- 不要添加 spec 中没有的 case（spec 是契约，超出范围请停下来报告）
- 用 spec 指定的测试基础设施（in-memory SQLite / fixture 等）
- 严格 table-driven，每个 case 一个 dict 输入

## 3. 上下文与参考
- TDD spec：docs/plans/phase-N-*.md 第 X.Y 节
- 必读：BLUEPRINT 第 ? 节、ADR ?
- 风格参考：（如有，列出已存在的同类测试文件路径）

## 4. 验收标准
- pytest tests/test_X.py 跑出来：N 个 test 全 fail（因为实现尚未存在）；不应该出 syntax error 或 import error
- 测试代码可以 import 一个还没存在的实现路径，pytest 报 ModuleNotFoundError 是正常的

## 5. 输出格式约束
- 不要 commit
- 不要写 README/解释
- 只改 tests/test_X.py（如需 fixture，写在文件内或 conftest.py）

## 6. 失败时的行为
- 如果 spec 里某 case 你觉得不合理，停下来报告，不要自己修改 spec
- 如果发现需要新建文件夹，停下来报告
```

### 步骤 4：review 测试代码

主对话和用户两层 review：

**主对话 review**：
- 跑 `pytest tests/test_X.py` 应全 fail（确认 RED 状态）
- 读完整代码：是否真的覆盖 spec 里所有 case
- 是否引入了 spec 之外的"创造性"测试
- 是否真的会 fail（不要写出永远 pass 的测试）

**用户 review**：
- 主对话把测试代码展示给用户看
- 用户至少快速浏览：case 名是否清晰、断言是否有意义、是否漏 case

### 步骤 5：测试单独 commit（红色基线钉死）

```bash
git add tests/test_X.py
git commit -m "phaseN(X.Y): test spec for X (red)

8-step TDD flow step 5/8 — locks the test contract before
implementation. All tests intentionally failing.

<其他正常 commit footer>"
```

**关键**：commit message 必须含 "(red)" 标识 + "step 5/8"，便于 git log 辨识 TDD 流程。

### 步骤 6：派工写实现

派工对象按任务规模决定：
- L2（SQLAlchemy 仓储、Pydantic schema）→ Sonnet subagent
- L2（Go sink）→ Codex
- L3（流式上传内核、状态机）→ **Opus 主对话亲自写**

派工 prompt 模板：

```markdown
## 1. 任务目标
按 tests/test_X.py 中已写好的测试，实现 <模块路径>，让所有测试通过。

## 2. 关键约束（不可违反）
- ❌ **绝对禁止修改 tests/test_X.py 任何一行**——如发现测试有问题，停下来报告，不要自己改
- ❌ 禁止用 mock 顶替真实实现
- 实现风格遵循：BLUEPRINT 第 ? 节、AGENTS.md § 九 质量底线
- 错误处理 / 流式 / 类型检查等参见 § 九

## 3. 上下文与参考
- 测试代码：tests/test_X.py（这是契约，必读）
- 相关接口：<相关文件路径>
- 风格参考：<existing similar implementation>

## 4. 验收标准
- pytest tests/test_X.py 全过
- ruff check 无 error
- 不引入未授权抽象（参见 AGENTS.md § 十 失败模式 C）

## 5. 输出格式约束
- 不要 commit
- 只改 <模块路径> 及其依赖（明确列出可改文件）
- tests/ 路径下的文件**完全不许动**

## 6. 失败时的行为
- 如果某测试始终过不了，停下来报告最后一次错误
- 不要修改测试或加跳过标记
- 不要"绕过"——比如把困难的 case 实现成 stub
```

### 步骤 7：review 实现

主对话：
- 跑 `pytest tests/test_X.py` 全过 = GREEN
- 跑 `git diff tests/` 必须为空——验证实现阶段没动测试
- 读 diff 检查实现质量
- 如果有 fail：决定是 subagent 重派 / 换模型 / 主对话接手；**不允许改测试**

用户 review（可选，简短）：
- 看 diff 摘要，确认方向对

### 步骤 8：实现 commit（绿色）

```bash
git add <impl files>
git commit -m "phaseN(X.Y): impl X (green)

8-step TDD flow step 8/8 — all tests in test_X.py pass.
Test file unchanged since (red) commit at <hash>.

<其他正常 commit footer>"
```

**关键**：commit message 含 "(green)" + "step 8/8" + 引用 (red) commit hash，让 git log 能完整看到 RED → GREEN 转换。

## 失败模式 playbook

### F1：subagent 写完测试，却"顺手"把实现也写了

**症状**：步骤 3 的 subagent 没遵守"只写测试不写实现"，把 `app/services/X.py` 也填上了能让测试过的代码。

**对策**：
- `git restore app/services/X.py`（如果还没 commit）或 `git reset --soft HEAD~1`（如果误 commit）
- 重派步骤 3，prompt 里明确加："禁止创建或修改 tests/ 之外的任何代码"
- 这个失败模式记到 commit message 草稿里，将来 review TDD 流程时能看到

### F2：subagent 在写实现时把测试改了让它过

**症状**：步骤 6 派工后，测试通过了，但 `git diff tests/` 不为空。

**对策**：
- `git restore tests/test_X.py`（恢复 RED commit 时的状态）
- 重新跑测试看实际是 fail 还是 pass
- 如果 fail，重派步骤 6，prompt 里加："tests/ 已被恢复，禁止再次修改"
- 如果用户 review 后觉得测试本身确实写错了：单独走"step 5.5: test spec adjustment"，commit "phaseN(X.Y): adjust test spec for X (red v2)"，再回到步骤 6

### F3：spec 写得太死，实现时发现接口签名要改

**症状**：步骤 6 实现时发现 spec 里测的接口签名不合理。

**对策**：
- 停下来报告
- 不要绕过——回到步骤 1 修订 spec，重走 2-5
- 这正是 TDD 的"early failure"价值：在写实现前发现契约错了，比写完才发现便宜

### F4：测试本身有 bug 导致永远 pass

**症状**：步骤 4 review 时漏了，步骤 6 实现"过"了但用户 review 实现发现根本没真测什么。

**对策**：
- 退回步骤 1 重写 spec（明确这一类断言要怎么写）
- 重做步骤 3-5
- 这种情况说明步骤 2 的用户 review 没起到防护作用——下次更仔细

## skill 输出形式

调用 skill 后，主对话应该：
1. 先打印当前在 8 步流程的哪一步
2. 给出该步要做的具体动作（draft spec / dispatch / review）
3. 等待用户 / subagent / 实现方完成 → 进下一步

**主对话不能跳步**——每一步都对应一个 commit / review checkpoint，跳步等于破坏契约。

## 推荐与禁忌

✅ 推荐使用 TDD 的任务：
- Repo / DAO 接口（CRUD + 强制过滤拦截器）
- 分类器 / 规则引擎（输入 → 输出确定）
- Sink 接口实现（接口稳定性高）
- Source 接口实现
- Workspace 抽象层
- 鉴权中间件

❌ 不推荐用 TDD 的任务：
- 项目骨架 / 配置 / 中间件接入（无行为契约）
- 数据库 schema / migration（声明式）
- 状态机编排（应该用 E2E 而不是单测）
- UI / 前端组件（应该用集成测试）
- 调试 / 排查 / 日志改善（任务本身就不是契约）
