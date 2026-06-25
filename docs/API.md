# HTTP API

REST endpoints for PikPak offline download, local sync, and cloud file management.

**[中文](API.zh-CN.md)**

## Start the server

```bash
pip install -r requirements-full.txt
python main.py login -u USER -p PASS

export AI_API_KEY=your-strong-secret
python main.py ai serve --host 0.0.0.0 --port 8765
```

Interactive docs after startup:

- Swagger UI: `http://127.0.0.1:8765/docs`
- OpenAPI JSON: `http://127.0.0.1:8765/openapi.json`

Docker:

```bash
docker compose --profile ai up -d
```

## Authentication

When `AI_API_KEY` is set, all endpoints except `/health` require auth.

Either:

```http
Authorization: Bearer <AI_API_KEY>
```

```http
X-API-Key: <AI_API_KEY>
```

If `AI_API_KEY` is unset, no auth is enforced (local debugging only — always set in production).

## General

| Item | Description |
|------|-------------|
| Base URL | `http://<host>:<port>`, default port `8765` |
| Content-Type | `application/json` |
| Session | `~/.easy-pika-cli/session.json` or `SESSION_PATH`; run CLI `login` first |
| Download dir | `DOWNLOAD_DIR`, default `./downloads` |
| Errors | HTTP 4xx/5xx, body `{"detail": "..."}` |

## Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | No | Health check |
| GET | `/v1/tools` | Yes | List tool names |
| GET | `/v1/models` | Yes | OpenAI-compatible model list |
| GET | `/v1/quota` | Yes | Storage quota |
| GET | `/v1/offline/list` | Yes | Offline tasks |
| POST | `/v1/relay` | Yes | Magnet relay (step toggles) |
| POST | `/v1/offline/add` | Yes | Submit offline download |
| POST | `/v1/offline/wait` | Yes | Wait for task completion |
| POST | `/v1/download` | Yes | Download by `file_id` |
| POST | `/v1/cleanup` | Yes | Delete cloud files/tasks |
| POST | `/v1/parse` | Yes | Extract links from text |

---

## GET /health

Liveness probe for Docker / load balancers.

**Response 200**

```json
{"status": "ok"}
```

---

## GET /v1/tools

Returns registered tool names.

**Response 200**

```json
{
  "tools": [
    {"name": "relay_magnet", "description": "Full relay: magnet → PikPak → download → cleanup"},
    {"name": "offline_add", "description": "Submit magnet to PikPak offline download"},
    {"name": "offline_list", "description": "List offline tasks"},
    {"name": "parse_links", "description": "Extract magnet/torrent URLs from text"},
    {"name": "quota", "description": "Get PikPak storage quota"}
  ]
}
```

---

## GET /v1/models

OpenAI-compatible `/v1/models` placeholder response.

**Response 200**

```json
{
  "object": "list",
  "data": [{"id": "easy-pika-cli", "object": "model", "owned_by": "easy-pika-cli"}]
}
```

---

## GET /v1/quota

PikPak storage quota (passthrough from `pikpakapi`).

**Response 200** — example:

```json
{
  "quota": {
    "limit": "1099511627776",
    "usage": "1234567890",
    "usage_in_trash": "0"
  }
}
```

---

## GET /v1/offline/list

List offline tasks (pending, running, complete, error).

**Response 200**

```json
{
  "tasks": [
    {
      "task_id": "abc123",
      "file_id": "xyz789",
      "name": "example.mkv",
      "phase": "PHASE_TYPE_COMPLETE"
    }
  ]
}
```

Common `phase` values: `PHASE_TYPE_PENDING`, `PHASE_TYPE_RUNNING`, `PHASE_TYPE_COMPLETE`, `PHASE_TYPE_ERROR`

---

## POST /v1/relay

Main relay endpoint: upload → wait → download → cleanup (each step optional via body flags).

**Request body**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `magnet` | string | — | Magnet or `.torrent` URL (or use `url`) |
| `url` | string | — | Same as `magnet` |
| `text` | string | — | Extract first link from text |
| `upload` | bool | `true` | Submit PikPak offline job |
| `wait` | bool | `true` | Wait for cloud completion |
| `download` | bool | `true` | Download to `DOWNLOAD_DIR` |
| `cleanup` | bool | `RELAY_CLEANUP_CLOUD` | Delete cloud files after download |
| `backend` | string | `DOWNLOAD_BACKEND` | `native` or `aria2` |
| `threads` | int | `12` | Native downloader threads |
| `timeout` | float | `7200` | Offline wait timeout (seconds) |
| `poll_interval` | float | `10` | Poll interval (seconds) |

**Example — full relay**

```bash
curl -X POST http://127.0.0.1:8765/v1/relay \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{"magnet":"magnet:?xt=urn:btih:..."}'
```

**Example — upload only**

```json
{
  "magnet": "magnet:?xt=...",
  "upload": true,
  "wait": false,
  "download": false,
  "cleanup": false
}
```

**Response 200**

```json
{
  "task_id": "offline_task_id",
  "file_ids": ["file_id_1", "file_id_2"],
  "local_paths": ["/path/to/downloads/file.mkv"],
  "cleaned": true
}
```

**Errors**

| Status | Cause |
|--------|-------|
| 400 | Missing `magnet` / `url` / parseable `text` |
| 401 | Invalid API key |
| 500 | PikPak offline/download failure |

---

## POST /v1/offline/add

Submit magnet to PikPak offline download only.

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `url` / `magnet` | string | Yes | Magnet or HTTP `.torrent` URL |
| `parent_id` | string | No | Target folder ID |

**Response 200** — PikPak API raw response; usually includes `id` (file_id) and `task`.

---

## POST /v1/offline/wait

Block until offline task completes.

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `task_id` | string | Yes | Offline task ID |
| `file_id` | string | Yes | File ID |
| `timeout` | float | No | Default `7200` |
| `poll_interval` | float | No | Default `10` |

**Response 200**

```json
{
  "task_id": "abc",
  "file_id": "xyz",
  "phase": "PHASE_TYPE_COMPLETE",
  "name": "file.mkv"
}
```

---

## POST /v1/download

Download by `file_id` from PikPak CDN (folders expand to multiple files).

**Request body**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `file_id` | string | Required | Cloud file or folder ID |
| `cleanup` | bool | `false` | Delete cloud files after download |
| `backend` | string | env default | `native` or `aria2` |

**Response 200**

```json
{
  "file_ids": ["id1", "id2"],
  "local_paths": ["/downloads/a.mkv", "/downloads/b.mkv"],
  "cleaned": false
}
```

---

## POST /v1/cleanup

Delete cloud files and offline tasks.

**Request body**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `file_ids` | string[] | `[]` | File/folder IDs to delete |
| `task_ids` | string[] | `[]` | Offline task IDs to delete |
| `delete_forever` | bool | `true` | `true` = permanent; `false` = trash |

**Response 200**

```json
{"ok": true}
```

---

## POST /v1/parse

Extract magnet and `.torrent` URLs from text.

**Request body**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `text` | string | Required | Input text |
| `use_llm` | bool | `false` | Use LLM when `OPENAI_API_KEY` is set |

**Response 200**

```json
{
  "links": [
    "magnet:?xt=urn:btih:...",
    "https://example.com/file.torrent"
  ]
}
```

---

## Examples

**Relay (one request)**

```
POST /v1/relay  {"magnet":"..."}
```

**Step-by-step (same as CLI)**

```
POST /v1/offline/add   → task_id, file_id
POST /v1/offline/wait
POST /v1/download
POST /v1/cleanup
```

## Environment variables

| Variable | Description |
|----------|-------------|
| `AI_API_KEY` | API auth secret |
| `AI_API_HOST` | Bind address, default `0.0.0.0` |
| `AI_API_PORT` | Port, default `8765` |
| `SESSION_PATH` | PikPak session file |
| `DOWNLOAD_DIR` | Local download directory |
| `DOWNLOAD_BACKEND` | `native` / `aria2` |
| `ARIA2_RPC_URL` | Aria2 JSON-RPC URL |
| `ARIA2_RPC_SECRET` | Aria2 RPC secret |
| `RELAY_CLEANUP_CLOUD` | Default cleanup after relay |
| `RELAY_TIMEOUT` | Offline wait timeout (s) |
| `RELAY_POLL_INTERVAL` | Offline poll interval (s) |
| `OPENAI_API_KEY` | LLM backend for `/v1/parse` |

See [.env.example](../.env.example) for the full list.
