# Deploy

docker-compose、可观测组件配置、SQL 初始化脚本。

## 本地依赖

### 启动

```bash
docker compose up -d minio minio-init
```

约 10 秒后 `minio-init` 自动创建 bucket `auto-upload-dev`，无需手动操作。

Phase 2 Kafka 验证：

```bash
docker compose up -d kafka
```

同时启动 Kafka 和 MinIO：

```bash
docker compose up -d kafka minio minio-init
```

Phase 4 Redis 能力层验证：

```bash
docker compose up -d redis
```

全栈本地依赖：

```bash
docker compose up -d mysql kafka minio minio-init redis
```

### 访问

| 用途 | 地址 | 账号/密码 |
|---|---|---|
| Console 管理界面 | http://localhost:9001 | minioadmin / minioadmin |
| API endpoint | http://localhost:9000 | minioadmin / minioadmin |
| Kafka broker | localhost:9092 | PLAINTEXT 本地开发 |
| Redis | localhost:6379 | 无密码，本地开发 |

### 停止

```bash
docker compose down        # 保留数据
docker compose down -v     # 清除数据（含 volume）
```

## 与 control-plane 对接

`control-plane/.env` 中 S3/MinIO 相关默认值已对齐本地 dev 配置：

```
S3_ENDPOINT_URL=http://localhost:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_BUCKET=auto-upload-dev
```

本地启动 MinIO 后，直接运行 control-plane 无需修改任何配置。

## 目录结构

```
deploy/
├── docker-compose.yml        # MySQL / Kafka / MinIO / Redis 本地开发
├── grafana/                  # Phase 5 填充：Grafana dashboard
├── otel/                     # Phase 5 填充：OpenTelemetry Collector 配置
└── prometheus/               # Phase 5 填充：Prometheus 抓取配置
```
