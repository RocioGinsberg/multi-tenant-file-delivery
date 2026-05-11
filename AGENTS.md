# AGENTS.md — Codex 宿主派工纪律

> Codex CLI 项目级规则（Codex 自动加载）。**完整协作规范**（L 等级定义、6 段模板、质量底线、失败模式等）见 [DISPATCH.md](./DISPATCH.md)。
>
> 核心原则：主编排 Agent 掌握架构权，执行池承担执行权；commit 权必须留给主编排 Agent + 用户。

---

## 执行池（Codex 宿主语法）

| 层级 | 默认执行方 | 典型任务 |
|---|---|---|
| L1 | `aider+DeepSeek`（Bash）；备选 `spawn_agent` + `gpt-5.4-mini` + `low` | 单测、docstring、字段名、scaffold |
| L2 | `spawn_agent` / `worker`（`gpt-5.3-codex` + `medium`） | 仓储层、FastAPI 路由、sink adapter |
| L3 | 主编排 Agent 亲自写 | ADR、接口签名、安全代码 |

---

## 档位升降信号（低成本默认）

**升档前先和用户确认**：
- 任务关键词含：并发 / race / 死锁 / 状态机 / 一致性 / 性能优化
- 接口变更面广（改 Go interface 牵动 3+ adapter）
- 跨进程/跨语言协议（Kafka 格式、trace context）
- 首次实现某算法（AIMD、Lua 令牌桶）
- 已派一档失败（medium 失败 → 升 high；high 失败 → 升 xhigh）
- 用户明确指定档位时立即覆盖，不重新评估

**降档可直接执行，事后告知**：
- 明确模板复制（"参照 X 写 Y/Z/W"）
- 批量补样板（测试、docstring）
- 纯机械改名 / import 路径调整

---

## 低成本编排默认模式

- 主编排 Agent 默认只做：读必要上下文、拆任务、写派工 prompt、review diff、跑测试、失败诊断、起草 commit
- 不为了"了解全局"一次性读全仓库；先 `rg --files` 定位，再读任务相关文件
- 单次写入限制 1-3 个可改文件；超过 5 个先拆再派，每个 patch 控制在 60-120 行
- Plan mode 用作执行前审批门：Phase 启动、跨模块任务、升档、aider 直接写文件前等用户确认
- 每次报告写清：实际执行方、模型/档位、改动文件、测试命令和结果

---

## 模块主力模型（档位）

| 模块 | 主力 | 档位 |
|---|---|---|
| `data-plane/internal/sink/` | Codex GPT-5 | medium |
| `data-plane/internal/pipeline/` | Codex GPT-5 | medium |
| `data-plane/internal/kafka/` | Codex GPT-5 | medium |
| `data-plane/internal/ratelimit/` | Codex GPT-5 | **high** |
| `data-plane/internal/resume/` | Codex GPT-5 | **high** |
| `data-plane/internal/progress/` | Codex GPT-5 | medium |
| `data-plane/internal/observability/` | Codex GPT-5 | medium |
| `data-plane/internal/source/` | Codex GPT-5 | medium |
| `control-plane/app/services/` | Codex worker | medium（并发/状态机升 high 先确认） |
| `control-plane/app/repos/` | Codex worker | medium |
| `control-plane/app/api/` | Codex worker | medium |
| `control-plane/app/core/` | Codex worker | medium（安全部分不委托） |
| `control-plane/tests/` | aider+DeepSeek | — |
| `data-plane/internal/*/test*.go` | aider+DeepSeek | — |

偏离主力时在 commit message 里写明原因。

---

## aider 强制参数

```bash
aider \
  --model deepseek/deepseek-chat \
  --no-auto-commits \
  --yes \
  --no-stream \
  --message "<任务指令，遵循 DISPATCH.md § 五 6 段模板>" \
  <可改文件路径...>
```

---

*完整规范 → [DISPATCH.md](./DISPATCH.md)*
