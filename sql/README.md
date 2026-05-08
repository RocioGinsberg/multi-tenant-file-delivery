`cosdrive_portal_schema.sql` 只保留了 CosDrive 相关表定义片段，来源于主仓库 `sql/02_portal_state_init.sql`。

如果要在全新数据库中初始化：

1. 先补公共函数 `trigger_set_updated_at()`
2. 再执行 `cosdrive_portal_schema.sql`
3. 再初始化注册表数据和运行环境变量
