# Web

HQ 上传台 + 子公司观测窗口的前端。

## 当前状态
**Phase 0**：保留 cosdrive 旧前端的壳子（`public/index.html` + `js/components/` + `js/utils/` + `css/`）。Phase 1 改造为 HQ 写路径 UI；Phase 6.5 加子公司读视图页面。

## 目录
```
public/
  index.html       HQ 上传台主页（Phase 1 改造）
js/
  components/      可复用组件（toast / modal / timeline / tabs / ...）
  utils/           api 封装、dom helper、format helper
css/               portal 风格 + tailwind
```

## 当前布局适配
旧前端假定后端路由前缀 `/api/upload/cosdrive`。Phase 1 重写控制面路由后会改成 `/api/v1/...`，对应 js/utils/api.js 也要更新。
