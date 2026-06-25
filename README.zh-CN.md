# easy-pika-cli

PikPak 网盘下载工具：纯 CLI、磁链中转、Telegram 机器人、Aria2、AI HTTP API。

**English:** [README.en.md](README.en.md)

## 快速开始（纯 CLI）

```bash
pip install -r requirements.txt
python main.py login -u 邮箱 -p 密码
python main.py ls /
python main.py download 文件ID -o ./downloads
```

图形界面（可选）：`pip install -r requirements-gui.txt` 后执行 `python main.py gui`

完整功能（Telegram + AI API）：`pip install -r requirements-full.txt`

## 中转模式（PikPak 作中转站）

利用 PikPak 离线下载获取更优 CDN，再拉回本地并清理网盘，节省空间。

**一次性完整流程：**

```bash
python main.py relay run "magnet:?xt=urn:btih:..." -o ./downloads
```

**分步执行（可单独运行）：**

| 步骤 | 命令 | 说明 |
|------|------|------|
| 1 上传 | `relay upload "magnet:..."` | 仅提交磁链到 PikPak |
| 2 等待 | `offline wait TASK_ID FILE_ID` | 等待云端离线下载完成 |
| 3 下载 | `relay download FILE_ID -o ./downloads` | 从 PikPak CDN 下载到本地 |
| 4 清理 | `relay cleanup FILE_ID --task-ids TASK_ID` | 删除网盘文件与离线任务 |

`relay run` 等价于上述四步串联。加 `--no-cleanup` 可保留下载后的网盘文件。

## 离线下载命令

```bash
python main.py offline add "magnet:?xt=..."
python main.py offline list --phase running
python main.py offline wait TASK_ID FILE_ID --timeout 7200
```

## Aria2 支持

将 PikPak CDN 链接推送到 Aria2 JSON-RPC，由 Aria2 负责实际下载：

```bash
python main.py download 文件ID -o ./downloads \
  --backend aria2 \
  --aria2-rpc http://127.0.0.1:6800/jsonrpc \
  --aria2-secret 你的密钥

python main.py relay run "magnet:..." --backend aria2
```

环境变量：

- `DOWNLOAD_BACKEND=aria2`
- `ARIA2_RPC_URL`
- `ARIA2_RPC_SECRET`

## Telegram 机器人

1. 向 [@BotFather](https://t.me/BotFather) 创建 Bot，获取 Token  
2. 配置环境变量并启动：

```bash
export TELEGRAM_BOT_TOKEN=你的Token
export TELEGRAM_ALLOWED_USERS=你的用户ID   # 可选，逗号分隔；留空则允许所有人
export DOWNLOAD_DIR=./downloads
python main.py telegram
```

3. 向机器人发送磁链或 `.torrent` 链接，自动执行：上传 → 等待 → 下载 → 清理  

若设置 `OPENAI_API_KEY`，可用 LLM 从自然语言消息中提取磁链（OpenAI 兼容 API）。

## AI 调用 / 自动化 API

供 AI Agent、n8n、脚本等通过 HTTP 调用本工具：

```bash
export AI_API_KEY=请设置强密钥
python main.py ai serve --host 0.0.0.0 --port 8765
```

鉴权：`Authorization: Bearer <AI_API_KEY>` 或请求头 `X-API-Key`。

主要接口：

| 路径 | 方法 | 功能 |
|------|------|------|
| `/v1/relay` | POST | 完整中转，body 可设 `upload/wait/download/cleanup` |
| `/v1/offline/add` | POST | 提交磁链 |
| `/v1/offline/wait` | POST | 等待离线任务 |
| `/v1/offline/list` | GET | 离线任务列表 |
| `/v1/download` | POST | 按 file_id 下载 |
| `/v1/cleanup` | POST | 清理网盘 |
| `/v1/parse` | POST | 从文本提取链接，`use_llm: true` 启用 LLM |
| `/v1/quota` | GET | 空间配额 |

示例：

```bash
curl -H "Authorization: Bearer 你的密钥" \
  -H "Content-Type: application/json" \
  -d '{"magnet":"magnet:?xt=...","cleanup":true}' \
  http://127.0.0.1:8765/v1/relay
```

## Docker 部署

```bash
cp .env.example .env
# 编辑 .env 填写账号、Token、密钥等

docker compose build

# 首次登录（会话写入 volume）
docker compose run --rm easy-pika-cli login -u 用户 -p 密码 --session /data/session/session.json

# 中转
docker compose run --rm easy-pika-cli relay run "magnet:..." \
  -o /data/downloads --session /data/session/session.json

# 后台运行 Telegram
docker compose --profile telegram up -d

# 后台运行 AI API
docker compose --profile ai up -d

# 单独启动 Aria2
docker compose --profile aria2 up -d
```

`docker-compose.yml` 中 `SESSION_PATH=/data/session/session.json` 已预置，持久化卷：`session`、`downloads`。

## 环境变量一览

见 [.env.example](.env.example)。

| 变量 | 说明 |
|------|------|
| `SESSION_PATH` | 会话文件路径 |
| `DOWNLOAD_DIR` | 本地下载目录 |
| `DOWNLOAD_BACKEND` | `native` 或 `aria2` |
| `RELAY_CLEANUP_CLOUD` | 中转后是否清理网盘（默认 true） |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token |
| `AI_API_KEY` | HTTP API 鉴权密钥 |
| `OPENAI_API_KEY` | 可选，LLM 解析消息 |

## CLI 命令总览

| 命令 | 说明 |
|------|------|
| `login` | 登录并保存会话 |
| `ls` | 浏览网盘 |
| `download` | 多线程下载（支持 `--backend aria2`） |
| `quota` | 查看空间 |
| `offline add/list/wait` | 离线下载管理 |
| `relay run/upload/download/cleanup` | 中转流程（可合并或分步） |
| `telegram` | Telegram 机器人 |
| `ai serve` | AI / 自动化 HTTP 服务 |
| `gui` | 图形界面（需 optional 依赖） |

## 路径与断点续传

- 会话：`%USERPROFILE%\.easy-pika-cli\session.json`（或 `SESSION_PATH`）
- 断点目录：`{保存目录}/{文件名}.parts/`
- 默认下载目录：`./downloads`（`DOWNLOAD_DIR`）

## Windows 打包

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1
```

## 注意事项

1. **账号安全**：勿将 `session.json` 或 `.env` 提交到公开仓库。  
2. **离线下载**：受 PikPak 账号配额与任务限制；失败可用 `offline list --phase error` 查看。  
3. **清理策略**：默认 `relay` 完成后永久删除网盘文件；`--trash-only` 仅移入回收站。  
4. **Telegram**：生产环境务必设置 `TELEGRAM_ALLOWED_USERS` 限制可用人。  
5. **AI API**：务必设置强 `AI_API_KEY`；无密钥时服务不鉴权（仅限本地调试）。

## 许可证

见 [LICENSE](LICENSE)。
