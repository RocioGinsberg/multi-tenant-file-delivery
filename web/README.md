# Web

HQ 上传台 + 子公司观测窗口的前端。

## 当前状态
**当前状态**：`public/index.html` 是 HQ 写路径 UI。用户选择文件夹，前端以 multipart `files` 字段提交所有文件并保留相对路径；Phase 6.5 加子公司读视图页面。

## 目录
```
public/
  index.html       HQ 上传台主页
js/
  components/      可复用组件（toast / modal / timeline / tabs / ...）
  utils/           api 封装、dom helper、format helper
css/               portal 风格 + tailwind
```

## 当前布局适配
前端请求同域 `/api/v1/...`。本地开发可用静态服务器或 nginx 提供 `web/public`，再把 `/api/v1` 反向代理到 control-plane。
