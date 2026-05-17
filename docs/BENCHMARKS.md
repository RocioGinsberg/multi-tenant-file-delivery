# Benchmarks

> Phase 7 之后填充。每组实验包含：场景描述、参数、结果数据、结论。
> 压测目标来自 [ROADMAP](./ROADMAP.md) 的后续阶段和 [PDR](./PDR.md) 的成功标准。

## 待做实验
- 平台层 dedup 命中率（HQ → N 子公司，首版重头戏）
- 平台 dedup vs sink 内置 dedup（Phase 7 stretch，如果接入 SMH）
- 流式 vs 整文件入内存（Go pipeline vs Python BytesIO）
- multipart vs 单段
- AIMD vs 固定限流
- 单 worker vs N worker 横向扩缩
- 优雅关停 vs 强杀
