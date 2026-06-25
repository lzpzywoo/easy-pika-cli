"""HTTP API for AI agents / automation (OpenAI-style tool endpoints)."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from .aria2 import Aria2Client
from .config import AppConfig
from .magnets import extract_links
from .offline_service import add_offline, cleanup_cloud, list_offline_tasks, wait_offline_complete
from .relay import RelayOptions, relay_download_only, relay_magnet
from .session import load_session_async
from .token_helpers import TokenManager

logger = logging.getLogger(__name__)

AwaitableHandler = Callable[..., Any]


def _json_response(data: Any, status: int = 200):
    from fastapi import Response

    return Response(
        content=json.dumps(data, ensure_ascii=False),
        status_code=status,
        media_type="application/json",
    )


def create_app(config: Optional[AppConfig] = None):
    try:
        from fastapi import Depends, FastAPI, Header, HTTPException, Request
    except ImportError as exc:
        raise ImportError(
            "AI API server requires fastapi and uvicorn. "
            "Install: pip install fastapi uvicorn"
        ) from exc

    cfg = config or AppConfig.from_env()
    app = FastAPI(title="easy-pika-cli API", version="0.4.0")

    _client = None
    _token_mgr: Optional[TokenManager] = None

    async def get_session():
        nonlocal _client, _token_mgr
        if _client is None:
            _client = await load_session_async(cfg.session_path)
            _token_mgr = TokenManager(_client, cfg.session_path)
        assert _token_mgr is not None
        await _token_mgr.refresh()
        return _client, _token_mgr

    def verify_key(authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None)):
        if not cfg.ai_api_key:
            return
        token = ""
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        elif x_api_key:
            token = x_api_key.strip()
        if token != cfg.ai_api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/v1/tools", dependencies=[Depends(verify_key)])
    async def list_tools():
        return {
            "tools": [
                {"name": "relay_magnet", "description": "Full relay: magnet → PikPak → download → cleanup"},
                {"name": "offline_add", "description": "Submit magnet to PikPak offline download"},
                {"name": "offline_list", "description": "List offline tasks"},
                {"name": "parse_links", "description": "Extract magnet/torrent URLs from text"},
                {"name": "quota", "description": "Get PikPak storage quota"},
            ]
        }

    @app.post("/v1/relay", dependencies=[Depends(verify_key)])
    async def api_relay(request: Request):
        body = await request.json()
        magnet = body.get("magnet") or body.get("url") or ""
        if not magnet and body.get("text"):
            links = extract_links(body["text"])
            magnet = links[0] if links else ""
        if not magnet:
            raise HTTPException(400, "magnet or text required")

        client, token_mgr = await get_session()
        aria2 = None
        if cfg.download_backend == "aria2":
            aria2 = Aria2Client(cfg.aria2_rpc_url, cfg.aria2_rpc_secret)

        opts = RelayOptions(
            upload=body.get("upload", True),
            wait=body.get("wait", True),
            download=body.get("download", True),
            cleanup=body.get("cleanup", cfg.relay_cleanup_cloud),
            dest_dir=cfg.download_dir,
            backend=body.get("backend", cfg.download_backend),
            aria2=aria2,
            threads=int(body.get("threads", 12)),
            timeout=float(body.get("timeout", cfg.relay_timeout)),
            poll_interval=float(body.get("poll_interval", cfg.relay_poll_interval)),
        )
        result = await relay_magnet(client, magnet, token_mgr, opts)
        return {
            "task_id": result.task_id,
            "file_ids": result.file_ids,
            "local_paths": [str(p) for p in result.local_paths],
            "cleaned": result.cleaned,
        }

    @app.post("/v1/offline/add", dependencies=[Depends(verify_key)])
    async def api_offline_add(request: Request):
        body = await request.json()
        url = body.get("url") or body.get("magnet")
        if not url:
            raise HTTPException(400, "url required")
        client, _ = await get_session()
        created = await add_offline(client, url, parent_id=body.get("parent_id"))
        return created

    @app.post("/v1/offline/wait", dependencies=[Depends(verify_key)])
    async def api_offline_wait(request: Request):
        body = await request.json()
        task_id = body.get("task_id")
        file_id = body.get("file_id")
        if not task_id or not file_id:
            raise HTTPException(400, "task_id and file_id required")
        client, _ = await get_session()
        task = await wait_offline_complete(
            client,
            task_id,
            file_id,
            timeout=float(body.get("timeout", cfg.relay_timeout)),
            poll_interval=float(body.get("poll_interval", cfg.relay_poll_interval)),
        )
        return {"task_id": task.task_id, "file_id": task.file_id, "phase": task.phase, "name": task.name}

    @app.get("/v1/offline/list", dependencies=[Depends(verify_key)])
    async def api_offline_list():
        client, _ = await get_session()
        tasks = await list_offline_tasks(client)
        return {
            "tasks": [
                {"task_id": t.task_id, "file_id": t.file_id, "name": t.name, "phase": t.phase}
                for t in tasks
            ]
        }

    @app.post("/v1/download", dependencies=[Depends(verify_key)])
    async def api_download(request: Request):
        body = await request.json()
        file_id = body.get("file_id")
        if not file_id:
            raise HTTPException(400, "file_id required")
        client, token_mgr = await get_session()
        aria2 = None
        if cfg.download_backend == "aria2":
            aria2 = Aria2Client(cfg.aria2_rpc_url, cfg.aria2_rpc_secret)
        opts = RelayOptions(
            upload=False,
            wait=False,
            download=True,
            cleanup=body.get("cleanup", False),
            dest_dir=cfg.download_dir,
            backend=body.get("backend", cfg.download_backend),
            aria2=aria2,
        )
        result = await relay_download_only(client, file_id, token_mgr, opts)
        return {
            "file_ids": result.file_ids,
            "local_paths": [str(p) for p in result.local_paths],
            "cleaned": result.cleaned,
        }

    @app.post("/v1/cleanup", dependencies=[Depends(verify_key)])
    async def api_cleanup(request: Request):
        body = await request.json()
        file_ids = body.get("file_ids") or []
        task_ids = body.get("task_ids") or []
        client, _ = await get_session()
        await cleanup_cloud(
            client,
            file_ids,
            delete_forever=body.get("delete_forever", True),
            task_ids=task_ids,
        )
        return {"ok": True}

    @app.post("/v1/parse", dependencies=[Depends(verify_key)])
    async def api_parse(request: Request):
        body = await request.json()
        text = body.get("text", "")
        use_llm = body.get("use_llm", False)
        if use_llm and cfg.openai_api_key:
            from .ai_parse import parse_message_with_llm

            links = await parse_message_with_llm(
                text,
                api_key=cfg.openai_api_key,
                base_url=cfg.openai_base_url,
                model=cfg.openai_model,
            )
        else:
            links = extract_links(text)
        return {"links": links}

    @app.get("/v1/quota", dependencies=[Depends(verify_key)])
    async def api_quota():
        client, _ = await get_session()
        info = await client.get_quota_info()
        return info

    @app.get("/v1/models", dependencies=[Depends(verify_key)])
    async def models():
        return {
            "object": "list",
            "data": [{"id": "easy-pika-cli", "object": "model", "owned_by": "easy-pika-cli"}],
        }

    return app


def run_ai_server(config: Optional[AppConfig] = None) -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise ImportError("pip install uvicorn fastapi") from exc

    cfg = config or AppConfig.from_env()
    app = create_app(cfg)
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host=cfg.ai_api_host, port=cfg.ai_api_port, log_level="info")
