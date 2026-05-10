# Phase 1 — Python 单体 MVP

> **状态**：📋 已规划 / 待启动
> **预计工时**：4-6 天
> **完成定义**：本地起 FastAPI 进程 + MinIO 容器，浏览器能上传 zip → 看见分类预览 → 确认 → 看进度 → 文件到达 MinIO bucket
> **关联 BLUEPRINT 章节**：§ 十 Phase 1
> **关联 ADR**：[0001 双语言架构](../ADR/0001-dual-language.md)、[0010 首版后端选 S3/MinIO](../ADR/0010-pivot-to-generic-object-storage.md)

---

## 一、4 个先决决策（已确认）

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| **D1** | Phase 1 是否包含 Workspace 抽象？ | ❌ **不做** | Phase 1 先跑通 Python+SQLite+SSE+流式上传，Workspace 留 Phase 6.5。表结构只 task / task_item / task_event 三张 |
| **D2** | 分类器是否支持注册表 draft/publish？ | ❌ **不做** | 单一硬编码配置：直接读 `task_classifications.json`（runtime 改了重启即可）。draft/publish 留 Phase 6.5+ |
| **D3** | 前端改造范围 | ✂️ **砍掉注册表配置段**，保留上传/预览/进度三段 | 配合 D2；前端配置入口 Phase 6.5 配合 workspace 一起重做 |
| **D4** | 凭证配置入口 | 📁 **`.env` 文件** | Dev 期最简；UI 配置入库留 Phase 6.5 多租户时再做 |
| **D5** | 哪些任务走 TDD？ | ✅ **1.4 / 1.5 / 1.7 走 TDD**；其余按"实现 → 测试"顺序 | 这 3 个是承重墙级契约（repo 接口 / 分类器逻辑 / progress bus 并发模型），未来多模块依赖。其他 L1 量产单测仍派 aider+DeepSeek。详见 [tdd-flow skill](../../.claude/skills/tdd-flow/SKILL.md) 8 步流程。 |

⚠️ 任意一项如果 Phase 1 中途想推翻，请在本文档加一条"决策变更"，并评估对未来 Phase 的影响。

---

## 二、子任务清单

状态标记：`[ ]` 未开始 / `[~]` 进行中 / `[x]` 已完成 / `[!]` 阻塞

**TDD 任务标记**：✅ = 走 8 步 TDD 流程（先 spec → 用户 review → 写测试 → 测试 commit (red) → 写实现 → 实现 commit (green)）；— = 普通"实现 → 测试"顺序

### 1.1 项目骨架
- **状态**：`[ ]`
- **L 等级**：L2
- **执行方**：Sonnet subagent
- **依赖**：无
- **范围**：
  - `control-plane/pyproject.toml`（uv 或 poetry，含 fastapi、uvicorn、sqlalchemy 2.0、aiosqlite、alembic、pydantic-settings、aioboto3、pytest、pytest-asyncio、httpx、ruff）
  - `control-plane/.env.example`（S3 endpoint、bucket、credential 占位）
  - `control-plane/app/main.py`（FastAPI 入口 + lifespan + healthz）
  - `control-plane/app/__init__.py`
  - `control-plane/ruff.toml` 或 pyproject 内置
- **验收**：`cd control-plane && uvicorn app.main:app --reload` 能起，`GET /healthz` 返回 200
- **commit message 草案**：`phase1(1.1): scaffold control-plane Python project (FastAPI + healthz)`

### 1.2 settings 配置
- **状态**：`[ ]`
- **L 等级**：L2
- **执行方**：Sonnet subagent
- **依赖**：1.1
- **范围**：
  - `control-plane/app/core/settings.py`：Pydantic Settings 读 .env
  - 字段：`s3_endpoint_url`, `s3_bucket`, `s3_access_key`, `s3_secret_key`, `s3_region`, `db_url`, `worker_max_team_concurrent`, `worker_max_file_concurrent`, `task_dir_base`, `max_zip_bytes`, `max_unzipped_bytes`, `max_file_count`, `cors_origins`, `app_env`
  - `from functools import lru_cache; @lru_cache def get_settings()` 模式
- **验收**：`pytest tests/test_settings.py` 验证默认值 + .env 覆盖
- **commit message 草案**：`phase1(1.2): pydantic settings with .env support`

### 1.3 DB + 三张表 + alembic
- **状态**：`[ ]`
- **L 等级**：L2
- **执行方**：Sonnet subagent
- **依赖**：1.1, 1.2
- **范围**：
  - `control-plane/app/core/db.py`：SQLAlchemy 2.0 async + aiosqlite engine + sessionmaker
  - `control-plane/app/models/task.py`、`task_item.py`、`task_event.py`：三张表，参考 BLUEPRINT § 八（去掉 tenant_id / workspace_id 字段，简化版）
  - `control-plane/alembic.ini` + `control-plane/alembic/env.py` + 第一份 migration
- **完整字段表**（已定）：
  - `task`：`id, status, idempotency_key, source_archive_name, temp_dir, summary_json, created_by, created_at, confirmed_at, finished_at`
  - `task_item`：`id, task_id, src_path, filename, ext, file_size, team_name_raw, team_name_matched, task_name, category_name, drive_dir, drive_path, severity, error_code, error_message, warning_message, upload_status, upload_error, uploaded_at`
  - `task_event`：`id, task_id, event_type, payload_json, created_at`
- **验收**：`alembic upgrade head` 在 SQLite 里建表成功；`pytest tests/test_db.py` 检查连通性
- **commit message 草案**：`phase1(1.3): SQLite + SQLAlchemy 2.0 async + alembic migration`

### 1.4 Repos ✅ TDD
- **状态**：`[ ]`
- **L 等级**：L2
- **TDD?**：✅（承重墙：repo 接口未来被所有 services 依赖）
- **执行方**：测试 — Sonnet subagent；实现 — Sonnet subagent；spec — 主对话
- **依赖**：1.3
- **范围**：
  - `control-plane/app/repos/task_repo.py`：create / get / update_status / list / get_by_idempotency_key
  - `control-plane/app/repos/item_repo.py`：bulk_insert / list_by_task / update_upload_status / count_by_status / batch_reset_failed
  - `control-plane/app/repos/event_repo.py`：append / list_by_task
  - 仓储层方法都用 async + 显式 session
- **TDD 流程**：
  - 1.4-spec：主对话起草测试 spec → 用户 review
  - 1.4-test：Sonnet 写 `tests/test_task_repo.py` / `test_item_repo.py` / `test_event_repo.py`，全 fail（red commit）
  - 1.4-impl：Sonnet 写实现，测试全过（green commit）
- **验收**：所有测试通过；至少 8 个 test case；`git diff <test commit>..HEAD -- tests/` 为空（实现阶段未改测试）
- **commit message 草案**：
  - red：`phase1(1.4): test spec for task/item/event repos (red)`
  - green：`phase1(1.4): impl repos with async session (green)`

### 1.5 Classifier 移植 ✅ TDD
- **状态**：`[ ]`
- **L 等级**：L2
- **TDD?**：✅（最值得 TDD 的任务——逻辑复杂、case 边界明确、未来跨 phase 引用）
- **执行方**：测试 — Sonnet subagent；实现 — Sonnet subagent；spec — 主对话（基于 `_legacy/` 推断 case）
- **依赖**：1.4，参考 `_legacy/smh_uploader/classifier.py`
- **范围**：
  - `control-plane/app/services/classifier.py`：从 `_legacy/smh_uploader/classifier.py` 提炼核心逻辑
  - 移除依赖：去掉 pandas / fuzzywuzzy（用 rapidfuzz 替换）/ CSV 输出
  - 输入：`zip_bytes` + `config_dict`（无 team_list 概念，Phase 1 不联团队 API）
  - 输出：`list[ClassifiedItem]` + `summary` 写到 task_item 表
  - 保留 `_decode_zip_entry_name` 的 GBK 解码逻辑
- **TDD 流程**：
  - 1.5-spec：主对话起草测试 spec（5 个 case 已在原 1.11 列出，扩充边界后写入 spec） → 用户 review
  - 1.5-test：Sonnet 写测试，全 fail（red commit）
  - 1.5-impl：Sonnet 写实现（参考 `_legacy/`），测试全过（green commit）
  - **此任务完成后原 1.11 即被吸收**
- **验收**：测试全过；含 5 个核心 case + 边界（空 zip / 仅忽略文件 / 全部错误等）
- **commit message 草案**：
  - red：`phase1(1.5): test spec for classifier (red)`
  - green：`phase1(1.5): impl classifier ported from legacy CLI (green)`

### 1.6 S3 流式上传（核心）
- **状态**：`[ ]`
- **L 等级**：**L3**
- **执行方**：**Opus 主对话亲自写**
- **依赖**：1.2，参考 `_legacy/smh_uploader/api_client.py` + `uploader.py`
- **范围**：
  - `control-plane/app/services/s3_uploader.py`
  - 用 `aioboto3` 或 `aiobotocore`（先调研选定）
  - 实现 `upload_file(local_path, bucket, key)`：流式 `put_object` + 边读边算 sha256
  - 大文件（> 50MB）走 `create_multipart_upload` + `errgroup` 风格并发 part 上传
  - 嵌套并发：team 级 + file 级双层 semaphore（参考 `uploader.py:_upload_team`）
  - 进度回调（每完成一个 file/part 通过 callback 推 progress_bus）
  - **关键约束**：禁止 `read_bytes()` 整文件入内存
- **验收**：`pytest tests/test_s3_uploader.py` 用 moto 模拟（见 1.13）；手工跑一次大文件上传到 MinIO 看内存峰值
- **commit message 草案**：`phase1(1.6): streaming S3 uploader with multipart and nested concurrency`

### 1.7 Progress Bus + SSE ✅ TDD
- **状态**：`[ ]`
- **L 等级**：L2
- **TDD?**：✅（并发组件，行为契约必须先定）
- **执行方**：测试 — Sonnet subagent；实现 — Sonnet subagent；spec — 主对话
- **依赖**：1.1
- **范围**：
  - `control-plane/app/services/progress_bus.py`：`{task_id: list[asyncio.Queue]}` 注册表 + `publish()` + `subscribe()`
  - 进程内 fanout：一个 task 多个订阅者
  - SSE endpoint 在 1.9 实现，本任务只做 bus 实现 + 单测
- **TDD 流程**：
  - 1.7-spec：主对话起草并发场景 spec（单订阅者 / 多订阅者 fanout / 订阅者中途取消 / 慢消费者背压 / publisher 在没有订阅者时不阻塞）→ 用户 review
  - 1.7-test：Sonnet 写测试，全 fail（red commit）
  - 1.7-impl：Sonnet 写实现，测试全过（green commit）
- **验收**：测试全过；并发 case 用 `pytest-asyncio` + `asyncio.gather` 模拟
- **commit message 草案**：
  - red：`phase1(1.7): test spec for progress bus (red)`
  - green：`phase1(1.7): impl in-process progress bus (green)`

### 1.8 Task Runner（编排核心）
- **状态**：`[ ]`
- **L 等级**：**L3**
- **执行方**：**Opus 主对话亲自写**
- **依赖**：1.5, 1.6, 1.7
- **范围**：
  - `control-plane/app/services/task_runner.py`
  - `async def run_task(task_id)`：编排状态机
    1. 从 DB 拿 task + items（status=confirmed, severity in (ok, warning)）
    2. 按 team_name_matched 分组
    3. 嵌套并发 upload，进度推 progress_bus
    4. 失败的 item 标记 upload_status=failed
    5. 任务结束更新 task.status (uploaded / partial_failed)
  - 由 `BackgroundTask` 启动；不用 Celery / arq
  - 错误处理：单个 file 失败不阻断 task；整个 task 异常要兜底写 task_event
- **验收**：手工跑一次 zip → 完成；端到端测试在 1.14
- **commit message 草案**：`phase1(1.8): task runner with state machine and progress emit`

### 1.9 API 路由
- **状态**：`[ ]`
- **L 等级**：L2
- **执行方**：Sonnet subagent
- **依赖**：1.4-1.8
- **范围**：
  - `control-plane/app/api/tasks.py`：
    - `POST /api/v1/tasks` (multipart zip upload) → 创建 task + 解压
    - `POST /api/v1/tasks/{id}/classify` → 调 classifier
    - `GET /api/v1/tasks/{id}/preview` → 返回 items + summary
    - `POST /api/v1/tasks/{id}/confirm` → 改 status=confirmed
    - `POST /api/v1/tasks/{id}/upload` → BackgroundTask 启动 task_runner
    - `POST /api/v1/tasks/{id}/retry` → reset failed items
    - `GET /api/v1/tasks/{id}` → 详情
    - `GET /api/v1/tasks/{id}/progress` → SSE 流
    - `GET /api/v1/tasks` → 列表（分页）
  - 路由前缀 `/api/v1`（与旧 `/api/upload/cosdrive` 区分）
  - 主入口在 `control-plane/app/main.py` 注册
- **验收**：`pytest tests/test_api_tasks.py` 全过；至少 10 个 test case
- **commit message 草案**：`phase1(1.9): tasks API routes with SSE progress endpoint`

### 1.10 Pydantic Schema
- **状态**：`[ ]`
- **L 等级**：L1
- **执行方**：**aider+DeepSeek**
- **依赖**：1.9 接口形状已定
- **范围**：
  - `control-plane/app/schemas/task.py`：CreateTaskResponse、ClassifyResponse、PreviewResponse、ConfirmResponse、UploadResponse、TaskDetailResponse、TaskListResponse、ProgressEvent（SSE event payload）
  - 严格按 1.9 路由签名生成
  - 不写业务逻辑，纯字段定义 + 校验
- **验收**：`pytest tests/test_schemas.py` 跑 schema 序列化 round-trip
- **派 aider 命令**：见执行时构造

### 1.11 ~~Classifier 单测~~（已并入 1.5 TDD 流程）
- **状态**：`[—]` 已移除
- 原 5 个 case 在 1.5-spec 阶段直接定义，由 1.5 的 TDD 流程产出测试代码

### 1.12 ~~Repos 单测~~（已并入 1.4 TDD 流程）
- **状态**：`[—]` 已移除
- 原 case 在 1.4-spec 阶段定义，由 1.4 的 TDD 流程产出

### 1.13 S3 Uploader 单测
- **状态**：`[ ]`
- **L 等级**：L1
- **TDD?**：— （非 TDD：1.6 已由主对话亲自写，单测后置补充覆盖率即可）
- **执行方**：**aider+DeepSeek**
- **依赖**：1.6
- **范围**：
  - `control-plane/tests/test_s3_uploader.py`
  - 用 `moto` mock S3
  - case：单段上传 / multipart 上传 / 上传失败重试 / 流式校验内存峰值（用大文件 mock）

### 1.14 端到端集成测试
- **状态**：`[ ]`
- **L 等级**：L2
- **执行方**：Sonnet subagent
- **依赖**：1.9 全部完成 + MinIO 容器（1.16）
- **范围**：
  - `control-plane/tests/test_e2e.py`
  - FastAPI TestClient + 真 MinIO（用 docker-compose 起）
  - 完整流程：上传测试 zip → classify → confirm → upload → 验证 MinIO bucket 里有文件
  - 用 pytest mark 标注 `@pytest.mark.e2e`，CI 时按需跑
- **验收**：`pytest tests/test_e2e.py -v` 全过

### 1.15 前端改造
- **状态**：`[ ]`
- **L 等级**：**L3**
- **执行方**：**Opus 主对话亲自写**（结构判断）+ aider 收尾文案
- **依赖**：1.9
- **范围**：
  - 砍掉 `web/public/index.html` 中"分类规则配置台"段（约 100 行）
  - 保留：上传准备 / 分类预览 / 执行结果
  - API 路径前缀全部改成 `/api/v1/`
  - 改动 `web/js/utils/api.js` 的 base URL
  - 删掉一些用不上的组件（如 status-vocab.js 里 cosdrive 专属状态）
- **验收**：浏览器能完成上传 zip → 预览 → 确认 → 看进度 SSE → 看结果

### 1.16 docker-compose for MinIO
- **状态**：`[ ]`
- **L 等级**：L2
- **执行方**：Sonnet subagent
- **依赖**：无（与 1.1-1.9 并行可做）
- **范围**：
  - `deploy/docker-compose.yml`：minio + minio-init（自动建 bucket）
  - 端口 9000（API）/ 9001（console）
  - 默认 access key / secret key 与 .env.example 对齐
  - `deploy/README.md` 更新启动指引
- **验收**：`cd deploy && docker compose up -d minio` 能起；浏览器开 9001 console 能登录

### 1.17 收尾文档
- **状态**：`[ ]`
- **L 等级**：**L3**
- **执行方**：**Opus 主对话亲自写**
- **依赖**：所有任务完成
- **范围**：
  - `control-plane/README.md` 改成完整启动指南（pyproject 装依赖 / .env 配置 / alembic / uvicorn / docker compose）
  - BLUEPRINT.md 加 Phase 1 完成 changelog
  - 本文档（phase-1-python-mvp.md）所有任务标 `[x]`，加"完成总结"小节

---

## 三、派工方案概览

| 执行方 | 任务数 | 任务编号 | 主要职责 |
|---|---|---|---|
| **Opus 主对话** | 4 | 1.6 / 1.8 / 1.15 / 1.17 | S3 流式上传内核、worker 编排、前端架构判断、收尾文档 |
| **Sonnet subagent** | 8 | 1.1 / 1.2 / 1.3 / **1.4 (TDD red+green)** / **1.5 (TDD red+green)** / **1.7 (TDD red+green)** / 1.9 / 1.14 / 1.16 | 项目骨架、settings、DB、repos+测试、classifier+测试、SSE bus+测试、API、E2E、docker |
| **aider+DeepSeek** | 2 | 1.10 / 1.13 | Pydantic schema、S3 uploader 单测 |
| **Codex** | 0 | — | Phase 2 起才用（数据面 Go） |

**TDD 任务额外开销**：
- 1.4 / 1.5 / 1.7 各多一次 commit（red + green），主对话多 1-2 次 review
- 用户深度参与点：3 个 spec review + 3 个测试代码 review，合计约 1-2 小时
- 收益：3 个承重墙模块的契约钉死，未来 Phase 2/6.5 修改时有保护

---

## 四、推荐执行顺序

依赖图（→ 表示依赖；TDD 任务用 [TDD] 标记，每个 TDD 任务内部含 spec/test/impl 三步）：

```
1.1 ──┬──> 1.2 ──> 1.3 ──> [TDD 1.4] ──> [TDD 1.5]
      │                          │              │
      ├──> [TDD 1.7]             │              │
      │                          │              │
      └──> 1.6 ──> 1.13          │              │
              │                  │              │
              ├──> 1.8 <─────────┴──────────────┘
              │     │
              │     └──> 1.9 ──> 1.10
              │                    │
              │                    ├──> 1.14（也依赖 1.16）
              │                    │
              │                    └──> 1.15
              │
              └──> 1.16（独立）

1.17 依赖所有
```

**推荐顺序**（按里程碑分组）：

**M1 — 骨架与持久化（最小可启动）**
1.1 → 1.2 → 1.3 → **[TDD 1.4]**（spec → test commit (red) → impl commit (green)）

**M2 — 业务核心**
**[TDD 1.5]**（spec → red → green）→ 1.6 → 1.13（s3 单测）→ **[TDD 1.7]**（spec → red → green）

**M3 — 编排与 API**
1.8 → 1.9 → 1.10（schemas）

**M4 — 端到端**
1.16（docker minio）并行可做 → 1.14 → 1.15

**M5 — 收尾**
1.17

---

## 四.5 TDD 任务的派工链（适用于 1.4 / 1.5 / 1.7）

每个 TDD 任务展开为 3 个执行阶段，**绝不合并**：

| 阶段 | 动作 | 执行方 | 产物 | commit |
|---|---|---|---|---|
| **spec** | 主对话起草测试 spec → 用户 review | Opus + 用户 | 写入本文档对应任务节，含 case 列表/边界/scope-out | 不 commit |
| **red** | 派 Sonnet 写测试代码（应全 fail） | Sonnet subagent | `tests/test_X.py` 全 fail | `phase1(X.Y): test spec for X (red)` |
| **green** | 派 Sonnet 写实现（测试不许改） | Sonnet subagent | 实现代码 + 全部测试通过 | `phase1(X.Y): impl X (green)` |

**主对话强制纪律**（已写入 [.claude/CLAUDE.md](../../.claude/CLAUDE.md)）：
- ❌ 测试与实现混 commit
- ❌ TDD 任务派 aider 写测试（aider 适合 L1 量产单测如 1.13，但承重墙级契约必须 Sonnet）
- ❌ 跳过用户 review spec
- ❌ 实现阶段改测试

调用 [tdd-flow skill](../../.claude/skills/tdd-flow/SKILL.md) 自动展开 8 步流程。

**M4 — 端到端**
1.16（docker minio）并行可做 → 1.14 → 1.15

**M5 — 收尾**
1.17

---

## 五、关键风险

| 风险 | 缓解 |
|---|---|
| `aioboto3` vs `aiobotocore` 选错（前者已停维护，后者更新） | 1.6 开工前先查最新状态 + 写 ADR 0011 记录选型 |
| classifier 移植丢失 GBK 解码 | 1.5 派 Sonnet 时明确"必须保留 `_decode_zip_entry_name`" |
| 前端改造引入大量未知 bug | 1.15 砍掉的组件先确认没有跨页引用，删一个测一次 |
| SSE 在生产场景的 keep-alive | Phase 1 不解决，先做能跑的版本，Phase 4 引入 Redis pub/sub 时一起优化 |
| Phase 1 完成定义里没提"按团队分组上传"——`uploader.py` 嵌套并发的核心 | 1.6/1.8 里仍要做（保留 `_legacy/` 灵魂），但 Phase 1 单租户单团队也能跑通端到端 |
| TDD 任务步骤被偷懒合并（实现阶段悄悄改测试） | tdd-flow skill 强制 8 步；CLAUDE.md 列硬底线；commit message 强制 (red)/(green) 标识便于 git log 审计；后续若仍出问题，加 pre-commit hook |

---

## 六、Phase 1 完成总结

待 1.17 时填充。

包括：
- 实际工时 vs 预估
- 偏离计划的地方（比如哪个任务派了备选模型、为什么）
- 遗留 TODO（推到哪个 Phase）
- 简历可写数字（如端到端延迟、内存峰值、测试覆盖率）
