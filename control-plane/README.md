# Control Plane（Python FastAPI）

业务逻辑、规则引擎、Workspace 抽象、读路径鉴权与签发。

## 当前状态
**Phase 0 骨架**。Phase 1 起开始填充。

## 目录
```
app/
  api/           FastAPI 路由（按读/写分组）
  core/          配置、安全、数据库、telemetry
  models/        SQLAlchemy 模型
  schemas/       Pydantic
  services/      规则引擎、分类器、任务编排、Workspace Service
  repos/         数据访问层（强制 tenant_id 过滤）
alembic/         DB migrations
tests/
_legacy/         v0 历史代码，Phase 1 完成后删
```

## 启动方式
（Phase 1 完成后填）
