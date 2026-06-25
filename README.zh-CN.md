# easy-pika-cli

PikPak 网盘命令行工具：登录、浏览、多线程下载、磁链离线下载同步（`relay`）。

**[English](README.md)** · [HTTP API](docs/API.zh-CN.md)

## 功能

| 组件 | 说明 |
|------|------|
| CLI | 核心：`login`、`ls`、`download`、`quota`、`offline`、`relay` |
| GUI | 可选桌面界面：网盘浏览、下载队列（不含 relay） |
| Telegram | 可选：接收磁链并执行 relay |
| HTTP API | 可选：REST 接口，见 [docs/API.zh-CN.md](docs/API.zh-CN.md) |
| Docker | `docker-compose.yml`，按 profile 启停服务 |

内置下载支持多连接分块、断点续传（`{文件名}.parts/`）；可选用 Aria2 作为下载后端（`--backend aria2`）。

## 要求

- Python 3.10+
- PikPak 账号

## 安装

```bash
pip install -r requirements.txt              # CLI
pip install -r requirements-gui.txt          # + GUI
pip install -r requirements-full.txt         # + Telegram + HTTP API
```

## 快速开始

```bash
python main.py login -u 邮箱 -p 密码
python main.py ls /
python main.py download <file_id> -o ./downloads
```

磁链离线下载并同步到本地：

```bash
python main.py relay run "magnet:?xt=..." -o ./downloads
```

## CLI 参考

全局选项：`--session <path>`（默认 `~/.easy-pika-cli/session.json`）

### `login`

```bash
python main.py login -u USER -p PASS
```

### `ls` / `download` / `quota`

```bash
python main.py ls [/path] [--limit 100]
python main.py download <file_id|/path> ... -o DIR [-t threads] [-c concurrent] [-n filename]
python main.py quota
```

`download` 支持 `--backend native|aria2`，以及 `--aria2-rpc`、`--aria2-secret`。

### `offline`

```bash
python main.py offline add <magnet|torrent_url> [--parent-id ID] [--name NAME]
python main.py offline list [--phase all|running|complete|error]
python main.py offline wait <task_id> <file_id> [--timeout 7200] [--interval 10]
```

### `relay`

```bash
python main.py relay run <magnet> ... -o DIR [--no-cleanup] [--trash-only]
python main.py relay upload <magnet> ...
python main.py relay download <file_id> ... -o DIR [--cleanup]
python main.py relay cleanup <file_id> ... [--task-ids TASK_ID ...] [--trash-only]
```

### `telegram` / `ai serve` / `gui`

```bash
python main.py telegram [--token TOKEN]     # 需 TELEGRAM_BOT_TOKEN
python main.py ai serve [--host HOST] [--port PORT] [--api-key KEY]
python main.py gui                          # 或 python gui.py
```

## 配置

环境变量见 [.env.example](.env.example)。常用项：

| 变量 | 默认 | 说明 |
|------|------|------|
| `SESSION_PATH` | `~/.easy-pika-cli/session.json` | 会话文件 |
| `DOWNLOAD_DIR` | `./downloads` | 下载目录 |
| `DOWNLOAD_BACKEND` | `native` | `native` 或 `aria2` |
| `RELAY_CLEANUP_CLOUD` | `true` | relay 完成后删除网盘文件 |
| `TELEGRAM_BOT_TOKEN` | — | Telegram 机器人 |
| `TELEGRAM_ALLOWED_USERS` | — | 允许的用户 ID（逗号分隔） |
| `AI_API_KEY` | — | HTTP API 鉴权；未设置时不校验 |

## Docker

```bash
cp .env.example .env
docker compose build
docker compose run --rm easy-pika-cli login -u USER -p PASS --session /data/session/session.json
docker compose --profile telegram up -d
docker compose --profile ai up -d
docker compose --profile aria2 up -d
```

## Windows 构建

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1
```

输出：`dist/easy-pika-cli-v<version>-windows-x64/`

## 注意

- 勿提交或泄露 `session.json`、`.env`、`AI_API_KEY`
- 离线下载受 PikPak 账号配额限制
- 生产环境请设置 `TELEGRAM_ALLOWED_USERS` 与 `AI_API_KEY`

## License

[LICENSE](LICENSE)

## 测试

```bash
pip install -r requirements-dev.txt
pytest
```
