# CosDrive Local Service

这个目录把原项目里的 `cosdrive` 相关后端、执行侧 worker、前端页面和 SQL 片段集中成一个本地微服务骨架，方便继续独立化，而不是继续混在 `portal-api` / `portal-web` / `jobs` 里。

## 目录

- `app/`：FastAPI 服务入口、路由、repo、service、schema
- `jobs/`：分类器、SMH 客户端、task service、独立 worker
- `web/`：从 Portal 抽出的 CosDrive 页面和静态资源
- `sql/`：CosDrive 相关表结构片段
- `env/`：本地运行环境变量样例

## 已抽出的内容

- Portal API 路由：`/api/upload/cosdrive/*`
- 注册表管理逻辑：draft / validate / publish / rollback
- Task 流程：zip 上传、分类、确认、上传、重试、详情、进度
- Worker 逻辑：逐文件投递、retry、断路器、attempt/event 写库
- Web 页面：当前 `cosdrive.html` 相关样式和脚本

## 仍然保留的仓库级依赖

- `libs/runner-shared`：worker 仍通过其中的 `portal_state` 写 `cosdrive_*` 状态表
- `libs/runtime-observability` / `libs/platform-core`：部分共用库仍从主仓库复用
- `portal_db`：当前仍复用原项目的 `cosdrive_*` 表，不是新数据库

## 启动方式

1. 载入环境变量，参考 `env/cosdrive.env.example`
2. 从本目录启动：

```bash
cd services/cosdrive-local-service
export PYTHONPATH="$(pwd):/home/rocio/projects/smartOps:/home/rocio/projects/smartOps/libs/runner-shared/src:/home/rocio/projects/smartOps/libs/runtime-observability/src:/home/rocio/projects/smartOps/libs/platform-core/src"
uvicorn app.main:app --reload --port 8310
```

3. 打开：

```text
http://localhost:8310/
```

## 当前约束

- 默认 `COSDRIVE_DISPATCH_MODE=local-process`，上传任务由本目录下的 worker 直接以子进程方式触发，不依赖 Prefect。
- 前端页面是 Portal 页面快照，功能已经能对接本服务，但视觉和导航仍保留原 Portal 风格。
- SQL 只抽了 `cosdrive` 相关表段；如果单独建库，还需要补公共函数，例如 `trigger_set_updated_at()`。

## 后续建议

- 把 `runner_shared.worker_state.portal_state` 里的 CosDrive 写库逻辑继续下沉到本目录，去掉对主仓库共享库的依赖。
- 单独定义 `pyproject.toml` 和容器镜像。
- 把 `web/` 从 Portal 公共组件中再裁一轮，去掉无关导航和运行态组件。
