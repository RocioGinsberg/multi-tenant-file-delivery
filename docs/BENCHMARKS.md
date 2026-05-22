# Benchmarks

每组实验包含：场景描述、参数、结果数据、结论。压测目标来自
[ROADMAP](./ROADMAP.md) 的后续阶段和 [PRD](./PRD.md) 的成功标准。

## Phase 3.x baseline: Go pipeline mock sink

**日期**：2026-05-20

**目标**：建立 Phase 3.x 的本地可复跑基线，先隔离 Go data-plane pipeline
自身开销。该基线不包含 Kafka、S3 / MinIO 网络、zip staging 下载，也不代表端到端吞吐。

**复跑命令**：

```bash
cd data-plane
GOCACHE=/tmp/smh_go_cache go test ./internal/pipeline \
  -run '^$' -bench 'BenchmarkProcessTaskMockSink' -benchmem -count 3
```

**环境**：

| 字段 | 值 |
| --- | --- |
| OS / arch | linux / amd64 |
| CPU | Intel(R) Core(TM) i9-7960X CPU @ 2.80GHz |
| Go package | `smh_auto_upload/data-plane/internal/pipeline` |
| Sink | `MockSink`，上传内容保存在内存 map |
| Source | `MemorySource`，每个 item 读取同一份内存 payload |

**代表结果**：下表取 3 次运行的中间值，便于人工比较；完整复验以本机命令输出为准。

| Items | File size | Item concurrency | ns/op | Throughput | B/op | allocs/op |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 16 KiB | 1 | 1,861,744 | 88.00 MB/s | 855,756 | 225 |
| 10 | 16 KiB | 4 | 1,355,354 | 120.88 MB/s | 855,783 | 225 |
| 100 | 16 KiB | 1 | 20,500,527 | 79.92 MB/s | 8,551,878 | 2,126 |
| 100 | 16 KiB | 4 | 9,818,145 | 166.87 MB/s | 8,551,881 | 2,127 |
| 1000 | 16 KiB | 1 | 161,537,492 | 101.43 MB/s | 85,640,809 | 21,049 |
| 1000 | 16 KiB | 4 | 54,476,439 | 300.75 MB/s | 85,641,072 | 21,051 |
| 10 | 1 MiB | 1 | 99,200,959 | 105.70 MB/s | 52,424,384 | 375 |
| 10 | 1 MiB | 4 | 34,536,378 | 303.61 MB/s | 52,424,477 | 375 |
| 100 | 1 MiB | 1 | 634,240,426 | 165.33 MB/s | 524,237,952 | 3,625 |
| 100 | 1 MiB | 4 | 207,492,917 | 505.36 MB/s | 524,238,485 | 3,627 |
| 1000 | 1 MiB | 1 | 4,433,198,319 | 236.53 MB/s | 5,242,506,048 | 36,052 |
| 1000 | 1 MiB | 4 | 1,278,778,484 | 819.98 MB/s | 5,242,504,224 | 36,049 |

**结论**：

- `MaxItemConcurrency=4` 对 100 / 1000 item 场景有稳定收益，尤其是 1 MiB payload。
- 该 benchmark 仍会在 mock sink 中复制并保存全部 object bytes，因此 `B/op` 约等于总 payload
  的多份内存占用；后续 S3 multipart / streaming sink benchmark 需要单独建模。
- Phase 3.x 后续 Kafka + object source 端到端 benchmark 应优先补充真实 broker、MinIO
  和 result apply 的链路耗时。

## 待做实验

- 平台层 dedup 命中率（HQ -> N 子公司，首版重头戏）
- 平台 dedup vs sink 内置 dedup（Phase 7 stretch，如果接入 SMH）
- 流式 vs 整文件入内存（Go pipeline vs Python BytesIO）
- multipart vs 单段
- AIMD vs 固定限流
- 单 worker vs N worker 横向扩缩
- 优雅关停 vs 强杀
