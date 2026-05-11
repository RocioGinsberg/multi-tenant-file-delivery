# Control Plane（Python FastAPI）

业务逻辑、分类引擎、任务编排与 S3/MinIO 上传。

## 目录结构

```
app/
  api/           FastAPI 路由（/api/v1）
  core/          配置（pydantic-settings）、数据库（SQLAlchemy 2.0 async）
  models/        SQLAlchemy ORM 模型（task / task_item / task_event）
  schemas/       Pydantic v2 response schemas
  services/      classifier、classification_profile、s3_uploader、progress_bus、task_runner
  repos/         数据访问层（task_repo / item_repo / event_repo）
alembic/         DB migrations（SQLite dev，PostgreSQL prod）
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
| `S3_ACCESS_KEY_ID` | `minioadmin` | MinIO root user |
| `S3_SECRET_ACCESS_KEY` | `minioadmin` | MinIO root password |
| `DATABASE_URL` | `sqlite+aiosqlite:///./control_plane.db` | 开发用 SQLite |
| `CLASSIFICATION_PROFILE_PATH` | `../profiles/hq_subsidiary_reports_v1/profile.json` | 分类 profile |

### 3. 起 MinIO（需要 Docker）

```bash
cd ../deploy
docker compose up -d minio minio-init
# minio-init 自动创建 auto-upload-dev bucket，约 10 秒完成
# Console: http://localhost:9001  用户名/密码: minioadmin/minioadmin
```

### 4. 执行数据库迁移

```bash
cd control-plane
alembic upgrade head
```

### 5. 启动 API 服务

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

健康检查：

```bash
curl http://localhost:8000/healthz
# {"ok":true,"service":"control-plane","env":"development"}
```

## API 路由（`/api/v1`）

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/tasks` | 上传 zip，创建 task（multipart/form-data，字段名 `file`） |
| `POST` | `/tasks/{id}/classify` | 调用分类引擎，写入 task_item |
| `GET` | `/tasks/{id}/preview` | 返回分类结果（items + summary） |
| `POST` | `/tasks/{id}/confirm` | 确认，status → confirmed |
| `POST` | `/tasks/{id}/upload` | 触发后台上传（BackgroundTasks） |
| `GET` | `/tasks/{id}/progress` | SSE 实时进度流（text/event-stream） |
| `POST` | `/tasks/{id}/retry` | 重置 failed items → pending |
| `GET` | `/tasks/{id}` | task 详情 |
| `GET` | `/tasks` | task 列表（limit/offset 分页） |

## 运行测试

```bash
# 单元测试（不含 e2e）
pytest tests/ --ignore=tests/test_e2e.py -v

# 包含 e2e（需要内存 SQLite，不需要 MinIO）
pytest tests/ -v

# 只跑 e2e
pytest tests/test_e2e.py -v -m e2e
```

当前测试覆盖：100 个 test case，全部通过。

## 前端

静态前端位于 `../web/public/index.html`，直接用浏览器打开或通过 nginx/静态服务器伺服。
API 请求默认打到同域的 `/api/v1`，dev 环境可用 nginx 反向代理到 `localhost:8000`。
