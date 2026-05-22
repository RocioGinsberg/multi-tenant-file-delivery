# Phase 5 — 可观测三件套

> **状态**：Current（5.1 已完成；5.2-5.7 待实现）
> **目标**：补齐 Python control-plane -> Kafka -> Go data-plane -> sink 的 trace context、RED 指标和本地运行面板，让跨组件问题能被定位，而不是只能靠日志和 smoke。
> **完成定义**：本地 compose 可启动 Prometheus、Grafana、OpenTelemetry Collector；control-plane 和 data-plane 暴露 Prometheus metrics；Kafka task message 携带 W3C trace context；Go worker 能从消息恢复 trace；Phase 5 smoke 能证明一次 object-source 任务在 trace / metrics / dashboard 维度可观测。
> **前序计划**：[Phase 4 — Redis 能力层](./phase-4-redis-capabilities.md)
> **相关背景**：[Technical Notes — OTel 跨语言 trace](../TECHNICAL_NOTES.md#otel-跨语言-trace)

## Summary

Phase 4 已经完成 Redis 共享能力层，但当前系统排障仍主要依赖测试输出和局部日志。Phase 5 的目标不是扩大业务功能，而是让现有链路可被观察：

- control-plane API 请求、Kafka publish、result apply 能有 trace span 和 RED 指标。
- data-plane worker consume、source read、sink upload、result publish 能有 trace span 和 RED 指标。
- Kafka task payload 已有 `traceparent` 字段，Phase 5 要把它从保留字段变成真实 W3C trace context。
- 本地 compose 能启动 OTel Collector、Prometheus、Grafana，并提供最小 dashboard / smoke runbook。

Phase 5 的原则：先做最小闭环，默认本地低成本运行；不要求生产级告警体系，不把业务状态机重写成观测框架。

## 一、先决决策

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| D1 | Trace 标准 | W3C `traceparent` + OpenTelemetry SDK | 已在 task message contract 预留字段；Python / Go 都有成熟 SDK |
| D2 | Trace export | OTLP HTTP / gRPC 到 OTel Collector | 应用只依赖 collector endpoint，后端可替换 Jaeger / Tempo / logging exporter |
| D3 | Metrics 暴露 | Prometheus scrape `/metrics` | 本地验证简单；RED 指标和 runtime 指标都适合 Prometheus |
| D4 | Dashboard | Grafana 最小 dashboard JSON | 只做 Phase 5 验收所需面板，不追求完整运维平台 |
| D5 | 默认行为 | Observability 默认可关闭或 no-op | 保持默认测试轻量，不要求每次本地开发启动 collector/prometheus |
| D6 | 指标命名 | 先按 component + operation 命名 | 避免过早引入 tenant/workspace 高基数字段；Phase 6 后再细化标签 |

## 二、范围

### In Scope

- `deploy/docker-compose.yml` 增加 Prometheus、Grafana、OTel Collector，以及对应配置目录。
- control-plane 增加 observability settings、trace provider 初始化和 `/metrics` endpoint。
- control-plane 在 API create/classify/confirm/upload/result apply/Kafka publish/consume 周围补 trace spans 和 RED metrics。
- delivery task message 注入真实 `traceparent`。
- data-plane 增加 trace provider、Prometheus metrics server 或 endpoint、worker / pipeline / transport / sink spans 和 RED metrics。
- Go worker 从 task message `traceparent` 恢复上下文，并在 result publish 前继续 trace。
- 一条 Docker opt-in smoke 验证 trace context 和 metrics 至少可被本地端点观察。
- README、architecture、roadmap、runbook 和 dashboard 同步。

### Out of Scope

- 完整告警规则、SLO / error budget 流程。
- 日志聚合栈（Loki / ELK）和结构化日志全量改造。
- 生产级 Grafana 权限、持久化和多环境 dashboard 管理。
- 高基数 tenant / task / item label 全量上报。
- OpenTelemetry auto-instrumentation 覆盖所有第三方库。
- Kafka broker 自身、Redis、MySQL、MinIO 的完整 exporter 体系；本阶段只保留最小应用观测。

## 三、任务清单

状态标记：`[ ]` 未开始 / `[~]` 进行中 / `[x]` 已完成 / `[!]` 阻塞

### 5.1 Observability compose 与配置基线

- **状态**：`[x]`
- **L 等级**：L1
- **范围**：
  - `deploy/docker-compose.yml` 增加 `otel-collector`、`prometheus`、`grafana`。
  - 新增 `deploy/otel/collector.yml`、`deploy/prometheus/prometheus.yml`、`deploy/grafana/` dashboard / datasource 最小配置。
  - `control-plane/app/core/settings.py` 增加 observability enable、service name、OTLP endpoint、metrics enable 配置。
  - `data-plane/cmd/worker` 增加 observability flags 占位。
  - README / deploy README 记录启动和访问地址。
- **验收**：
  - `cd deploy && docker compose config` 通过。
  - `cd deploy && docker compose up -d otel-collector prometheus grafana` 可启动。
  - 默认应用测试不要求这些服务存在。
- **建议执行方**：L1 worker 或主 Agent。
- **实际执行**：
  - deploy 配置由 worker（medium）实现，主 Agent 审计。
  - data-plane CLI placeholder flags 由 worker（medium）实现，主 Agent 审计。
  - control-plane settings / env / docs 由主 Agent 实现。
- **实际变更**：
  - `deploy/docker-compose.yml`：新增 `otel-collector`、`prometheus`、`grafana` 和 `grafana_data`。
  - `deploy/otel/collector.yml`：新增 OTLP gRPC / HTTP receiver、debug trace exporter、Prometheus metrics exporter。
  - `deploy/prometheus/prometheus.yml`：新增 Prometheus / OTel Collector / control-plane / data-plane 最小 scrape config。
  - `deploy/grafana/provisioning` 和 `deploy/grafana/dashboards/phase5-overview.json`：新增 Prometheus datasource 和最小 scrape-status dashboard。
  - `control-plane/app/core/settings.py`、`.env.example`、`README.md`：新增 observability no-op settings，并修正 folder/internal archive limit env 名称。
  - `data-plane/cmd/worker`、`data-plane/README.md`：新增 metrics/tracing placeholder flags，默认关闭，只 parse / validate / log。
- **验证**：
  - `cd deploy && docker compose config`
  - `cd control-plane && .venv/bin/python -m pytest tests/unit/test_settings.py`
  - `cd data-plane && GOTOOLCHAIN=local GOCACHE=/tmp/smh_go_cache go test ./cmd/worker`

### 5.2 control-plane metrics baseline

- **状态**：`[x]`
- **L 等级**：L2
- **范围**：
  - 增加 Prometheus client dependency。
  - `app/main.py` 暴露 `/metrics`，默认可由 setting 控制。
  - 为 API 请求、task create/classify/confirm/upload、Kafka publish/result consume/result apply 添加 RED 指标。
  - 指标标签限制在 `component`、`operation`、`status`、`transport` 等低基数字段。
- **验收**：
  - `/metrics` 能返回 Prometheus text format。
  - 单测覆盖 metrics disabled / enabled。
  - 默认 pytest 不依赖 Prometheus。
- **建议执行方**：L2 worker；主 Agent review 指标命名和标签基数。
- **实际执行**：主 Agent 实现并审计。
- **实际变更**：
  - `control-plane/pyproject.toml` / `uv.lock`：新增 `prometheus-client`。
  - `control-plane/app/services/metrics.py`：新增独立 Prometheus registry、HTTP middleware metrics、task workflow metrics、delivery metrics 和 `/metrics` response helper。
  - `control-plane/app/main.py`：接入 metrics middleware，并在 `METRICS_ENABLED=true` 时开放 configured metrics path。
  - `control-plane/app/api/tasks.py`：为 create/classify/confirm/upload 记录 task operation RED 指标，覆盖 claim 获取失败等错误路径。
  - `control-plane/app/services/delivery.py`：为 file/kafka task publish、result consume、result apply 记录 delivery RED 指标。
  - `control-plane/tests/integration/test_api_tasks.py`：新增 metrics endpoint disabled/enabled 覆盖。
  - `control-plane/README.md`：补 metrics 启用和 curl 命令。
- **验证**：
  - `cd control-plane && .venv/bin/python -m pytest tests/integration/test_api_tasks.py::test_metrics_endpoint_disabled_by_default tests/integration/test_api_tasks.py::test_metrics_endpoint_records_http_requests_when_enabled`
  - `cd control-plane && .venv/bin/python -m pytest tests/integration/test_api_tasks.py tests/integration/test_delivery.py`
  - `cd control-plane && .venv/bin/python -m ruff check app tests`

### 5.3 control-plane trace context 注入

- **状态**：`[ ]`
- **L 等级**：L3
- **范围**：
  - 接入 OpenTelemetry Python SDK，支持 no-op / enabled。
  - FastAPI 请求创建 server span；delivery publish / result consume / result apply 创建内部 span。
  - `build_delivery_task_message()` 默认从当前 context 注入 W3C `traceparent`。
  - Kafka publisher 如可行同步写 Kafka headers；payload `traceparent` 作为跨语言稳定兼容字段。
- **验收**：
  - 单测证明有 active span 时 task message 含合法 `traceparent`。
  - 无 OTel / disabled 时 payload 兼容，`traceparent` 可为空。
  - Kafka/object-source smoke 不回归。
- **建议执行方**：主 Agent 或 L3 worker；涉及跨语言 contract。
- **验证**：
  - `cd control-plane && .venv/bin/python -m pytest tests/integration/test_delivery.py tests/integration/test_api_tasks.py`
  - `cd control-plane && .venv/bin/python -m ruff check app tests`

### 5.4 data-plane metrics baseline

- **状态**：`[ ]`
- **L 等级**：L2
- **范围**：
  - Go worker 增加 metrics HTTP endpoint 或复用现有 server 入口。
  - 为 task consume、source read、sink upload、result publish、limiter acquire 添加 RED 指标。
  - 默认关闭或监听独立端口，避免影响现有 worker path。
- **验收**：
  - `go test ./...` 通过。
  - 单测或 integration test 能读取 metrics endpoint 并看到 worker 指标。
  - 不启用 metrics 时 CLI 行为不变。
- **建议执行方**：L2 worker。
- **验证**：
  - `cd data-plane && GOTOOLCHAIN=local GOCACHE=/tmp/smh_go_cache go test ./...`

### 5.5 data-plane trace context 提取与 sink spans

- **状态**：`[ ]`
- **L 等级**：L3
- **范围**：
  - 接入 OpenTelemetry Go SDK，支持 OTLP endpoint 和 no-op。
  - 从 `message.DeliveryTask.Traceparent` 提取 remote parent。
  - worker consume、source resolver、pipeline item、sink upload、result publish 创建 spans。
  - 错误路径把 item failure / task partial_failed 记录到 span status/event。
- **验收**：
  - Go 单测证明 traceparent 提取后 span parent 正确。
  - Kafka/object-source smoke 可在日志或 collector debug exporter 中看到同一 trace ID。
  - 无 traceparent 时 worker 正常处理任务。
- **建议执行方**：主 Agent 或 L3 worker；涉及跨语言 contract 和 worker critical path。
- **验证**：
  - `cd data-plane && GOTOOLCHAIN=local GOCACHE=/tmp/smh_go_cache go test ./...`
  - `cd control-plane && RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest tests/integration/test_phase2_bridge.py::test_phase4_redis_kafka_object_source_smoke`

### 5.6 Phase 5 observability smoke

- **状态**：`[ ]`
- **L 等级**：L2
- **范围**：
  - 新增一条 Docker opt-in smoke，启动 MySQL / Kafka / MinIO / Redis / OTel / Prometheus。
  - 发布一次 object-source task，验证 traceparent 从 Python 进入 Kafka payload，再被 Go worker 处理。
  - 验证 control-plane `/metrics` 和 data-plane metrics endpoint 有关键 RED 指标。
  - 如 collector 使用 debug/logging exporter，验证同一 trace ID 出现在 control-plane 和 data-plane spans。
- **验收**：
  - smoke 默认跳过，`RUN_DOCKER_TESTS=1` 才执行。
  - 失败信息能指明是 collector / metrics / Kafka / worker 哪一段异常。
- **建议执行方**：L2 worker；主 Agent 负责最终审计。
- **验证**：
  - `cd deploy && docker compose up -d mysql kafka minio minio-init redis otel-collector prometheus grafana`
  - `cd control-plane && RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest tests/integration/test_observability_docker.py`

### 5.7 Dashboard、runbook 与阶段收口

- **状态**：`[ ]`
- **L 等级**：L1
- **范围**：
  - 补 Grafana dashboard：control-plane RED、data-plane RED、worker upload rate/error、result apply error。
  - 更新 `README.md`、`deploy/README.md`、`control-plane/README.md`、`data-plane/README.md`、`docs/ARCHITECTURE.md`、`docs/ROADMAP.md`。
  - Phase 5 完成后把 Phase 6 标记为 Current。
- **验收**：
  - 新开发者能按文档启动 observability stack 并跑 smoke。
  - Phase 5 plan 中每项状态和实际验证命令同步。
- **建议执行方**：L1 worker 或主 Agent。
- **验证**：
  - `cd deploy && docker compose config`
  - 文档-only 部分可不跑全量测试；若改 dashboard JSON，至少确认 compose config。

## 四、建议执行顺序

1. 先做 `5.1`，固定 compose、settings、依赖和默认 no-op 行为。
2. 做 `5.2` 和 `5.4`，先让两端 metrics 可见，低风险且便于后续 smoke。
3. 做 `5.3`，让 Python 注入真实 trace context。
4. 做 `5.5`，让 Go 端提取 trace context 并补 worker / sink spans。
5. 做 `5.6`，用一条 Docker smoke 串联 trace + metrics。
6. 做 `5.7`，补 dashboard / runbook 并关闭 Phase 5。

## 五、验证矩阵

默认快速验证：

```bash
cd control-plane
.venv/bin/python -m ruff check app tests
.venv/bin/python -m pytest

cd ../data-plane
GOTOOLCHAIN=local GOPATH=/tmp/smh_go_path \
  GOMODCACHE=/tmp/smh_go_mod_cache GOCACHE=/tmp/smh_go_cache \
  go test ./...
```

Docker smoke：

```bash
cd deploy
docker compose up -d mysql kafka minio minio-init redis otel-collector prometheus grafana

cd ../control-plane
RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest tests/integration/test_observability_docker.py
```

## 六、风险与降级

- **观测依赖不可用**：默认 no-op 或 disabled；生产打开后 exporter 失败不应阻塞上传主链路。
- **指标标签爆炸**：禁止把 task_id、item_id、filename、tenant_name 作为默认 Prometheus label；这些只进入 trace attributes 或 logs。
- **trace context 丢失**：payload `traceparent` 是跨语言稳定路径；Kafka headers 作为增强，不作为唯一依赖。
- **依赖膨胀**：OTel SDK 和 Prometheus client 分阶段引入；每次新增依赖都跑全量测试。
- **smoke 偶发**：Docker smoke 必须给出分段失败信息，便于定位是 observability stack 还是业务链路问题。

## 七、派工建议

- L1：`5.1`、`5.7` 可派低成本 worker，主 Agent review 配置和文档。
- L2：`5.2`、`5.4`、`5.6` 可派中等模型 worker，要求文件边界清晰。
- L3：`5.3`、`5.5` 由主 Agent 或高可靠 worker 执行；涉及跨语言 trace contract、消息兼容和 worker critical path。

每个子任务完成后必须记录：

- 实际执行方 / 模型档位。
- 改动文件。
- 验证命令和结果。
- 是否偏离本计划，若偏离要说明原因。
