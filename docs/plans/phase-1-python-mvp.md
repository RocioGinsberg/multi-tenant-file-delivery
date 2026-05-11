# Phase 1 — Python 单体 MVP

> **状态**：📋 已规划 / 待启动
> **预计工时**：4-6 天
> **完成定义**：本地起 FastAPI 进程 + MinIO 容器，浏览器能上传 zip → 看见分类预览 → 确认 → 看进度 → 文件到达 MinIO bucket
> **关联 BLUEPRINT 章节**：§ 十 Phase 1
> **关联 ADR**：[0001 双语言架构](../ADR/0001-dual-language.md)、[0010 首版后端选 S3/MinIO](../ADR/0010-pivot-to-generic-object-storage.md)、[0011 Classifier Core 与业务 Profile 分层](../ADR/0011-classification-profile-engine.md)

> **执行方命名说明**：实际执行时按 [DISPATCH.md](../../DISPATCH.md) 的宿主无关规则解释：`主对话` / `主编排 Agent` 等价当前宿主的编排层；L1 首选 `aider+DeepSeek`；L2 在 Claude Code 宿主用 `Agent(model="sonnet")`，在 Codex 宿主用 `spawn_agent + gpt-5.3-codex + medium`；L3 由主编排 Agent 亲自掌握。

> **预算档位**：Phase 1 默认走低成本编排模式。主编排 Agent 主要负责 spec、派工 prompt、review diff、跑测试、失败诊断和 commit draft；只有 1.6 / 1.8 / 1.15 / 1.17 这类 L3/承重墙任务直接实现。安全、并发/race、状态机、接口签名、worker 连续失败或用户显式要求时，才确认升档到高档 Codex / 主编排 Agent 直接接手。

---

## 一、6 个先决决策（已确认）

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| **D1** | Phase 1 是否包含 Workspace 抽象？ | ❌ **不做** | Phase 1 先跑通 Python+SQLite+SSE+流式上传，Workspace 留 Phase 6.5。表结构只 task / task_item / task_event 三张 |
| **D2** | 分类器是否支持注册表 draft/publish？ | ❌ **不做** | 单一静态 profile：直接读 `profiles/hq_subsidiary_reports_v1/profile.json`（runtime 改了重启即可）。draft/publish 留 Phase 6.5+ |
| **D3** | 前端改造范围 | ✂️ **砍掉注册表配置段**，保留上传/预览/进度三段 | 配合 D2；前端配置入口 Phase 6.5 配合 workspace 一起重做 |
| **D4** | 凭证配置入口 | 📁 **`.env` 文件** | Dev 期最简；UI 配置入库留 Phase 6.5 多租户时再做 |
| **D5** | 哪些任务走 TDD？ | ✅ **1.4 / 1.5 / 1.7 走 TDD**；其余按"实现 → 测试"顺序 | 这 3 个是承重墙级契约（repo 接口 / 分类器逻辑 / progress bus 并发模型），未来多模块依赖。其他 L1 量产单测仍派 aider+DeepSeek。详见 [tdd-flow skill](../../.claude/skills/tdd-flow/SKILL.md) 8 步流程。 |
| **D6** | 分类器是否直接编码业务语义？ | ❌ **不写死**，采用 Classifier Core + 静态 Classification Profile | 旧 SMH classifier 只作为参考。Core 只理解 file facts / target / document_type / dst_path / severity；文件归属规则、描述映射、优先级、路径模板放入 versioned profile。Phase 1 只加载本地静态 profile，不做 registry draft/publish 或动态 Python plugin。 |

⚠️ 任意一项如果 Phase 1 中途想推翻，请在本文档加一条"决策变更"，并评估对未来 Phase 的影响。

---

## 二、子任务清单

状态标记：`[ ]` 未开始 / `[~]` 进行中 / `[x]` 已完成 / `[!]` 阻塞

**TDD 任务标记**：✅ = 走 8 步 TDD 流程（先 spec → 用户 review → 写测试 → 测试 commit (red) → 写实现 → 实现 commit (green)）；— = 普通"实现 → 测试"顺序

### 1.1 项目骨架
- **状态**：`[ ]`
- **L 等级**：L2
- **执行方**：L2 worker（Codex 宿主默认 `gpt-5.3-codex` + `medium` / `gpt-5.4-mini` + `medium`；Claude 宿主可用 Sonnet）
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
- **执行方**：L2 worker（Codex 宿主默认 `gpt-5.3-codex` + `medium` / `gpt-5.4-mini` + `medium`；Claude 宿主可用 Sonnet）
- **依赖**：1.1
- **范围**：
  - `control-plane/app/core/settings.py`：Pydantic Settings 读 .env
  - 字段：`s3_endpoint_url`, `s3_bucket`, `s3_access_key`, `s3_secret_key`, `s3_region`, `db_url`, `worker_max_target_concurrent`, `worker_max_file_concurrent`, `task_dir_base`, `classification_profile_path`, `max_zip_bytes`, `max_unzipped_bytes`, `max_file_count`, `cors_origins`, `app_env`
  - `from functools import lru_cache; @lru_cache def get_settings()` 模式
- **验收**：`pytest tests/test_settings.py` 验证默认值 + .env 覆盖
- **commit message 草案**：`phase1(1.2): pydantic settings with .env support`

### 1.3 DB + 三张表 + alembic
- **状态**：`[ ]`
- **L 等级**：L2
- **执行方**：L2 worker（Codex 宿主默认 `gpt-5.3-codex` + `medium` / `gpt-5.4-mini` + `medium`；Claude 宿主可用 Sonnet）
- **依赖**：1.1, 1.2
- **范围**：
  - `control-plane/app/core/db.py`：SQLAlchemy 2.0 async + aiosqlite engine + sessionmaker
  - `control-plane/app/models/task.py`、`task_item.py`、`task_event.py`：三张表，参考 BLUEPRINT § 八（去掉 tenant_id / workspace_id 字段，简化版）
  - `control-plane/alembic.ini` + `control-plane/alembic/env.py` + 第一份 migration
- **完整字段表**（已定）：
  - `task`：`id, status, idempotency_key, source_archive_name, temp_dir, summary_json, created_by, created_at, confirmed_at, finished_at`
  - `task_item`：`id, task_id, src_path, filename, ext, file_size, target_name_raw, target_name_matched, document_type, category_name, dst_dir, dst_path, severity, error_code, error_message, warning_message, upload_status, upload_error, uploaded_at`
  - `task_event`：`id, task_id, event_type, payload_json, created_at`
- **验收**：`alembic upgrade head` 在 SQLite 里建表成功；`pytest tests/test_db.py` 检查连通性
- **commit message 草案**：`phase1(1.3): SQLite + SQLAlchemy 2.0 async + alembic migration`

### 1.4 Repos ✅ TDD
- **状态**：`[x]`
- **L 等级**：L2
- **TDD?**：✅（承重墙：repo 接口未来被所有 services 依赖）
- **执行方**：测试 — L2 worker；实现 — L2 worker；spec — 主编排 Agent
- **依赖**：1.3
- **范围**：
  - `control-plane/app/repos/task_repo.py`：create / get / update_status / list / get_by_idempotency_key
  - `control-plane/app/repos/item_repo.py`：bulk_insert / list_by_task / update_upload_status / count_by_status / batch_reset_failed
  - `control-plane/app/repos/event_repo.py`：append / list_by_task
  - 仓储层方法都用 async + 显式 session
- **TDD 流程**：
  - 1.4-spec：主编排 Agent 起草测试 spec → 用户 review
  - 1.4-test：L2 worker 写 `tests/test_task_repo.py` / `test_item_repo.py` / `test_event_repo.py`，全 fail（red commit）
  - 1.4-impl：L2 worker 写实现，测试全过（green commit）
- **验收**：所有测试通过；至少 8 个 test case；`git diff <test commit>..HEAD -- tests/` 为空（实现阶段未改测试）
- **Repo 契约（1.4-spec，2026-05-10）**：
  - 通用约束：所有方法接收显式 `AsyncSession`；repo 不调用 `commit()`，写操作只 `flush()`，事务边界由 service / API 层控制；查询使用 SQLAlchemy 2.0 `select()` / `update()` 风格，不使用 legacy Query API。
  - `TaskRepo.create(session, *, idempotency_key, submission_label="", temp_dir="", summary_json=None, created_by="local-user", status="draft") -> Task`：创建 task，默认 `summary_json={}`；返回已 flush、带 `id` 的 ORM 对象。
  - `TaskRepo.get(session, task_id) -> Task | None`：按主键读取，不存在返回 `None`。
  - `TaskRepo.get_by_idempotency_key(session, idempotency_key) -> Task | None`：按唯一幂等键读取，不存在返回 `None`。
  - `TaskRepo.update_status(session, task_id, status, *, confirmed_at=None, finished_at=None) -> Task | None`：只更新状态和显式传入的时间字段；不存在返回 `None`。
  - `TaskRepo.list(session, *, limit=50, offset=0) -> list[Task]`：按 `created_at desc, id desc` 返回分页列表。
  - `ItemRepo.bulk_insert(session, task_id, items) -> list[TaskItem]`：接收 dict 列表或可迭代映射，统一注入 `task_id`，批量创建并返回已 flush 对象；不吞掉唯一约束错误。
  - `ItemRepo.list_by_task(session, task_id) -> list[TaskItem]`：按 `src_path asc` 返回任务下所有 item。
  - `ItemRepo.update_upload_status(session, item_id, upload_status, *, upload_error="", uploaded_at=None) -> TaskItem | None`：按 item id 更新上传状态、错误信息和上传时间；不存在返回 `None`。
  - `ItemRepo.count_by_status(session, task_id) -> dict[str, int]`：按 `upload_status` 分组计数；没有 item 时返回 `{}`。
  - `ItemRepo.batch_reset_failed(session, task_id) -> int`：将该 task 下 `upload_status="failed"` 的 item 重置为 `pending`，清空 `upload_error` 和 `uploaded_at`，返回重置数量。
  - `EventRepo.append(session, task_id, event_type, payload_json=None) -> TaskEvent`：追加事件，默认 `payload_json={}`，返回已 flush 对象。
  - `EventRepo.list_by_task(session, task_id) -> list[TaskEvent]`：按 `created_at asc, id asc` 返回事件列表。
- **测试 spec（red 阶段必须覆盖）**：
  - `test_task_repo_create_and_get_returns_persisted_task`
  - `test_task_repo_get_by_idempotency_key_returns_existing_task`
  - `test_task_repo_update_status_updates_only_explicit_timestamp_fields`
  - `test_task_repo_list_orders_newest_first_and_applies_pagination`
  - `test_item_repo_bulk_insert_and_list_by_task_orders_by_src_path`
  - `test_item_repo_update_upload_status_returns_none_for_missing_item`
  - `test_item_repo_count_by_status_groups_task_items_only`
  - `test_item_repo_batch_reset_failed_only_resets_failed_items_for_task`
  - `test_event_repo_append_and_list_by_task_orders_events`
- **commit message 草案**：
  - red：`phase1(1.4): test spec for task/item/event repos (red)`
  - green：`phase1(1.4): impl repos with async session (green)`

### 1.5 Classifier Core + Profile ✅ TDD
- **状态**：`[ ]`
- **L 等级**：L2
- **TDD?**：✅（承重墙：分类结果会被 API preview、task_runner、workspace registry 复用）
- **执行方**：测试 — L2 worker；实现 — L2 worker；spec — 主编排 Agent（基于 ADR 0011 + `_legacy/` 推断 case）
- **依赖**：1.4，参考 `_legacy/smh_uploader/classifier.py`，遵循 ADR 0011
- **范围**：
  - `control-plane/app/services/classifier.py`：实现 Classifier Engine；旧 classifier 只作为算法参考，不移植业务语义
  - `control-plane/app/services/classification_profile.py`（或同文件内独立类型）：定义静态 profile schema / loader / validation
  - 移除依赖：去掉 pandas / fuzzywuzzy（用 rapidfuzz 替换或先用标准库 exact/alias，模糊匹配封装在 adapter 内）/ CSV 输出
  - 输入：`zip_bytes` + `profile_dict`（Phase 1 不联 workspace/tenant API；target 字典来自 profile）
  - 输出：`list[ClassifiedItem]` + `summary`；service 层写入 task_item
  - 保留 `_decode_zip_entry_name` 的 GBK 解码逻辑
  - Core 只使用通用语义：`target_name_raw`, `target_name_matched`, `document_type`, `category_name`, `dst_dir`, `dst_path`, `severity`
  - Profile 承载业务适配：entry filters、**target extraction strategy**、target resolution、classification resolution、match priority、path template、error policy
  - **Target extraction strategy（Profile 配置，2026-05-11 设计决策）**：
    - **`"directory"`（主策略，推荐默认）**：zip 顶层目录名 = target key。HQ 按接收方建文件夹，文件名不承担路由职责。`acme/月报.xlsx` → target_name_raw="acme"
    - **`"filename_segment"`（兜底）**：zip 根目录下无文件夹的文件，回退到文件名解析（最后分隔段为 target）。扁平 zip 的兼容路径，不是首选
    - **`"broadcast"`**：整包发给同一个 target，profile 里写死 `broadcast_target` key；适合单接收方场景
    - Phase 1 实现 `"directory_or_filename"`（先取顶层目录，根目录文件回退 filename_segment）和 `"broadcast"`；`"filename_segment"` 单独保留供 legacy 兼容
    - 目录深度超过 1 层时只取第 1 层作为 target（`acme/2026/月报.xlsx` → target="acme"）
- **TDD 流程**：
  - 1.5-spec：主编排 Agent 起草 profile engine 测试 spec（覆盖安全、target extraction strategy、归属规则、优先级、路径渲染、summary） → 用户 review
  - 1.5-test：L2 worker 写测试，全 fail（red commit）
  - 1.5-impl：L2 worker 写实现（参考 `_legacy/` 的解码/匹配经验，不复刻业务语义），测试全过（green commit）
  - **此任务完成后原 1.11 即被吸收**
- **测试 spec（red 阶段必须覆盖）**：
  - zip entry 解码与安全：UTF-8/GBK 文件名可读；`../evil.xlsx` / 绝对路径被拒绝；目录 entry 跳过
  - entry filter：`.DS_Store` / `Thumbs.db` / `README.md` 等进入 ignored 或不产出可上传 item，summary 正确
  - **target extraction**：directory 策略（顶层目录 = target）；broadcast 策略（全部归一个 target）；directory_or_filename 的扁平兜底（根目录文件回退 filename_segment）；混合 zip（有文件夹 + 有根目录文件）
  - target resolution：alias / exact / strip-number-prefix / missing target；未知 target 不丢弃，产出 `severity="error"`
  - classification resolution：suffix priority / description exact / description fuzzy（如启用）/ suffix fallback 的优先级固定
  - path rendering：默认 `{category}/{document_type}/{filename}`；禁止绝对路径、`..`、空路径段
  - summary：`total / ok / warning / error / ignored / has_blocking_errors` 可解释
- **验收**：测试全过；至少两个小 profile fixture，证明换 profile 不改 engine；含空 zip / 仅忽略文件 / 全部错误等边界
- **commit message 草案**：
  - red：`phase1(1.5): test spec for classifier profile engine (red)`
  - green：`phase1(1.5): impl classifier profile engine (green)`

#### TDD spec — 1.5 Classifier Profile Engine（2026-05-11，用户已 review 通过）

**一句话验收标准**：`classify_zip(zip_bytes, profile)` 根据 profile 中声明的 target extraction strategy、matching 规则和路径模板，正确分类 zip 内所有文件；换 profile 不改 engine 代码。

##### 接口契约

```python
# app/services/classification_profile.py
@dataclass
class TargetConfig:
    key: str
    aliases: list[str] = field(default_factory=list)
    strip_number_prefix: bool = False  # "12. acme" 匹配 "acme"

@dataclass
class DocumentTypeConfig:
    category: str  # 写入路径模板的上层分类

@dataclass
class MatchingConfig:
    enable_fuzzy_match: bool = True
    fuzzy_threshold: int = 70
    description_fuzzy_threshold: int = 70

@dataclass
class EntryFilterConfig:
    ignored_filenames: list[str] = field(default_factory=list)
    ignored_prefixes: list[str] = field(default_factory=list)   # 如 "__MACOSX"

@dataclass
class TargetExtractionConfig:
    strategy: str = "directory_or_filename"
    # "directory_or_filename"：有顶层目录→目录名；根目录文件→filename_segment 兜底
    # "broadcast"：全包归一个 target（broadcast_target 必填）
    # "filename_segment"：legacy，全用文件名解析
    delimiters: list[str] = field(default_factory=lambda: ["-", "—", "–", "’", "-"])
    broadcast_target: str | None = None

@dataclass
class ProfileConfig:
    version: str
    targets: list[TargetConfig]
    document_types: dict[str, DocumentTypeConfig]   # key → config
    suffix_priority: dict[str, str]                 # ".xlsx" → document_type key
    description_mapping: dict[str, str]             # "月报" → document_type key
    suffix_fallback: dict[str, str]
    entry_filters: EntryFilterConfig = field(default_factory=EntryFilterConfig)
    path_template: str = "{category}/{document_type}/{filename}"
    matching_config: MatchingConfig = field(default_factory=MatchingConfig)
    target_extraction: TargetExtractionConfig = field(default_factory=TargetExtractionConfig)

# app/services/classifier.py（更新签名）
def classify_zip(zip_bytes: bytes, profile: ProfileConfig) -> tuple[list[ClassifiedItem], ClassifySummary]:
    ...

# ClassifiedItem 更新：target_name_matched 改为 str | None = None
```

##### 两个 Profile Fixture（测试内定义，不读文件）

**Profile A（"simple"）**：
- targets: `[{key:"acme", aliases:["ACME"]}, {key:"globex"}]`
- document_types: `{monthly:{category:"reports"}, contract:{category:"legal"}}`
- suffix_priority: `{".pdf": "contract"}`
- description_mapping: `{"月报": "monthly", "合同": "contract"}`
- suffix_fallback: `{".xlsx": "monthly"}`
- path_template: `"{category}/{document_type}/{filename}"`
- ignored_filenames: `[".DS_Store", "Thumbs.db"]`
- target_extraction.strategy: `"directory_or_filename"`

**Profile B（"minimal"）**：
- targets: `[{key:"alpha"}]`
- document_types: `{report:{category:"docs"}}`
- suffix_priority: `{}`; suffix_fallback: `{".xlsx": "report"}`
- path_template: `"uploads/{document_type}/{filename}"`
- target_extraction.strategy: `"broadcast"`, broadcast_target: `"alpha"`

##### 26 个测试 case

**Cat 1 — ZIP 解码与安全（5）**
1. `test_utf8_filename_decoded`：UTF-8 中文文件名 → `item.filename` 可读无乱码
2. `test_gbk_filename_decoded`：GBK 编码（flag_bits=0，无 UTF-8 flag）→ 仍可读
3. `test_path_traversal_rejected`：entry 路径 `../evil.xlsx` → severity="error", error_code="path_traversal"
4. `test_absolute_path_rejected`：entry 路径 `/etc/evil.xlsx` → severity="error", error_code="path_traversal"
5. `test_directory_entry_skipped`：纯目录 entry（`somedir/`）→ 不在 items，不计入 total，不计入 ignored

**Cat 2 — Entry Filter（2）**
6. `test_ignored_filename_not_in_items`：`.DS_Store` → items 为空，summary.ignored=1, total=1
7. `test_mixed_ignored_and_ok_summary`：1 ignored + 1 ok → ignored=1, ok=1, total=2

**Cat 3 — Target Extraction Strategy（6）**
8. `test_directory_strategy_folder_is_target`：`acme/月报.xlsx`，strategy=directory_or_filename → target_name_raw="acme"
9. `test_directory_nested_only_first_level`：`acme/2026/月报.xlsx` → target_name_raw="acme"（只取第 1 层）
10. `test_broadcast_all_files_get_same_target`：Profile B（broadcast/alpha），两个文件 → 都 matched="alpha"
11. `test_fallback_flat_file_uses_filename_segment`：strategy=directory_or_filename；根目录 `月报-acme.xlsx` → 回退 filename_segment，raw="acme"
12. `test_mixed_zip_dir_and_flat_both_resolved`：`acme/月报.xlsx`（dir 路由）+ `季报-globex.xlsx`（filename 兜底）→ 两条分别 matched
13. `test_flat_file_no_delimiter_produces_error`：根目录 `月报.xlsx`（无分隔符）→ filename_segment 解析不出 target → severity="error", error_code="unknown_target"

**Cat 4 — Target Resolution（4）**
14. `test_target_exact_match`：raw="acme"，targets 有 key="acme" → matched="acme", severity="ok"
15. `test_target_alias_resolved`：raw="ACME"，alias ACME→acme → matched="acme"
16. `test_target_strip_number_prefix`：raw="acme"，targets 有 key="12. acme"（strip_number_prefix=True）→ matched="12. acme"
17. `test_unknown_target_error_item_kept`：raw="unknowncorp" 无匹配 → severity="error", target_name_matched=None，item 仍在 result items

**Cat 5 — Classification Resolution（4）**
18. `test_suffix_priority_beats_description`：`.pdf` 命中 suffix_priority → document_type 以 suffix_priority 为准，忽略描述
19. `test_description_mapping_exact`：无 suffix_priority 命中；描述精确命中 description_mapping
20. `test_suffix_fallback_when_no_match`：无任何命中 → document_type 从 suffix_fallback 取
21. `test_fuzzy_description_enabled_vs_disabled`：近似描述（如"月份报"接近"月报"）：fuzzy 开 → 命中；fuzzy 关 → fallback

**Cat 6 — 路径渲染（2）**
22. `test_path_template_renders_correctly`：`{category}/{document_type}/{filename}` → "reports/monthly/月报-acme.xlsx"
23. `test_path_template_dotdot_blocked`：渲染结果含 `..` → severity="error", error_code="path_render_error"

**Cat 7 — Summary（2）**
24. `test_empty_zip_summary_all_zeros`：空 zip → items=[], 全字段=0, has_blocking_errors=False
25. `test_has_blocking_errors_reflects_error_count`：有 error item → has_blocking_errors=True；全 ok → False

**Cat 8 — Profile 隔离（1）**
26. `test_same_zip_different_profile_different_result`：同 zip_bytes 分别用 Profile A / Profile B → dst_path 不同

##### 不验证的内容
- DB 写入（1.8 task_runner 职责）
- S3 上传（1.6）
- API 序列化（1.9）
- profile 磁盘 IO（`load_profile` 的读文件部分；只测 dict → ProfileConfig 逻辑）
- manifest 路由方案（留 Phase 6.5）
- **多级子目录路径保留**（`{rel_path}` 模板变量，如 `acme/2026/月报.xlsx` → dst 保留 `2026/月报.xlsx`）：当前 `{filename}` 只取 basename，子目录层级丢弃。后续补 `{rel_path}` 变量 + 对应回归测试。

### 1.6 S3 流式上传（核心）
- **状态**：`[ ]`
- **L 等级**：**L3**
- **执行方**：**主编排 Agent 亲自写**
- **依赖**：1.2，参考 `_legacy/smh_uploader/api_client.py` + `uploader.py`
- **范围**：
  - `control-plane/app/services/s3_uploader.py`
  - 用 `aioboto3` 或 `aiobotocore`（先调研选定）
  - 实现 `upload_file(local_path, bucket, key)`：流式 `put_object` + 边读边算 sha256
  - 大文件（> 50MB）走 `create_multipart_upload` + `errgroup` 风格并发 part 上传
  - 嵌套并发：target 级 + file 级双层 semaphore（参考 `uploader.py:_upload_team` 的模式，不继承旧 team 语义）
  - 进度回调（每完成一个 file/part 通过 callback 推 progress_bus）
  - **关键约束**：禁止 `read_bytes()` 整文件入内存
- **验收**：`pytest tests/test_s3_uploader.py` 用 moto 模拟（见 1.13）；手工跑一次大文件上传到 MinIO 看内存峰值
- **commit message 草案**：`phase1(1.6): streaming S3 uploader with multipart and nested concurrency`

### 1.7 Progress Bus + SSE ✅ TDD
- **状态**：`[~]` 进行中（spec 已通过，进入 TDD Step 3）
- **L 等级**：L2
- **TDD?**：✅（并发组件，行为契约必须先定）
- **执行方**：测试 — L2 worker；实现 — L2 worker；spec — 主编排 Agent
- **依赖**：1.1
- **范围**：
  - `control-plane/app/services/progress_bus.py`：`{task_id: list[asyncio.Queue]}` 注册表 + `publish()` + `subscribe()`
  - 进程内 fanout：一个 task 多个订阅者
  - SSE endpoint 在 1.9 实现，本任务只做 bus 实现 + 单测
- **TDD 流程**：
  - 1.7-spec：主编排 Agent 起草并发场景 spec（单订阅者 / 多订阅者 fanout / 订阅者中途取消 / 慢消费者背压 / publisher 在没有订阅者时不阻塞）→ 用户 review
  - 1.7-test：L2 worker 写测试，全 fail（red commit）
  - 1.7-impl：L2 worker 写实现，测试全过（green commit）
- **验收**：测试全过；并发 case 用 `pytest-asyncio` + `asyncio.gather` 模拟
- **commit message 草案**：
  - red：`phase1(1.7): test spec for progress bus (red)`
  - green：`phase1(1.7): impl in-process progress bus (green)`

#### TDD spec — 1.7 Progress Bus（2026-05-11，用户已 review 通过）

**一句话验收标准**：进程内异步 fanout bus，publish 的消息能被所有当前订阅者收到，订阅者取消不影响其他人，publisher 在无订阅者时不阻塞。

**接口契约**：

```python
class ProgressBus:
    async def publish(self, task_id: str, event: dict) -> None: ...
    def subscribe(self, task_id: str) -> AsyncContextManager[asyncio.Queue[dict | None]]: ...
    async def close_task(self, task_id: str) -> None: ...
```

- `subscribe()` 是 async context manager：进入时注册 Queue，退出时自动注销
- `close_task()` 向所有订阅者 push sentinel `None`，表示流结束
- queue 默认 `maxsize=0`（无限），不做背压控制（Phase 2 引入 Kafka 后替换）

**11 个 case（Cat A–E）**：

- **Cat A — 基础投递（3）**
  1. `test_publish_to_single_subscriber`：1 订阅者 publish 1 条 → queue.get() 得到同一 dict
  2. `test_publish_multiple_events_ordered`：1 订阅者 publish 3 条 → 按顺序收到 3 条
  3. `test_no_subscriber_publish_does_not_block`：无订阅者时 publish → 不阻塞，不抛异常

- **Cat B — Fanout（2）**
  4. `test_fanout_to_multiple_subscribers`：2 订阅者同一 task_id，publish 1 条 → 两个 queue 各得 1 条
  5. `test_different_task_ids_isolated`：订阅 task_A，publish 到 task_B → queue 不收到任何消息

- **Cat C — 订阅者生命周期（3）**
  6. `test_subscriber_exits_cleanly`：退出 context manager → 内部注册表移除该 queue，再 publish 不抛错
  7. `test_subscriber_cancel_does_not_affect_others`：2 订阅者，1 个 cancel → 另 1 个仍收到后续 publish
  8. `test_resubscribe_after_exit`：退出后重新订阅同一 task_id → 新 queue 正常工作

- **Cat D — close_task（2）**
  9. `test_close_task_sends_sentinel`：有订阅者时 close_task → queue 收到 `None`
  10. `test_close_task_no_subscribers_does_not_raise`：无订阅者时 close_task → 不抛异常

- **Cat E — 慢消费者（1）**
  11. `test_slow_consumer_does_not_block_publisher`：publish 10 条不等消费者 → publish 立即返回，消费者事后读到全部 10 条

**不验证什么**（scope-out）：
- SSE 序列化（`text/event-stream` 格式）— 留 1.9
- 满 queue drop/raise 行为 — Phase 1 maxsize=0 不会满，Phase 2 Kafka 替换后不再需要
- 跨进程 / 跨 worker 广播 — Phase 2 Redis pub/sub
- 持久化 / 消息回放
- 消息 schema 校验 — event 是裸 dict，类型约束在 1.8 task_runner 层

**测试基础设施**：
- `pytest-asyncio`，`asyncio_mode = "auto"`
- 纯 in-memory，无 mock，无外部依赖
- 并发 case 用 `asyncio.gather` + `asyncio.wait_for`（超时防死锁）

### 1.8 Task Runner（编排核心）
- **状态**：`[ ]`
- **L 等级**：**L3**
- **执行方**：**主编排 Agent 亲自写**
- **依赖**：1.5, 1.6, 1.7
- **范围**：
  - `control-plane/app/services/task_runner.py`
  - `async def run_task(task_id)`：编排状态机
    1. 从 DB 拿 task + items（status=confirmed, severity in (ok, warning)）
    2. 按 target_name_matched 分组
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
- **执行方**：L2 worker（Codex 宿主默认 `gpt-5.3-codex` + `medium` / `gpt-5.4-mini` + `medium`；Claude 宿主可用 Sonnet）
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
- **TDD?**：— （非 TDD：1.6 已由主编排 Agent 亲自写，单测后置补充覆盖率即可）
- **执行方**：**aider+DeepSeek**
- **依赖**：1.6
- **范围**：
  - `control-plane/tests/test_s3_uploader.py`
  - 用 `moto` mock S3
  - case：单段上传 / multipart 上传 / 上传失败重试 / 流式校验内存峰值（用大文件 mock）

### 1.14 端到端集成测试
- **状态**：`[ ]`
- **L 等级**：L2
- **执行方**：L2 worker（Codex 宿主默认 `gpt-5.3-codex` + `medium` / `gpt-5.4-mini` + `medium`；Claude 宿主可用 Sonnet）
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
- **执行方**：**主编排 Agent 亲自写**（结构判断）+ aider 收尾文案
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
- **执行方**：L2 worker（Codex 宿主默认 `gpt-5.3-codex` + `medium` / `gpt-5.4-mini` + `medium`；Claude 宿主可用 Sonnet）
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
- **执行方**：**主编排 Agent 亲自写**
- **依赖**：所有任务完成
- **范围**：
  - `control-plane/README.md` 改成完整启动指南（pyproject 装依赖 / .env 配置 / alembic / uvicorn / docker compose）
  - BLUEPRINT.md 加 Phase 1 完成 changelog
  - 本文档（phase-1-python-mvp.md）所有任务标 `[x]`，加"完成总结"小节

---

## 三、派工方案概览

| 执行方 | 任务数 | 任务编号 | 主要职责 |
|---|---|---|---|
| **主编排 Agent** | 4 | 1.6 / 1.8 / 1.15 / 1.17 | S3 流式上传内核、worker 编排、前端架构判断、收尾文档 |
| **L2 worker（Codex 宿主默认中档；Claude 宿主可用 Sonnet）** | 8 | 1.1 / 1.2 / 1.3 / **1.4 (TDD red+green)** / **1.5 (TDD red+green)** / **1.7 (TDD red+green)** / 1.9 / 1.14 / 1.16 | 项目骨架、settings、DB、repos+测试、classifier+测试、SSE bus+测试、API、E2E、docker |
| **aider+DeepSeek** | 2 | 1.10 / 1.13 | Pydantic schema、S3 uploader 单测 |
| **高档 Codex / 主编排 Agent 直接实现** | 0（默认） | — | Phase 1 不默认升高档；仅在 L3、worker 连续失败、安全/并发/状态机风险出现时确认使用 |

**TDD 任务额外开销**：
- 1.4 / 1.5 / 1.7 各多一次 commit（red + green），主编排 Agent 多 1-2 次 review
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
| **spec** | 主编排 Agent 起草测试 spec → 用户 review | 主编排 Agent + 用户 | 写入本文档对应任务节，含 case 列表/边界/scope-out | 不 commit |
| **red** | 派 L2 worker 写测试代码（应全 fail） | L2 worker | `tests/test_X.py` 全 fail | `phase1(X.Y): test spec for X (red)` |
| **green** | 派 L2 worker 写实现（测试不许改） | L2 worker | 实现代码 + 全部测试通过 | `phase1(X.Y): impl X (green)` |

**主编排 Agent 强制纪律**（以 [DISPATCH.md](../../DISPATCH.md) 为准；宿主特定规则见 `AGENTS.md`（Codex）或 `.claude/CLAUDE.md`（Claude Code））：
- ❌ 测试与实现混 commit
- ❌ TDD 任务派 aider 写测试（aider 适合 L1 量产单测如 1.13，但承重墙级契约必须 L2 worker）
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
| classifier engine 重写丢失 GBK 解码 | 1.5 派 worker 时明确"必须保留 `_decode_zip_entry_name`" |
| profile engine 被旧业务语义污染 | spec 中至少放两个小 profile fixture；测试证明换 profile 不改 engine |
| 前端改造引入大量未知 bug | 1.15 砍掉的组件先确认没有跨页引用，删一个测一次 |
| SSE 在生产场景的 keep-alive | Phase 1 不解决，先做能跑的版本，Phase 4 引入 Redis pub/sub 时一起优化 |
| Phase 1 完成定义里没提"按 target 分组上传"——`uploader.py` 嵌套并发的核心 | 1.6/1.8 里仍要做（保留 `_legacy/` 的并发模式，不继承 team 业务语义），但 Phase 1 单 profile / 单 target 也能跑通端到端 |
| TDD 任务步骤被偷懒合并（实现阶段悄悄改测试） | tdd-flow skill 强制 8 步；CLAUDE.md 列硬底线；commit message 强制 (red)/(green) 标识便于 git log 审计；后续若仍出问题，加 pre-commit hook |

---

## 六、Phase 1 完成总结

待 1.17 时填充。

包括：
- 实际工时 vs 预估
- 偏离计划的地方（比如哪个任务派了备选模型、为什么）
- 遗留 TODO（推到哪个 Phase）
- 简历可写数字（如端到端延迟、内存峰值、测试覆盖率）
