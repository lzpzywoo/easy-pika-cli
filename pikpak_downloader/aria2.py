"""Aria2 JSON-RPC client."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

import httpx


class Aria2Error(RuntimeError):
    pass


class Aria2Client:
    def __init__(self, rpc_url: str, secret: str = "") -> None:
        self.rpc_url = rpc_url.rstrip("/")
        if not self.rpc_url.endswith("/jsonrpc"):
            self.rpc_url = f"{self.rpc_url}/jsonrpc"
        self.secret = secret
        self._id = 0

    def _next_id(self) -> str:
        self._id += 1
        return f"easy-pika-{self._id}-{uuid.uuid4().hex[:8]}"

    async def call(self, method: str, params: Optional[list] = None) -> Any:
        payload: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": self._wrap_params(params or []),
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(self.rpc_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        if "error" in data:
            err = data["error"]
            raise Aria2Error(f"Aria2 {method}: {err.get('message', err)}")
        return data.get("result")

    def _wrap_params(self, params: list) -> list:
        if self.secret:
            return [f"token:{self.secret}", *params]
        return params

    async def add_uri(
        self,
        url: str,
        *,
        dir_path: Optional[str] = None,
        out: Optional[str] = None,
    ) -> str:
        options: Dict[str, str] = {}
        if dir_path:
            options["dir"] = dir_path
        if out:
            options["out"] = out
        params: list = [[url]]
        if options:
            params.append(options)
        gid = await self.call("aria2.addUri", params)
        if not isinstance(gid, str):
            raise Aria2Error(f"Unexpected addUri result: {gid!r}")
        return gid

    async def tell_status(self, gid: str) -> dict:
        result = await self.call(
            "aria2.tellStatus",
            [gid, ["gid", "status", "totalLength", "completedLength", "downloadSpeed", "files"]],
        )
        return result if isinstance(result, dict) else {}

    async def wait_complete(self, gid: str, poll_interval: float = 2.0) -> dict:
        import asyncio

        while True:
            status = await self.tell_status(gid)
            state = status.get("status")
            if state == "complete":
                return status
            if state in ("error", "removed"):
                raise Aria2Error(f"Aria2 download {gid} ended with status={state}")
            await asyncio.sleep(poll_interval)
