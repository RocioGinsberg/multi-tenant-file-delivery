# SMH 批量上传临时脚本

用于腾讯企业网盘（SMH）的临时批量上传脚本，包含团队拉取、文件分类、上传三个步骤。

## 项目说明

- 本仓库定位为临时工具脚本，不做完整工程化治理。
- 已通过 `.gitignore` 排除本地运行产物（缓存、日志、`workspace/`、`.env`、密钥等）。
- 建议仅提交代码和必要示例配置，不提交真实数据和凭据。

## 目录结构

```text
smh_uploader/
  __main__.py
  main.py
  config.py
  token_manager.py
  api_client.py
  classifier.py
  uploader.py
.env.example
README.md
.gitignore
```

## 依赖安装

```bash
pip install aiohttp aiofiles python-dotenv cryptography requests pandas fuzzywuzzy python-Levenshtein
```

## 本地配置

```bash
cp .env.example .env
```

编辑 `.env`，至少配置：

- `APP_ID`
- `PRIVATE_KEY_FILE`
- `ORG_ID`
- `LIBRARY_ID`
- `PHONE_NUMBER`
- `WORKSPACE_PATH`

## 使用方式

```bash
python -m smh_uploader run              # teams -> classify -> upload
python -m smh_uploader teams            # 仅拉取团队列表
python -m smh_uploader classify         # 仅分类并生成 CSV
python -m smh_uploader upload [CSV路径]  # 仅上传
python -m smh_uploader                  # 交互模式
```

## 推送前检查

```bash
git status
git add .
git status
```

确认未包含以下内容再提交：

- `.env` / `*.key`
- `workspace/`
- `preproduce/`
- `*.zip`
- `upload_log.txt`
- `分类结果.csv` / `current_teamlist.json`
