# Proto

跨语言消息/接口定义。

## 当前状态
**Phase 0 占位**，可能不会被启用——Kafka 任务消息默认 JSON Schema，性能不够再考虑 Protobuf。

## 可能填充
- `delivery_task.proto` — Kafka 任务消息（如果上 Protobuf）
- `delivery_result.proto` — Kafka 结果事件
