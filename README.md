# easy-pika-cli

PikPak cloud CLI: sign in, browse, multi-thread download, magnet offline sync (`relay`).

**[中文](README.zh-CN.md)** · [HTTP API](docs/API.md)

## Features

| Component | Description |
|-----------|-------------|
| CLI | Core: `login`, `ls`, `download`, `quota`, `offline`, `relay` |
| GUI | Optional desktop UI: cloud browse, download queue (no relay) |
| Telegram | Optional: accept magnets and run relay |
| HTTP API | Optional REST endpoints — see [docs/API.md](docs/API.md) |
| Docker | `docker-compose.yml` with compose profiles |

Native download: multi-connection chunks, resume via `{filename}.parts/`. Optional Aria2 backend (`--backend aria2`).

## Requirements

- Python 3.10+
- PikPak account

## Install

```bash
pip install -r requirements.txt              # CLI
pip install -r requirements-gui.txt          # + GUI
pip install -r requirements-full.txt         # + Telegram + HTTP API
```

## Quick start

```bash
python main.py login -u USER -p PASS
python main.py ls /
python main.py download <file_id> -o ./downloads
```

Magnet offline download and local sync:

```bash
python main.py relay run "magnet:?xt=..." -o ./downloads
```

## CLI reference

Global: `--session <path>` (default `~/.easy-pika-cli/session.json`)

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

`download` accepts `--backend native|aria2`, `--aria2-rpc`, `--aria2-secret`.

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
python main.py telegram [--token TOKEN]     # requires TELEGRAM_BOT_TOKEN
python main.py ai serve [--host HOST] [--port PORT] [--api-key KEY]
python main.py gui                          # or python gui.py
```

## Configuration

See [.env.example](.env.example). Common variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `SESSION_PATH` | `~/.easy-pika-cli/session.json` | Session file |
| `DOWNLOAD_DIR` | `./downloads` | Download directory |
| `DOWNLOAD_BACKEND` | `native` | `native` or `aria2` |
| `RELAY_CLEANUP_CLOUD` | `true` | Delete cloud files after relay |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token |
| `TELEGRAM_ALLOWED_USERS` | — | Allowed user IDs (comma-separated) |
| `AI_API_KEY` | — | HTTP API auth; no check if unset |

## Docker

```bash
cp .env.example .env
docker compose build
docker compose run --rm easy-pika-cli login -u USER -p PASS --session /data/session/session.json
docker compose --profile telegram up -d
docker compose --profile ai up -d
docker compose --profile aria2 up -d
```

## Windows build

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1
```

Output: `dist/easy-pika-cli-v<version>-windows-x64/`

## Notes

- Do not commit or expose `session.json`, `.env`, or `AI_API_KEY`
- Offline tasks are limited by PikPak account quota
- Set `TELEGRAM_ALLOWED_USERS` and `AI_API_KEY` in production

## License

[LICENSE](LICENSE)

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```
