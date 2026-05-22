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

Phase 5 本地可观测组件：

```bash
docker compose up -d otel-collector prometheus grafana
```

全栈本地依赖：

```bash
docker compose up -d mysql kafka minio minio-init redis
```

Phase 4 完整 smoke：

```bash
cd ../control-plane
RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest \
  tests/integration/test_phase2_bridge.py::test_phase4_redis_kafka_object_source_smoke

cd ../data-plane
RUN_DOCKER_TESTS=1 GOTOOLCHAIN=local GOPATH=/tmp/smh_go_path \
  GOMODCACHE=/tmp/smh_go_mod_cache GOCACHE=/tmp/smh_go_cache \
  go test ./internal/limiter -run TestRedisLimiterDocker -count=1
```

### 访问

| 用途 | 地址 | 账号/密码 |
|---|---|---|
| Console 管理界面 | http://localhost:9001 | minioadmin / minioadmin |
| API endpoint | http://localhost:9000 | minioadmin / minioadmin |
| Kafka broker | localhost:9092 | PLAINTEXT 本地开发 |
| Redis | localhost:6379 | 无密码，本地开发 |
| OTel Collector OTLP gRPC | localhost:4317 | 无认证，本地开发 |
| OTel Collector OTLP HTTP | http://localhost:4318 | 无认证，本地开发 |
| OTel Collector Prometheus exporter | http://localhost:9464/metrics | 无认证，本地开发 |
| Prometheus | http://localhost:9090 | 无认证，本地开发 |
| Grafana | http://localhost:3000 | admin / admin |

### 可观测配置说明

OTel Collector 接收 OTLP gRPC / HTTP，trace 默认输出到 collector 日志，OTLP metrics 暴露到 `:9464/metrics` 供 Prometheus 抓取。

Prometheus 默认抓取：

| Job | Target |
|---|---|
| `otel-collector` | `otel-collector:9464` |
| `control-plane` | `host.docker.internal:8000/metrics` |
| `data-plane` | `host.docker.internal:8081/metrics` |

`host.docker.internal` 由 compose 映射到 Docker host，适合 control-plane / data-plane 作为本机进程运行、Prometheus 在容器内抓取的开发场景。control-plane 需要以 `METRICS_ENABLED=true` 启动才会开放 `/metrics`；data-plane 需要以 `-metrics-enabled -metrics-listen-addr :8081` 启动才会开放 `/metrics`。

Grafana 会自动配置 Prometheus datasource，并加载 `Phase 5 Observability Overview` 最小 dashboard。

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
├── docker-compose.yml        # MySQL / Kafka / MinIO / Redis / observability 本地开发
├── grafana/                  # Grafana datasource / dashboard provisioning
├── otel/                     # OpenTelemetry Collector 配置
└── prometheus/               # Prometheus 抓取配置
```
