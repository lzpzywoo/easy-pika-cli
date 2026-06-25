# easy-pika-cli

PikPak cloud download tool with CLI, relay (magnet → PikPak → local), Telegram bot, Aria2, and AI HTTP API.

**中文文档:** [README.zh-CN.md](README.zh-CN.md)

## Quick start (CLI only)

```bash
pip install -r requirements.txt
python main.py login -u YOUR_EMAIL -p YOUR_PASSWORD
python main.py ls /
python main.py download FILE_ID -o ./downloads
```

Optional GUI: `pip install -r requirements-gui.txt` then `python main.py gui`

## Relay (use PikPak as transit)

Full pipeline — magnet → PikPak offline → wait → download → cleanup cloud:

```bash
python main.py relay run "magnet:?xt=urn:btih:..." -o ./downloads
```

Step by step:

```bash
python main.py relay upload "magnet:?xt=..."
python main.py offline list
python main.py offline wait TASK_ID FILE_ID
python main.py relay download FILE_ID -o ./downloads --cleanup
python main.py relay cleanup FILE_ID --task-ids TASK_ID
```

## Aria2 backend

```bash
# Push PikPak CDN URL to Aria2 RPC
python main.py download FILE_ID -o ./downloads --backend aria2 --aria2-rpc http://127.0.0.1:6800/jsonrpc
python main.py relay run "magnet:..." --backend aria2
```

Environment: `DOWNLOAD_BACKEND=aria2`, `ARIA2_RPC_URL`, `ARIA2_RPC_SECRET`

## Telegram bot

```bash
pip install -r requirements-full.txt
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_ALLOWED_USERS=12345678   # optional
python main.py telegram
```

Send a magnet link in chat; the bot runs the full relay pipeline.

Optional LLM parsing: set `OPENAI_API_KEY` (OpenAI-compatible API).

## AI / automation API

```bash
export AI_API_KEY=your-secret
python main.py ai serve --port 8765
```

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/v1/tools` | GET | List tools |
| `/v1/relay` | POST | Full relay (`magnet`, `upload`, `wait`, `download`, `cleanup`) |
| `/v1/offline/add` | POST | Submit magnet |
| `/v1/offline/wait` | POST | Wait for task |
| `/v1/offline/list` | GET | List offline tasks |
| `/v1/download` | POST | Download by `file_id` |
| `/v1/cleanup` | POST | Delete cloud files |
| `/v1/parse` | POST | Extract links from text (`use_llm`: optional) |
| `/v1/quota` | GET | Storage quota |

Auth: `Authorization: Bearer <AI_API_KEY>` or header `X-API-Key`.

Example:

```bash
curl -s -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{"magnet":"magnet:?xt=..."}' \
  http://127.0.0.1:8765/v1/relay
```

## Docker

```bash
cp .env.example .env
# edit .env

docker compose build

# Login once (session persisted in volume)
docker compose run --rm easy-pika-cli login -u USER -p PASS --session /data/session/session.json

# Relay
docker compose run --rm easy-pika-cli relay run "magnet:..." -o /data/downloads --session /data/session/session.json

# Telegram bot (profile)
docker compose --profile telegram up -d

# AI API (profile)
docker compose --profile ai up -d

# Aria2 sidecar
docker compose --profile aria2 up -d
```

Volumes: `session`, `downloads`

## CLI reference

| Command | Description |
|---------|-------------|
| `login` | Login and save session |
| `ls` | List files |
| `download` | Download file(s) (`--backend native\|aria2`) |
| `quota` | Storage usage |
| `offline add/list/wait` | Magnet upload & task management |
| `relay run/upload/download/cleanup` | Transit pipeline (combined or separate) |
| `telegram` | Run Telegram bot |
| `ai serve` | HTTP API for agents |
| `gui` | Desktop GUI (optional) |

## Paths

- Session: `~/.easy-pika-cli/session.json` (or `SESSION_PATH`)
- Resume parts: `{output}/{filename}.parts/`
- Default downloads: `./downloads` (or `DOWNLOAD_DIR`)

## Windows release

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1
```

## License

See [LICENSE](LICENSE).
