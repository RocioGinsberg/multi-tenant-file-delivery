---
name: aider-dispatch
description: Use this skill when the main conversation needs to dispatch a coding task to aider with a DeepSeek backend via Bash. Triggers when the user says "派 aider"、"用 aider 跑"、"dispatch aider"、"let aider write"、"aider 写". Suitable for L1 batch tasks (writing tests, scaffolding, docstring/comment additions, mechanical renames, batch translations) where DeepSeek is 50-100x cheaper than Sonnet/Opus and aider's git integration handles edits cleanly. NOT suitable for L3 architecture tasks, multi-file refactors spanning 5+ files, or tasks requiring deep project context.
---

# aider-dispatch

派 aider + DeepSeek 后端跑 L1 批量任务。

## 何时调用此 skill

- 用户明示要派 aider（"派 aider 写 5 个测试"、"用 aider 跑批量改名"）
- 主对话识别出 L1 任务，按 DISPATCH.md § 八 速查表首选 aider+DeepSeek
- 任务符合下列模式：写测试 / scaffold 重复样板 / 补 docstring / 改字段名 / 翻译注释

**不要在这些情况调用**：
- L3 架构任务、写 ADR、改接口签名 → 主对话自己写
- L2 实现任务（写仓储、写 sink）→ 派 Sonnet/Codex subagent
- 跨 5+ 文件的修改 → 主对话编排
- 涉及 secret/凭证/客户数据 → 不能给经第三方中转的模型

## 必须遵守的强制规则

### 1. 命令必须包含的参数

```bash
aider \
  --model deepseek/deepseek-chat \
  --no-auto-commits \
  --yes \
  --no-stream \
  --message "<具体任务指令>" \
  <可改文件路径 1> \
  <可改文件路径 2>
```

**理由**：
- `--model deepseek/deepseek-chat` — 强制走 DeepSeek，不要默认 Anthropic（违背成本初衷）
- `--no-auto-commits` — **commit 权必须留给主对话**（参见 DISPATCH.md 失败模式 I）
- `--yes` — 不进交互确认（主对话不能回答交互式提示）
- `--no-stream` — stdout 一次性输出（主对话才能完整读到）
- 显式文件路径 — 圈定可改范围，防 aider 自由发挥

### 2. --message 内容遵循 DISPATCH.md § 五 的 6 段模板

写在 `--message` 字符串里时压缩成精炼的中文段落，但必须覆盖：
- 任务目标（一句话）
- 关键约束（如"必须 table-driven、不改被测函数、覆盖 happy/edge/error 三档"）
- 验收标准（如"跑 pytest tests/test_X.py 全过"）
- 输出格式约束（如"不要改其他文件、不要写 README"）

### 3. 验证 DEEPSEEK_API_KEY 已设置

调用前先 `echo "$DEEPSEEK_API_KEY" | head -c 10` 看是否为空。
若为空，告诉用户配置 `export DEEPSEEK_API_KEY="sk-..."` 后再调。

## 调用流程

按 DISPATCH.md § 7.4.1 工作流：

1. **写任务清单**：先在主对话里和用户确认要做什么、可改哪些文件
2. **构造 Bash 命令**：按上面的强制规则拼好完整命令
3. **执行前展示给用户**：派 aider 比派 subagent 多一步——把 Bash 命令完整贴出来让用户看一眼，因为 aider 直接写文件无 diff preview。如果用户没异议，再执行
4. **执行**：用 Bash 工具跑命令，捕获 stdout
5. **review**：跑 `git diff` 看 aider 实际改了什么
6. **跑测试/验收**：按任务清单的验收标准跑
7. **汇报**：commit 前先告诉用户"aider 改了哪些文件、测试是否通过、下一步要不要 commit"
8. **commit**（用户批准后）：主对话起草 commit message → 一次性 commit

## 出错处理

### aider 没装
```
ModuleNotFoundError: No module named 'aider' / command not found: aider
```
告诉用户 `pip install aider-chat`，不要自己尝试装（用户的 venv 由用户控制）。

### DeepSeek API key 失效
```
401 / Authentication failed
```
告诉用户检查 `DEEPSEEK_API_KEY`，不要尝试用其他模型替代（违背成本初衷）。

### aider 改了任务范围外的文件
**这是 aider 的"自由发挥"问题**——失败模式 B（破坏未列出文件）。
对策：`git restore <无关文件>` 回滚那些改动，保留任务相关改动。
若多次发生：在 `--message` 里更明确写"不要修改 X、Y、Z 以外的文件"。

### aider 还是自动 commit 了
触发了**失败模式 I**——某些 aider 版本可能不严格遵守 `--no-auto-commits`。
对策：`git reset --soft HEAD~N`（N = aider 多 commit 的数量）把改动退回工作区，然后由主对话起草 commit message 重新 commit。

### aider 写出错的代码
跑 `git restore <受影响文件>` 全部回滚。然后判断：
- 任务描述太模糊 → 重写 `--message` 再派一次
- 任务超出 DeepSeek 能力 → 改派 Sonnet subagent
- 不要修 aider 的产物来"将就"——重派或改派比修补便宜

## 成本预估（参考，会变）

DeepSeek 当前定价 ~¥1/M input + ~¥2/M output（远低于 Anthropic 1-2 数量级）。

典型 L1 任务（5 个 pytest 单测，~10K input + ~3K output）成本 < ¥0.02。
同等任务派 Sonnet subagent 约 ¥1+，**50-100× 价差**。

## 调用示例

### 例 1：写 5 个 pytest 单测

```bash
aider \
  --model deepseek/deepseek-chat \
  --no-auto-commits \
  --yes \
  --no-stream \
  --message "为 control-plane/app/services/classifier.py 中的 classify_files 函数写 5 个 table-driven pytest，覆盖：(1) 正常分类 happy path；(2) zip slip 攻击文件名；(3) 中文文件名 GBK 解码；(4) ignored_filenames 过滤；(5) 团队名未匹配返回 error severity。约束：不改被测函数；测试用 in-memory 数据；fixture 写在测试文件里不另开 conftest；只改 control-plane/tests/test_classifier.py" \
  control-plane/app/services/classifier.py \
  control-plane/tests/test_classifier.py
```

### 例 2：批量加中文 docstring

```bash
aider \
  --model deepseek/deepseek-chat \
  --no-auto-commits \
  --yes \
  --no-stream \
  --message "给本文件所有 public 方法加中文 docstring，说明参数、返回值、异常。简洁版（1-3 行 docstring）不要 Google/Numpy 风格。不改任何代码逻辑、不改方法签名、不加 type hint。" \
  control-plane/app/services/foo.py
```

### 例 3：scaffold 5 个相似 endpoint

```bash
aider \
  --model deepseek/deepseek-chat \
  --no-auto-commits \
  --yes \
  --no-stream \
  --message "按现有 list_workspaces 在 control-plane/app/api/workspaces.py 里的实现模式，新增 list_objects、list_tasks、list_users、list_audit、list_notifications 五个 endpoint。每个 endpoint 都需要：(1) 同样的鉴权依赖；(2) 同样的分页参数；(3) 同样的 response schema 风格；(4) 同样的错误处理。Pydantic schema 写在 control-plane/app/schemas/ 对应文件里。" \
  control-plane/app/api/workspaces.py \
  control-plane/app/schemas/objects.py \
  control-plane/app/schemas/tasks.py \
  control-plane/app/schemas/users.py \
  control-plane/app/schemas/audit.py \
  control-plane/app/schemas/notifications.py
```
