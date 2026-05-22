# Control Plane（Python FastAPI）

业务逻辑、分类引擎、任务编排与 S3/MinIO 上传。

## 目录结构

```
app/
  api/           FastAPI 路由（/api/v1）
  core/          配置（pydantic-settings）、数据库（SQLAlchemy 2.0 async）
  models/        SQLAlchemy ORM 模型（task / task_item / task_event / workspace）
  schemas/       Pydantic v2 response schemas
  services/      classifier、classification_profile、delivery、staging_source、progress_bus、task_runner
  repos/         数据访问层（task_repo / item_repo / event_repo / workspace_repo）
alembic/         DB migrations（SQLite test，MySQL dev/prod target）
tests/           pytest 单测 + e2e 集成测试
_legacy/         v0 历史代码（参考用，不参与构建）
profiles/        静态 classification profile JSON 文件
```

## 本地启动

### 1. 安装依赖

```bash
cd control-plane
uv sync --dev          # 或 pip install -e ".[dev]"
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 默认值已对齐本地 MinIO（见下方），dev 环境无需修改
```

关键字段（`.env.example` 有完整说明）：

| 字段 | 默认值 | 说明 |
|---|---|---|
| `S3_ENDPOINT_URL` | `http://localhost:9000` | MinIO API 端口 |
| `S3_BUCKET_NAME` | `auto-upload-dev` | 目标 bucket |
| `STAGING_BUCKET_NAME` | `auto-upload-staging` | source reference 暂存 bucket |
| `S3_ACCESS_KEY_ID` | `minioadmin` | MinIO root user |
| `S3_SECRET_ACCESS_KEY` | `minioadmin` | MinIO root password |
| `WORKSPACE_DOWNLOAD_URL_TTL_SECONDS` | `300` | Phase 6.5 workspace 下载 URL TTL |
| `DATABASE_URL` | `sqlite+aiosqlite:///./control_plane.db` | 开发用 SQLite |
| `AUTH_ALLOW_DEV_HEADERS` | `true` | 是否允许通过开发 header 指定当前 actor |
| `AUTH_DEFAULT_ACTOR_ENABLED` | `true` | 未提供 actor header 时是否使用默认本地 actor |
| `AUTH_DEFAULT_TENANT_ID` | `hq` | 默认本地 actor 的 tenant |
| `AUTH_DEFAULT_USER_ID` | `local-user` | 默认本地 actor 的 user |
| `AUTH_DEFAULT_ROLE` | `hq_uploader` | 默认本地 actor 的角色 |
| `AUTH_ACTOR_TENANT_HEADER` | `X-Actor-Tenant` | 开发 actor tenant header 名称 |
| `AUTH_ACTOR_USER_HEADER` | `X-Actor-User` | 开发 actor user header 名称 |
| `AUTH_ACTOR_ROLE_HEADER` | `X-Actor-Role` | 开发 actor role header 名称 |
| `CLASSIFICATION_PROFILE_PATH` | `../profiles/hq_subsidiary_reports_v1/profile.json` | 分类 profile |
| `DELIVERY_BACKEND` | `python` | 上传后端：`python` 直传或 `go-worker` outbox |
| `DELIVERY_TRANSPORT` | `file` | `go-worker` 模式下的 transport：`file` 或 `kafka` |
| `DELIVERY_SOURCE_MODE` | `file` | `go-worker` 模式下的 source：`file` 或 `object` |
| `DELIVERY_OUTBOX_BASE` | `/tmp/auto_upload_outbox` | `go-worker` 模式下的本地任务 outbox |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Kafka broker 地址 |
| `KAFKA_TASK_TOPIC` | `delivery.tasks.v1` | 控制面发布任务 topic |
| `KAFKA_RESULT_TOPIC` | `delivery.results.v1` | 控制面消费结果 topic |
| `KAFKA_RESULT_GROUP_ID` | `control-plane-results` | 控制面 result consumer group |
| `REDIS_URL` | `redis://localhost:6379/0` | Phase 4 Redis 能力层连接地址 |
| `PROGRESS_BACKEND` | `memory` | 进度 backend：`memory` 或 `redis`；Redis 模式用于跨实例 SSE fanout |
| `REDIS_SOCKET_TIMEOUT_SECONDS` | `1.0` | Redis socket timeout |
| `REDIS_HEALTHCHECK_ENABLED` | `false` | 是否启用 Redis opt-in health/readiness 检查 |
| `REDIS_IDEMPOTENCY_ENABLED` | `false` | 是否启用 Redis 短 TTL idempotency guard |
| `REDIS_IDEMPOTENCY_TTL_SECONDS` | `60` | create/upload guard claim TTL |
| `REDIS_LEASE_ENABLED` | `false` | 是否启用 Redis lease；当前用于 result apply 临界区 |
| `REDIS_LEASE_TTL_SECONDS` | `30` | Redis lease claim TTL |
| `OBSERVABILITY_ENABLED` | `false` | 是否启用 OpenTelemetry tracing |
| `SERVICE_NAME` | `control-plane` | OTel service name |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4318` | OTel Collector OTLP endpoint |
| `METRICS_ENABLED` | `false` | 是否启用 Prometheus metrics endpoint |
| `METRICS_PATH` | `/metrics` | control-plane metrics path |

### 3. 起本地依赖（需要 Docker）

```bash
cd ../deploy
docker compose up -d minio minio-init
# minio-init 自动创建 auto-upload-dev / auto-upload-staging bucket，约 10 秒完成
# Console: http://localhost:9001  用户名/密码: minioadmin/minioadmin
```

Phase 3 MySQL / Kafka / MinIO 全栈验证：

```bash
docker compose up -d mysql kafka minio minio-init
```

Phase 4 Redis 本地依赖：

```bash
docker compose up -d redis
```

Kafka Docker 集成测试：

```bash
RUN_DOCKER_TESTS=1 KAFKA_BOOTSTRAP_SERVERS=localhost:9092 \
  uv run pytest tests/integration/test_delivery_kafka_docker.py
```

## 配置 Profiles

| Profile | Control-plane 关键 env | Worker 启动参数 | 依赖 |
|---|---|---|---|
| Local file-spool | `DATABASE_URL=sqlite+aiosqlite:///./control_plane.db`<br>`DELIVERY_BACKEND=go-worker`<br>`DELIVERY_TRANSPORT=file`<br>`DELIVERY_SOURCE_MODE=file`<br>`DELIVERY_OUTBOX_BASE=/tmp/auto_upload_outbox` | `-transport file`<br>`-source-mode file`<br>`-sink mock` | 无外部 broker；worker 与 API 共享本机 outbox 和任务工作目录 |
| Docker object source | `DATABASE_URL=mysql+asyncmy://control_plane:control_plane@localhost:3306/control_plane?charset=utf8mb4`<br>`DELIVERY_BACKEND=go-worker`<br>`DELIVERY_TRANSPORT=file`<br>`DELIVERY_SOURCE_MODE=object`<br>`S3_ENDPOINT_URL=http://localhost:9000`<br>`S3_BUCKET_NAME=auto-upload-dev`<br>`STAGING_BUCKET_NAME=auto-upload-staging` | `-transport file`<br>`-source-mode object`<br>`-s3-endpoint http://localhost:9000`<br>`-s3-bucket auto-upload-dev`<br>`-sink mock` 或 `-sink s3` | `docker compose up -d mysql minio minio-init` |
| Production-like Kafka | `DATABASE_URL=mysql+asyncmy://...`<br>`DELIVERY_BACKEND=go-worker`<br>`DELIVERY_TRANSPORT=kafka`<br>`DELIVERY_SOURCE_MODE=object`<br>`KAFKA_BOOTSTRAP_SERVERS=<broker:9092>`<br>`KAFKA_TASK_TOPIC=delivery.tasks.v1`<br>`KAFKA_RESULT_TOPIC=delivery.results.v1`<br>`S3_ENDPOINT_URL=<s3 endpoint>`<br>`S3_BUCKET_NAME=<target bucket>`<br>`STAGING_BUCKET_NAME=<staging bucket>` | `-transport kafka`<br>`-kafka-brokers <broker:9092>`<br>`-kafka-dlq-topic delivery.tasks.dlq.v1`<br>`-source-mode object`<br>`-sink s3`<br>`-s3-endpoint <s3 endpoint>`<br>`-s3-bucket <target bucket>`<br>`-staging-bucket <staging bucket>`<br>`-item-concurrency 4` | MySQL、Kafka、S3-compatible object storage；task/result/DLQ topic 预先创建 |

Production-like 最小环境变量示例：

```bash
export DATABASE_URL='mysql+asyncmy://control_plane:control_plane@localhost:3306/control_plane?charset=utf8mb4'
export DELIVERY_BACKEND=go-worker
export DELIVERY_TRANSPORT=kafka
export DELIVERY_SOURCE_MODE=object
export KAFKA_BOOTSTRAP_SERVERS=localhost:9092
export KAFKA_TASK_TOPIC=delivery.tasks.v1
export KAFKA_RESULT_TOPIC=delivery.results.v1
export KAFKA_RESULT_GROUP_ID=control-plane-results
export S3_ENDPOINT_URL=http://localhost:9000
export S3_BUCKET_NAME=auto-upload-dev
export STAGING_BUCKET_NAME=auto-upload-staging
export S3_ACCESS_KEY_ID=minioadmin
export S3_SECRET_ACCESS_KEY=minioadmin
```

### 4. 执行数据库迁移

```bash
cd control-plane
alembic upgrade head
```

应用启动不会自动创建或 seed 数据表；新库必须先运行 Alembic migration。Phase 6 的默认 `hq` / `local-user` 本地 actor seed 由 `0002_phase6_multitenancy_auth.py` 写入；Phase 6.5 的 demo subsidiary actor 和 `aishide` / `xinyanhaijia` workspace seed 由 `0003_phase65_workspace_read_view.py` 写入。

MySQL 本地 compose：

```bash
DATABASE_URL='mysql+asyncmy://control_plane:control_plane@localhost:3306/control_plane?charset=utf8mb4' \
  .venv/bin/python -m alembic upgrade head
```

### 5. 启动 API 服务

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

健康检查：

```bash
curl http://localhost:8000/healthz
# {"ok":true,"service":"control-plane","env":"development","checks":{"redis":"disabled"}}
```

`REDIS_HEALTHCHECK_ENABLED=true` 时，`/healthz` 会额外 ping `REDIS_URL`，用于 Phase 4 Redis smoke / readiness 验证。

Prometheus metrics：

```bash
METRICS_ENABLED=true uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
curl http://localhost:8000/metrics
```

Phase 5.2 暴露 control-plane HTTP、task workflow 和 delivery publish/apply 的低基数字段 RED 指标。默认 `METRICS_ENABLED=false` 时 `/metrics` 返回 404。

OpenTelemetry tracing：

```bash
OBSERVABILITY_ENABLED=true OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \
  uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Phase 5.3 为 HTTP request、delivery publish、result consume/apply 创建 span，并把当前 trace context 写入 delivery task payload 的 `traceparent` 字段；Kafka transport 同步写 `traceparent` header。

## API 路由（`/api/v1`）

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/tasks` | 上传文件夹，创建 task（multipart/form-data，多文件字段名 `files`，保留相对路径） |
| `POST` | `/tasks/{id}/classify` | 调用分类引擎，写入 task_item |
| `GET` | `/tasks/{id}/preview` | 返回分类结果（items + summary） |
| `POST` | `/tasks/{id}/confirm` | 确认已分类且无阻断错误的 task，status → confirmed |
| `POST` | `/tasks/{id}/upload` | 触发后台上传（BackgroundTasks） |
| `GET` | `/tasks/{id}/progress` | SSE 实时进度流（text/event-stream） |
| `POST` | `/tasks/{id}/retry` | 重置 failed items → pending |
| `GET` | `/tasks/{id}` | task 详情 |
| `GET` | `/tasks` | task 列表（limit/offset 分页） |
| `GET` | `/workspaces` | 当前 actor 可见 workspace 列表 |
| `GET` | `/workspaces/{id}/objects` | 当前 actor 可见 workspace 文件列表 |
| `GET` | `/workspace-objects/{id}` | workspace 文件详情 |
| `POST` | `/workspace-objects/{id}/download-url` | 鉴权后签发短 TTL S3 / MinIO 下载 URL |

## 运行测试

```bash
# 单元测试
pytest tests/unit -v

# 集成测试
pytest tests/integration -v

# 包含 e2e（需要内存 SQLite，不需要 MinIO）
pytest tests/ -v

# 只跑 e2e
pytest tests/e2e -v -m e2e

# MySQL smoke（需要 deploy/docker-compose.yml mysql running）
RUN_MYSQL_TESTS=1 .venv/bin/python -m pytest tests/integration/test_mysql_docker.py

# Redis smoke（需要 deploy/docker-compose.yml redis running）
RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest tests/integration/test_redis_docker.py

# Redis progress backend smoke（证明两个 control-plane bus 实例可跨 Redis fanout）
RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest tests/integration/test_progress_redis_docker.py

# Redis idempotency guard smoke（证明两个 control-plane 实例共享短 TTL claim）
RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest tests/integration/test_idempotency_redis_docker.py

# Redis lease smoke（证明两个 consumer 实例竞争同一 lease 时只有一个成功）
RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest tests/integration/test_redis_lease_docker.py

# Phase 4 full smoke（Redis progress + Kafka/object source + Go worker limiter + result apply lease）
RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest \
  tests/integration/test_phase2_bridge.py::test_phase4_redis_kafka_object_source_smoke

# Phase 5 observability smoke（自动启动 Docker 依赖并验证 metrics + trace context）
RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest tests/integration/test_observability_docker.py

# source reference bridge（需要 MinIO running）
RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest \
  tests/integration/test_phase2_bridge.py::test_source_reference_file_spool_bridge_round_trip

# source reference + Kafka bridge（需要 Kafka / MinIO running）
RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest \
  tests/integration/test_phase2_bridge.py::test_source_reference_kafka_bridge_round_trip
```

当前测试按 `unit / integration / e2e` 分层组织。

## Staging GC

object source 模式会把上传文件夹生成的内部 archive 暂存到 `STAGING_BUCKET_NAME`。终态 task 超过 retention 后，可手动清理：

```bash
python -m app.jobs.cleanup_staging_sources --retention-days 7 --bucket-name auto-upload-staging
```

## 前端

静态前端写路径位于 `../web/public/index.html`，Phase 6.5 子公司读视图位于 `../web/public/workspaces.html`；直接用浏览器打开或通过 nginx/静态服务器伺服。
API 请求默认打到同域的 `/api/v1`，dev 环境可用 nginx 反向代理到 `localhost:8000`。
