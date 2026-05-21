# Phase 4 — Redis 能力层

> **状态**：Current（计划已启动，尚未实现）
> **目标**：把当前单进程内存态能力迁移到 Redis-backed 能力层，为多实例 control-plane、worker 集群 backpressure、跨进程进度和幂等控制打基础。
> **完成定义**：本地 compose 可启动 Redis；control-plane 可用 Redis pub/sub 广播任务进度；任务提交和上传触发有 Redis-backed 幂等 / lease 保护；worker 发布前有可配置限流入口；Redis 不可用时有明确降级或 fail-fast 行为。
> **前序计划**：[Phase 3.x — Source reference 生产化与 worker 集群前置条件](./phase-3x-production-hardening.md)
> **关联 RFC**：[0003 Kafka retry / DLQ / idempotency semantics](../RFC/0003-kafka-retry-dlq-idempotency.md)

## Summary

Phase 3.x 已经把 data-plane 从本地文件依赖里解耦出来，并补齐 Kafka / object source 的主要运行边界。Phase 4 不继续扩展 sink 或多租户模型，而是补一个横向扩展前必须有的共享能力层。

当前系统里仍有几类单进程或单实例假设：

- SSE 进度使用 in-process `ProgressBus`，多 control-plane 实例时订阅者只会收到本进程事件。
- API 层 idempotency 主要依赖数据库唯一键，还没有短 TTL 的“正在处理”去重窗口。
- upload 触发和 result apply 缺少轻量 lease，未来多个调度器 / consumer 并发时容易重复执行同一临界区。
- worker 并发只限制单进程内部，缺少跨 worker / sink / tenant 的集中限流入口。

Phase 4 的原则是“Redis 一物多用，但不滥用”：先做 progress pub/sub、短 TTL idempotency、lease、rate limiter 四类小而明确的能力；不在本阶段引入完整任务队列，也不替代 Kafka。

## 一、先决决策

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| D1 | Redis 部署形态 | 本地 compose 单节点；生产先按 external Redis URL 接入 | 先证明能力边界，不在 demo 阶段引入 Redis Cluster / Sentinel |
| D2 | Python Redis client | `redis.asyncio` | 官方 redis-py 异步 API，依赖面小，适合 FastAPI |
| D3 | Go Redis client | `github.com/redis/go-redis/v9` | Go 生态主流客户端，后续 worker 限流 / lease 可复用 |
| D4 | Progress backend | `memory|redis` 可切换，默认 memory | 保持默认测试和本地轻量路径；Redis opt-in 验证跨进程能力 |
| D5 | Redis 是否替代 Kafka | 不替代 | Kafka 继续承担 durable task/result transport；Redis 只做低延迟共享状态和控制面能力 |
| D6 | Redis 不可用策略 | progress 可降级 memory；lease / limiter / idempotency 默认 fail-fast | 进度丢实时性可接受；并发控制失效不应静默放行生产写路径 |

## 二、范围

### In Scope

- `deploy/docker-compose.yml` 增加 Redis 服务和健康检查。
- `control-plane` 增加 Redis 配置、连接封装和健康检查 smoke。
- `ProgressBus` 抽象出 memory / Redis backend，SSE API 不变。
- Redis pub/sub 覆盖跨进程 progress fanout。
- Redis-backed 短 TTL idempotency guard，用于 create task / upload trigger 的“正在处理”窗口。
- Redis lease helper，用于 upload trigger / result consumer / GC job 的临界区保护。
- Redis token bucket 或 fixed-window limiter，先覆盖 publish task 或 worker sink 入口的最小路径。
- Go worker 增加 Redis limiter client 的配置和本地单测。
- Phase 文档、README、运行命令和测试矩阵同步。

### Out of Scope

- 用 Redis Streams 替代 Kafka。
- Redis Cluster / Sentinel / HA 运维。
- 分布式调度器或完整 worker autoscaling。
- 平台级内容 dedup 表和 workspace object 模型。
- 多租户鉴权和 RBAC。
- 可观测三件套；Phase 5 再统一接 OTel / Prometheus / Grafana。

## 三、任务清单

状态标记：`[ ]` 未开始 / `[~]` 进行中 / `[x]` 已完成 / `[!]` 阻塞

### 4.1 Redis compose 与配置基线

- **状态**：`[x]`
- **L 等级**：L1
- **范围**：
  - `deploy/docker-compose.yml` 增加 Redis 7 服务、端口、healthcheck 和 volume（如需要）。
  - `control-plane/app/core/settings.py` 增加 `redis_url`、`progress_backend`、Redis 超时配置。
  - `data-plane/cmd/worker` 增加 Redis URL / limiter enable 参数占位。
  - README / component README 记录启动命令。
- **验收**：
  - `cd deploy && docker compose up -d redis` 可启动。
  - control-plane settings 单测覆盖默认值和 env override。
  - 不启用 Redis 时默认测试不需要 Redis。
- **实际变更**：
  - `deploy/docker-compose.yml`：新增 Redis 7 compose 服务、healthcheck 和 volume。
  - `control-plane/app/core/settings.py` / `.env.example`：新增 Redis URL、progress backend、socket timeout 和 healthcheck 配置。
  - `data-plane/cmd/worker`：新增 Redis limiter 预留参数，只 parse / validate / redacted log，不改变执行路径。
  - `deploy/README.md`、`control-plane/README.md`、`data-plane/README.md`：补 Redis 启动和参数说明。
- **验证**：
  - `cd deploy && docker compose config`
  - `cd control-plane && .venv/bin/python -m pytest tests/unit/test_settings.py`
  - `cd data-plane && GOCACHE=/tmp/smh_go_cache go test ./cmd/worker`

### 4.2 Redis client 封装与健康检查

- **状态**：`[x]`
- **L 等级**：L2
- **范围**：
  - control-plane 新增 `app/services/redis_client.py` 或 `app/core/redis.py`。
  - 提供 async connection factory、ping、close，测试可注入 fake。
  - health/readiness 增加 Redis opt-in 检查，不影响默认 `/healthz`。
- **验收**：
  - 单测覆盖 ping 成功、连接失败、配置禁用。
  - Docker smoke 可 ping compose Redis。
- **实际变更**：
  - `control-plane/app/services/redis_client.py`：新增 async Redis client wrapper、factory、ping 和 close。
  - `control-plane/app/main.py`：`/healthz` 在 `REDIS_HEALTHCHECK_ENABLED=true` 时执行 Redis ping；默认返回 disabled，不影响现有健康检查。
  - `control-plane/tests/unit/test_redis_client.py`：覆盖 wrapper 和 factory 参数。
  - `control-plane/tests/integration/test_redis_docker.py`：新增 Redis compose opt-in ping smoke。
  - `control-plane/pyproject.toml` / `uv.lock`：新增 `redis` Python client。
- **验证**：
  - `cd control-plane && .venv/bin/python -m pytest tests/unit/test_redis_client.py`
  - `cd control-plane && RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest tests/integration/test_redis_docker.py`
  - `cd control-plane && .venv/bin/python -m pytest tests/integration/test_api_tasks.py::test_healthz tests/integration/test_api_tasks.py::test_healthz_checks_redis_when_enabled`

### 4.3 ProgressBus backend 抽象

- **状态**：`[ ]`
- **L 等级**：L3
- **范围**：
  - 保留当前 `ProgressBus` 接口：`publish(task_id, event)` / `subscribe(task_id)`。
  - 抽象 backend：memory backend 复用当前实现；Redis backend 使用 pub/sub channel。
  - API 层通过 settings 选择 backend，SSE 路由不改契约。
  - 增加 keep-alive / disconnect cleanup 语义，避免长连接泄漏。
- **验收**：
  - 现有 `tests/unit/test_progress_bus.py` 对 memory backend 继续通过。
  - 新增 Redis fake / Docker 测试证明不同 bus 实例可跨进程 fanout。
  - SSE API 测试不需要改调用方式。
- **验证**：
  - `cd control-plane && .venv/bin/python -m pytest tests/unit/test_progress_bus.py tests/integration/test_api_tasks.py::test_progress_sse`
  - `cd control-plane && RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest tests/integration/test_progress_redis_docker.py`

### 4.4 Redis idempotency guard

- **状态**：`[ ]`
- **L 等级**：L2
- **范围**：
  - 新增短 TTL idempotency guard：`SET key value NX EX ttl`。
  - create task 在 DB 唯一键前增加“正在创建”保护，避免并发重复上传写临时目录。
  - upload trigger 对同一 task 增加短 TTL guard，避免用户快速重复点击导致重复 publish。
  - guard key 设计需包含 operation、task/idempotency_key 和版本前缀。
- **验收**：
  - 并发同 idempotency_key create 只有一个执行写路径；其他请求返回已有 task 或 409/202 明确状态。
  - 同 task upload trigger 快速重复调用不会重复发布 delivery task。
  - Redis disabled 时维持当前 DB 唯一键行为。
- **验证**：
  - `cd control-plane && .venv/bin/python -m pytest tests/integration/test_api_tasks.py`
  - `cd control-plane && RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest tests/integration/test_idempotency_redis_docker.py`

### 4.5 Redis lease helper

- **状态**：`[ ]`
- **L 等级**：L3
- **范围**：
  - 实现 owner-token lease：acquire / refresh / release，release 使用 token 校验避免误删他人锁。
  - 先用于 control-plane 的 upload trigger、result consumer 或 staging GC 中一个高价值临界区。
  - lease TTL 和 refresh 间隔可配置。
- **验收**：
  - 单测覆盖 acquire success、already-held、expired reacquire、token mismatch release。
  - 集成测试证明两个 worker/consumer 竞争同一 lease 时只有一个进入临界区。
- **验证**：
  - `cd control-plane && .venv/bin/python -m pytest tests/unit/test_redis_lease.py`
  - `cd control-plane && RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest tests/integration/test_redis_lease_docker.py`

### 4.6 Redis limiter

- **状态**：`[ ]`
- **L 等级**：L3
- **范围**：
  - 定义 limiter key 维度：global、sink、tenant/task 先选最小可落地的一类。
  - control-plane 或 data-plane 实现 token bucket / fixed window limiter。
  - Go worker 支持 Redis limiter client，并在 sink 上传前 acquire。
  - limiter 默认关闭，配置打开后才影响路径。
- **验收**：
  - 单测用 fake clock / fake Redis 验证并发上限。
  - Go worker limiter 失败时返回明确错误，不静默无限等待。
  - 不启用 limiter 时现有 pipeline benchmark 和 tests 不回归。
- **验证**：
  - `cd data-plane && GOCACHE=/tmp/smh_go_cache go test ./internal/... ./cmd/worker`
  - `cd control-plane && .venv/bin/python -m pytest tests/unit/test_rate_limiter.py`

### 4.7 Phase 4 smoke 与运行手册

- **状态**：`[ ]`
- **L 等级**：L1
- **范围**：
  - 补一条端到端 smoke：Redis progress backend + Kafka/object source task + result apply。
  - 更新 `README.md`、`control-plane/README.md`、`data-plane/README.md`、`docs/ARCHITECTURE.md`。
  - 在 `docs/ROADMAP.md` 标注 Phase 4 已完成时的验收口径。
- **验收**：
  - 新开发者能按文档启动 MySQL / Kafka / MinIO / Redis 并跑 smoke。
  - Phase 4 完成前，所有 Redis docker tests 默认跳过且有清晰 env gate。
- **验证**：
  - `cd deploy && docker compose up -d mysql kafka minio minio-init redis`
  - `cd control-plane && RUN_DOCKER_TESTS=1 RUN_MYSQL_TESTS=1 .venv/bin/python -m pytest tests/integration/test_redis_* tests/integration/test_phase2_bridge.py::test_source_reference_kafka_bridge_round_trip`

## 四、建议执行顺序

1. 先做 `4.1` 和 `4.2`，把 Redis 依赖、配置、测试 gate 固定下来。
2. 做 `4.3` progress pub/sub，因为它最直接消除当前 in-process SSE 限制。
3. 做 `4.4` idempotency guard，收紧用户重复提交和重复点击。
4. 做 `4.5` lease，把一个高价值临界区改成可横向扩展。
5. 做 `4.6` limiter，先以最小维度接入，避免一次性设计复杂策略。
6. 做 `4.7` 文档和 smoke，收敛 Phase 4 完成定义。

## 五、验证矩阵

默认快速验证：

```bash
cd control-plane
.venv/bin/python -m pytest
.venv/bin/python -m ruff check app tests

cd ../data-plane
GOCACHE=/tmp/smh_go_cache go test ./...
```

Docker smoke：

```bash
cd deploy
docker compose up -d mysql kafka minio minio-init redis

cd ../control-plane
RUN_MYSQL_TESTS=1 .venv/bin/python -m pytest tests/integration/test_mysql_docker.py
RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest tests/integration/test_redis_docker.py
RUN_DOCKER_TESTS=1 .venv/bin/python -m pytest tests/integration/test_progress_redis_docker.py
```

## 六、风险与降级

- Redis 引入后测试变慢：Redis docker tests 必须保持 opt-in，默认路径使用 memory/fake。
- progress pub/sub 丢事件：SSE 只保证实时观察，不作为最终状态来源；最终状态仍查 DB。
- lease TTL 过短：可能误放并发；实现必须让 TTL 可配置，并用测试覆盖 expired reacquire。
- limiter 维度过早复杂化：先选一个最小可证明维度，不同时做 tenant/sink/global 全组合。
- Redis 不可用：progress 可降级 memory；写路径控制能力默认 fail-fast，避免生产中静默绕过保护。
