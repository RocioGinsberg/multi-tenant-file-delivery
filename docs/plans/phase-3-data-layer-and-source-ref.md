# Phase 3 — MySQL 数据层与 source reference 迁移

> **状态**：Current（3.1-3.9 已完成；后续可继续拆分性能、GC 和生产化任务）
> **目标**：把控制面数据层切到 MySQL，并按 RFC 0002 开始去除 data-plane 对本地临时目录的运行时依赖。
> **完成定义**：本地 compose 可启动 MySQL / Kafka / MinIO；control-plane 使用 MySQL 跑通核心测试；任务发布支持 object source reference；Go worker 可从 staged object storage 读取源文件并完成上传结果回写。
> **关联 RFC**：[0001 控制面 / 数据面分离与消息桥接](../RFC/0001-control-data-plane-bridge.md)、[0002 Stateless data-plane 与 source reference 迁移](../RFC/0002-stateless-data-plane-source-ref.md)

## Summary

Phase 2 已经完成 control-plane / data-plane 拆分、Kafka bridge 和 S3 / MinIO 单段上传。Phase 3 在此基础上做两件事：

- 数据层从 SQLite 开发形态升级到 MySQL 形态，稳定 migration、连接配置和本地 compose。
- 源文件输入从 `temp_dir + src_path` 的本地路径模型，迁移到 durable object storage 上的 source reference 模型。

这不是为了提前做复杂分布式，而是为后续 worker 集群、重试恢复、staging GC 和多实例部署扫清基础约束。

## 一、先决决策（已确认 / 待确认）

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| D1 | 主数据库 | MySQL | 用户已确认；贴近常见业务后端和面试叙事 |
| D2 | SQLite 是否保留 | 保留为轻量测试数据库 | 单元测试和本地快速验证仍需要低成本路径 |
| D3 | staged source 存储 | MinIO / S3-compatible bucket | 复用当前 S3 / MinIO 依赖，避免引入新存储系统 |
| D4 | 消息兼容策略 | v1 保留，新增 v2/source reference | 降低迁移风险，file-spool 本地路径继续可用 |
| D5 | Kafka topic | 迁移期可继续用 `delivery.tasks.v1`，payload 用 `schema_version` 区分 | 减少运维变量；是否启用 `delivery.tasks.v2` 留到实现评审 |
| D6 | worker 集群目标 | 本阶段证明无共享本地目录即可执行 | 不在本阶段引入 Kubernetes 或完整 autoscaling |

## 二、范围

### In Scope

- MySQL docker compose 服务和 `.env.example` 配置。
- SQLAlchemy / Alembic 在 MySQL 下可迁移、可测试。
- 修复 SQLite 与 MySQL 之间已暴露的类型 / 默认值 / 索引差异。
- 新增 staging bucket 配置，例如 `STAGING_BUCKET_NAME`。
- control-plane 上传源文件到 staging object storage。
- `DeliveryTaskMessage` 支持 source reference。
- data-plane 新增 `SourceResolver`，支持 v1 file source 和 v2 object source。
- Go worker 从 object source 读取 item bytes，再调用现有 sink 上传。
- 跨语言集成测试覆盖：control-plane staging -> Kafka/file transport -> Go worker -> result apply。

### Out of Scope

- S3 multipart / resume。
- 平台层 dedup。
- Redis backpressure / distributed lock。
- Kubernetes 部署。
- 完整 HA / rolling restart。
- 删除 v1 `temp_dir` 字段。

## 三、子任务清单

状态标记：`[ ]` 未开始 / `[~]` 进行中 / `[x]` 已完成 / `[!]` 阻塞

### 3.1 MySQL compose 与配置

- **状态**：`[x]`
- **L 等级**：L2
- **范围**：
  - `deploy/docker-compose.yml` 增加 MySQL 服务、健康检查和初始化环境变量。
  - `control-plane/.env.example` 增加 MySQL `DATABASE_URL` 示例。
  - `control-plane/app/core/settings.py` 确认 database URL 可覆盖。
- **验收**：
  - `docker compose up -d mysql` 可启动。
  - `cd control-plane && alembic upgrade head` 可连接 MySQL 并建表。
- **实际变更**：
  - `deploy/docker-compose.yml`：新增 MySQL 8.4 服务、healthcheck 和 `mysql_data` volume。
  - `control-plane/.env.example`：新增 MySQL `DATABASE_URL` 示例。
  - `control-plane/pyproject.toml` / `uv.lock`：新增 `asyncmy` driver。
- **验证**：
  - `cd deploy && docker compose config`
  - `cd deploy && docker compose up -d mysql`
  - `cd deploy && docker compose ps mysql`
  - `cd control-plane && .venv/bin/python -m pytest tests/unit/test_settings.py`

### 3.2 MySQL migration 兼容性

- **状态**：`[x]`
- **L 等级**：L3
- **范围**：
  - 检查现有 migration 在 MySQL 下的 JSON、DateTime、String、索引和默认值。
  - 修复 MySQL 下不兼容的 schema 定义。
  - 保持 SQLite 测试路径可用。
- **验收**：
  - MySQL 上 `alembic upgrade head` 成功。
  - SQLite 测试库仍可创建表并运行 repo 测试。
- **实际变更**：
  - `control-plane/alembic/versions/0001_initial.py`：移除 JSON 列的数据库层 default，避免 MySQL `JSON column can't have a default value`。
- **验证**：
  - `cd control-plane && DATABASE_URL='mysql+asyncmy://control_plane:control_plane@localhost:3306/control_plane?charset=utf8mb4' .venv/bin/python -m alembic upgrade head`
  - `cd deploy && docker compose exec -T mysql mysql -ucontrol_plane -pcontrol_plane control_plane -e "SHOW TABLES;"`
  - `cd control-plane && .venv/bin/python -m pytest tests/integration/test_db.py`

### 3.3 数据层测试矩阵

- **状态**：`[x]`
- **L 等级**：L2
- **范围**：
  - 标记 MySQL 集成测试，例如 `RUN_MYSQL_TESTS=1`。
  - 保留默认 SQLite 快速测试。
  - 增加至少一个 MySQL repo / migration smoke test。
- **验收**：
  - 默认 `uv run pytest` 不依赖 MySQL。
  - `RUN_MYSQL_TESTS=1` 时可验证 MySQL 连接和核心 repo 行为。
- **实际变更**：
  - `control-plane/tests/integration/test_mysql_docker.py`：新增 MySQL Docker smoke test，默认跳过。
  - `control-plane/pyproject.toml`：新增 `mysql` pytest marker。
- **验证**：
  - `cd control-plane && .venv/bin/python -m pytest tests/integration/test_mysql_docker.py`
  - `cd control-plane && RUN_MYSQL_TESTS=1 .venv/bin/python -m pytest tests/integration/test_mysql_docker.py`

### 3.4 Source reference 消息模型

- **状态**：`[x]`
- **L 等级**：L3
- **范围**：
  - control-plane `DeliveryTaskMessage` 增加 source reference 字段。
  - Go `DeliveryTask` 增加 source reference struct。
  - 保持 v1 `temp_dir` / `src_path` 兼容。
  - 明确 `schema_version=2` 的字段语义。
- **验收**：
  - Python / Go 消息 round-trip 测试覆盖 v1 和 v2。
  - 未启用 object source 时，现有 Phase 2 bridge 不回归。
- **实际变更**：
  - `control-plane/app/services/delivery.py`：新增 `DeliverySourceReference`，`DeliveryTaskMessage` 支持可选 `source`，builder 可生成 `schema_version=2` payload。
  - `data-plane/internal/message/message.go`：新增 `SourceRef`，`DeliveryTask` 支持可选 `source`，item 支持 `source_path`。
  - Python / Go 契约测试覆盖 source reference payload。
- **验证**：
  - `cd control-plane && .venv/bin/python -m ruff check app/services/delivery.py tests/integration/test_delivery.py`
  - `cd control-plane && .venv/bin/python -m pytest tests/integration/test_delivery.py`
  - `cd data-plane && GOCACHE=/tmp/smh_go_cache go test ./internal/message`

### 3.5 Control-plane staging source

- **状态**：`[x]`
- **L 等级**：L3
- **范围**：
  - 新增 staging source service，把原始 zip 或解包后的源文件写入 MinIO / S3 staging bucket。
  - 配置 `DELIVERY_SOURCE_MODE=file|object`。
  - 在 `go-worker` 模式下可发布 object source task。
- **验收**：
  - object source 模式下，任务消息不要求 worker 访问 control-plane 本地目录。
  - staging object key 可由 task_id 稳定推导，便于排障和后续 GC。
- **实际变更**：
  - `control-plane/app/services/staging_source.py`：新增 `stage_task_archive()`，上传 `original.zip` 到 staging bucket 并返回 `DeliverySourceReference`。
  - `control-plane/app/api/tasks.py`：`DELIVERY_SOURCE_MODE=object` 时发布 source reference task。
  - `control-plane/app/core/settings.py` / `.env.example`：新增 `staging_bucket_name` 和 `delivery_source_mode`。
  - `deploy/docker-compose.yml`：MinIO 初始化时创建 `auto-upload-staging` bucket。
- **验证**：
  - `cd control-plane && .venv/bin/python -m pytest tests/unit/test_staging_source.py tests/unit/test_settings.py tests/integration/test_api_tasks.py tests/integration/test_delivery.py`
  - `cd control-plane && .venv/bin/python -m ruff check app/core/settings.py app/services/staging_source.py tests/unit/test_staging_source.py`
  - `cd deploy && docker compose config`

### 3.6 Data-plane SourceResolver

- **状态**：`[x]`
- **L 等级**：L3
- **范围**：
  - 抽象 `SourceResolver`，按任务 source 打开 item source。
  - v1 resolver 继续支持 `temp_dir + src_path`。
  - v2 resolver 支持 MinIO / S3 object source。
  - pipeline 改为依赖 resolver，而不是直接构造 file source。
- **验收**：
  - Go 单测覆盖 v1 file resolver 和 v2 object resolver。
  - mock/object source 可在无共享本地目录情况下跑通 pipeline。
- **实际变更**：
  - `data-plane/internal/source`：新增 `Resolver`、`FileResolver`、`ZipArchiveResolver`、`MemorySource` 和 S3 object fetcher。
  - `data-plane/internal/pipeline`：新增 `ProcessTaskWithResolver()`，默认 `ProcessTask()` 仍使用 file resolver。
  - `data-plane/internal/worker`：支持注入 source resolver。
  - `data-plane/cmd/worker`：新增 `-source-mode file|object`，object 模式使用 S3 / MinIO staged archive。
- **验证**：
  - `cd data-plane && GOCACHE=/tmp/smh_go_cache go test ./...`

### 3.7 跨语言 source reference 集成测试

- **状态**：`[x]`
- **L 等级**：L3
- **范围**：
  - control-plane 创建任务并写 staging source。
  - 发布 source reference task。
  - Go worker 消费并从 MinIO 读取源文件。
  - worker 写 result，control-plane 回写 item 状态。
- **验收**：
  - 不依赖 control-plane 解压目录共享。
  - `RUN_DOCKER_TESTS=1` 下可用 Kafka / MinIO / MySQL 跑通核心路径。
- **实际变更**：
  - `control-plane/tests/integration/test_phase2_bridge.py`：新增 source reference file-spool bridge 测试。
  - 测试链路：Python 写 staging MinIO -> file-spool task -> Go worker `-source-mode object` -> result apply。
- **验证**：
  - `cd control-plane && .venv/bin/python -m pytest tests/integration/test_phase2_bridge.py`
  - `cd deploy && docker compose up -d minio minio-init`
  - `cd control-plane && RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest tests/integration/test_phase2_bridge.py::test_source_reference_file_spool_bridge_round_trip`
  - `cd control-plane && .venv/bin/python -m ruff check tests/integration/test_phase2_bridge.py`

### 3.8 文档和运行手册

- **状态**：`[x]`
- **L 等级**：L1
- **范围**：
  - 更新 `README.md`、`control-plane/README.md`、`data-plane/README.md`。
  - 更新 `docs/ARCHITECTURE.md` 写路径。
  - 记录 MySQL 和 source reference 的本地启动命令。
- **验收**：
  - 新开发者能按 README 起 MySQL / Kafka / MinIO 并跑通最小链路。
- **实际变更**：
  - `README.md`：标注 Phase 3 进行中能力。
  - `control-plane/README.md`：补 MySQL、staging bucket、source mode 和测试命令。
  - `data-plane/README.md`：补 `-source-mode object` worker 示例。
  - `docs/ARCHITECTURE.md`：补 source reference 写路径。

### 3.9 Kafka source reference 端到端验证

- **状态**：`[x]`
- **L 等级**：L2
- **范围**：
  - control-plane 将 schema_version=2 source reference task 发布到 Kafka。
  - Go worker 使用 Kafka transport 和 object source mode 消费任务。
  - Go worker 将 result 写回 Kafka。
  - control-plane Kafka result consumer 回写 task / item 状态。
- **验收**：
  - 测试不依赖 control-plane 与 data-plane 共享本地解压目录。
  - Kafka topic 使用测试唯一名，避免重跑时读取历史消息。
- **实际变更**：
  - `control-plane/tests/integration/test_phase2_bridge.py`：新增 source reference Kafka bridge round-trip。
- **验证**：
  - `cd deploy && docker compose up -d kafka minio minio-init`
  - `cd control-plane && RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest tests/integration/test_phase2_bridge.py::test_source_reference_kafka_bridge_round_trip`

## 四、执行顺序建议

1. 先做 MySQL compose 与 migration smoke，保证基础设施可用。
2. 再做 message v2 的 Python / Go 契约测试。
3. 然后做 control-plane staging source。
4. 再做 data-plane SourceResolver。
5. 最后补跨语言集成测试和文档。

不要一开始就改 worker 并发模型。先把“任意 worker 能读到源文件”这个前置条件解决。

## 五、验证计划

默认快速验证：

```bash
cd control-plane
uv run pytest
```

Go 快速验证：

```bash
cd data-plane
GOCACHE=/tmp/smh_go_cache go test ./...
```

Docker 集成验证：

```bash
cd deploy
docker compose up -d mysql kafka minio minio-init
```

MySQL migration 验证：

```bash
cd control-plane
alembic upgrade head
```

source reference 跨语言验证：

```bash
RUN_DOCKER_TESTS=1 RUN_MYSQL_TESTS=1 uv run pytest tests/integration
RUN_DOCKER_TESTS=1 go test ./...
```

具体命令可随实现落地调整。

## 六、风险与控制

- MySQL 与 SQLite 行为不一致：先补 smoke test，不一次性扩大测试矩阵。
- v1 / v2 消息并存导致复杂度上升：所有新增字段必须有 round-trip 测试。
- object source 读取 zip 内单文件可能引入重复下载：本阶段先保证正确性，性能优化留 multipart / cache 阶段。
- staging object 泄露：本阶段先定义 key 和生命周期字段，GC 可后续独立实现。
- Kafka at-least-once 造成重复上传：继续依赖幂等 key、状态机和 result apply 约束，必要时后续补去重表。

## 七、预算档位与派工

- **默认预算档位**：L1 文档和小配置；L2 compose、测试和普通适配；L3 消息契约、migration 兼容、SourceResolver 和跨语言闭环。
- **主编排 Agent 职责**：规划、拆任务、review diff、跑测试、诊断失败、控制接口边界。
- **升档触发**：数据库 schema、消息字段语义、跨语言兼容、worker 读取模型、Kafka ack / 幂等语义。
- **单次派工文件上限**：默认 1-3 个文件；超过 5 个文件先拆任务。

## 八、当前假设

- MySQL 是 Phase 3 的主数据库。
- MinIO 继续作为本地 S3-compatible object storage。
- Kafka 仍是生产形态 transport；file-spool 只作为本地开发和迁移 fallback。
- Phase 3 不删除 `temp_dir`，只把生产路径迁到 source reference。
