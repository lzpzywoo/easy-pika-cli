# HTTP API

easy-pika-cli HTTP API：PikPak 离线下载、本地同步、网盘管理。

**[English](API.md)**

## 启动服务

```bash
pip install -r requirements-full.txt
python main.py login -u 账号 -p 密码

export AI_API_KEY=请设置强密钥
python main.py ai serve --host 0.0.0.0 --port 8765
```

启动后访问交互式文档：

- Swagger UI：`http://127.0.0.1:8765/docs`
- OpenAPI JSON：`http://127.0.0.1:8765/openapi.json`

Docker：

```bash
docker compose --profile ai up -d
```

## 鉴权

当设置了环境变量 `AI_API_KEY` 时，除 `/health` 外所有接口均需鉴权。

任选一种方式：

```http
Authorization: Bearer <AI_API_KEY>
```

```http
X-API-Key: <AI_API_KEY>
```

未设置 `AI_API_KEY` 时不校验（仅限本地调试，生产环境务必配置）。

## 通用说明

| 项 | 说明 |
|----|------|
| Base URL | `http://<host>:<port>`，默认 `8765` |
| Content-Type | `application/json` |
| 会话 | 使用 `~/.easy-pika-cli/session.json` 或 `SESSION_PATH`；需先 CLI `login` |
| 下载目录 | 由 `DOWNLOAD_DIR` 控制，默认 `./downloads` |
| 错误 | HTTP 4xx/5xx，body 为 `{"detail": "..."}` |

## 接口一览

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | `/health` | 否 | 健康检查 |
| GET | `/v1/tools` | 是 | 列出可用工具名称 |
| GET | `/v1/models` | 是 | OpenAI 兼容模型列表 |
| GET | `/v1/quota` | 是 | 网盘空间配额 |
| GET | `/v1/offline/list` | 是 | 离线任务列表 |
| POST | `/v1/relay` | 是 | Relay 流程（可拆分步骤） |
| POST | `/v1/offline/add` | 是 | 提交离线下载 |
| POST | `/v1/offline/wait` | 是 | 等待离线任务完成 |
| POST | `/v1/download` | 是 | 按 file_id 下载到本地 |
| POST | `/v1/cleanup` | 是 | 清理网盘文件/离线任务 |
| POST | `/v1/parse` | 是 | 从文本提取磁链 |

---

## GET /health

健康检查，用于 Docker / 负载均衡探活。

**响应 200**

```json
{"status": "ok"}
```

---

## GET /v1/tools

返回已注册的工具名称列表。

**响应 200**

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

OpenAI 兼容的 `/v1/models` 占位响应。

**响应 200**

```json
{
  "object": "list",
  "data": [{"id": "easy-pika-cli", "object": "model", "owned_by": "easy-pika-cli"}]
}
```

---

## GET /v1/quota

获取 PikPak 账号存储配额（透传 `pikpakapi` 返回值）。

**响应 200** — 示例字段：

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

列出当前离线任务（运行中、待处理、已完成、失败）。

**响应 200**

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

`phase` 常见值：`PHASE_TYPE_PENDING`、`PHASE_TYPE_RUNNING`、`PHASE_TYPE_COMPLETE`、`PHASE_TYPE_ERROR`

---

## POST /v1/relay

Relay 主接口：离线上传 → 等待完成 → 本地下载 → 网盘清理（各步骤可通过 body 开关）。

**请求体**

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `magnet` | string | — | 磁链或 `.torrent` URL（与 `url` 二选一） |
| `url` | string | — | 同 `magnet` |
| `text` | string | — | 从文本中提取第一个链接 |
| `upload` | bool | `true` | 提交 PikPak 离线下载 |
| `wait` | bool | `true` | 等待云端完成 |
| `download` | bool | `true` | 下载到 `DOWNLOAD_DIR` |
| `cleanup` | bool | `RELAY_CLEANUP_CLOUD` | 下载后删除网盘文件 |
| `backend` | string | `DOWNLOAD_BACKEND` | `native` 或 `aria2` |
| `threads` | int | `12` | 内置下载线程数 |
| `timeout` | float | `7200` | 等待离线超时（秒） |
| `poll_interval` | float | `10` | 轮询间隔（秒） |

**示例 — 完整 Relay 流程**

```bash
curl -X POST http://127.0.0.1:8765/v1/relay \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{"magnet":"magnet:?xt=urn:btih:..."}'
```

**示例 — 仅上传（不等待、不下载）**

```json
{
  "magnet": "magnet:?xt=...",
  "upload": true,
  "wait": false,
  "download": false,
  "cleanup": false
}
```

**响应 200**

```json
{
  "task_id": "offline_task_id",
  "file_ids": ["file_id_1", "file_id_2"],
  "local_paths": ["/path/to/downloads/file.mkv"],
  "cleaned": true
}
```

**错误**

| 状态码 | 原因 |
|--------|------|
| 400 | 缺少 `magnet` / `url` / 可解析的 `text` |
| 401 | API Key 无效 |
| 500 | PikPak 离线/下载失败 |

---

## POST /v1/offline/add

仅提交磁链到 PikPak 离线下载，不等待、不下载。

**请求体**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `url` / `magnet` | string | 是 | 磁链或 HTTP `.torrent` 链接 |
| `parent_id` | string | 否 | 目标文件夹 ID |

**示例**

```bash
curl -X POST http://127.0.0.1:8765/v1/offline/add \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{"url":"magnet:?xt=urn:btih:..."}'
```

**响应 200** — PikPak API 原始返回，通常包含 `id`（file_id）及 `task` 信息。

---

## POST /v1/offline/wait

阻塞等待指定离线任务完成。

**请求体**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `task_id` | string | 是 | 离线任务 ID |
| `file_id` | string | 是 | 文件 ID |
| `timeout` | float | 否 | 超时秒数，默认 `7200` |
| `poll_interval` | float | 否 | 轮询间隔，默认 `10` |

**响应 200**

```json
{
  "task_id": "abc",
  "file_id": "xyz",
  "phase": "PHASE_TYPE_COMPLETE",
  "name": "file.mkv"
}
```

**错误** — 超时或任务失败时返回 500。

---

## POST /v1/download

按 `file_id` 从 PikPak CDN 下载到本地（支持文件夹展开为多文件）。

**请求体**

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `file_id` | string | 必填 | 网盘文件或文件夹 ID |
| `cleanup` | bool | `false` | 下载后清理网盘 |
| `backend` | string | 环境默认 | `native` 或 `aria2` |

**响应 200**

```json
{
  "file_ids": ["id1", "id2"],
  "local_paths": ["/downloads/a.mkv", "/downloads/b.mkv"],
  "cleaned": false
}
```

---

## POST /v1/cleanup

删除网盘文件与离线任务。

**请求体**

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `file_ids` | string[] | `[]` | 要删除的文件/文件夹 ID |
| `task_ids` | string[] | `[]` | 要删除的离线任务 ID |
| `delete_forever` | bool | `true` | `true` 永久删除；`false` 移入回收站 |

**响应 200**

```json
{"ok": true}
```

---

## POST /v1/parse

从任意文本中提取磁链与 `.torrent` URL。

**请求体**

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `text` | string | 必填 | 待解析文本 |
| `use_llm` | bool | `false` | 为 `true` 且配置了 `OPENAI_API_KEY` 时用 LLM 提取 |

**响应 200**

```json
{
  "links": [
    "magnet:?xt=urn:btih:...",
    "https://example.com/file.torrent"
  ]
}
```

---

## 示例

**Relay（一步）**

```
POST /v1/relay  {"magnet":"..."}
```

**分步（等同 CLI）**

```
POST /v1/offline/add   → task_id, file_id
POST /v1/offline/wait
POST /v1/download
POST /v1/cleanup
```

## 环境变量

| 变量 | 说明 |
|------|------|
| `AI_API_KEY` | API 鉴权密钥 |
| `AI_API_HOST` | 监听地址，默认 `0.0.0.0` |
| `AI_API_PORT` | 端口，默认 `8765` |
| `SESSION_PATH` | PikPak 会话文件 |
| `DOWNLOAD_DIR` | 本地下载目录 |
| `DOWNLOAD_BACKEND` | `native` / `aria2` |
| `ARIA2_RPC_URL` | Aria2 JSON-RPC 地址 |
| `ARIA2_RPC_SECRET` | Aria2 RPC 密钥 |
| `RELAY_CLEANUP_CLOUD` | Relay 完成后默认是否清理网盘 |
| `RELAY_TIMEOUT` | 离线等待超时（秒） |
| `RELAY_POLL_INTERVAL` | 离线轮询间隔（秒） |
| `OPENAI_API_KEY` | `/v1/parse` 的 LLM 后端 |

完整列表见 [.env.example](../.env.example)。
