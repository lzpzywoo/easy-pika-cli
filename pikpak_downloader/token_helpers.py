"""Token refresh and API call retry helpers."""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from pikpakapi import PikPakApi
from pikpakapi.PikpakException import PikpakException, PikpakRetryException

from .api_helpers import retry_api_call
from .session import save_session


class TokenManager:
    """Thread-safe token refresh for long-running downloads."""

    REFRESH_INTERVAL = 3000  # seconds (~50 min, token TTL ~7200s)

    def __init__(self, client: PikPakApi, session_path: Optional[str] = None) -> None:
        self.client = client
        self.session_path = session_path
        self._lock = asyncio.Lock()
        self._last_refresh = 0.0

    async def refresh(self, force: bool = False) -> None:
        async with self._lock:
            now = time.time()
            if not force and now - self._last_refresh < self.REFRESH_INTERVAL:
                return
            await retry_api_call(
                self.client.refresh_access_token,
                max_retries=6,
                label="Token 刷新",
            )
            if self.session_path:
                save_session(self.client, self.session_path)
            self._last_refresh = now

    async def ensure_fresh(self) -> None:
        await self.refresh(force=False)

    def get_headers(self) -> dict:
        return self.client.get_headers()


async def get_download_url_with_retry(
    client: PikPakApi,
    file_id: str,
    token_mgr: TokenManager,
    *,
    max_retries: int = 8,
    cancel_event: asyncio.Event | None = None,
) -> dict:
    """Fetch download URL; refresh token and retry on auth / network failures."""
    last_error: Exception | None = None

    for attempt in range(max_retries):
        if cancel_event and cancel_event.is_set():
            raise asyncio.CancelledError("任务已取消")

        await token_mgr.refresh(force=(attempt > 0))
        try:
            return await retry_api_call(
                lambda: client.get_download_url(file_id),
                max_retries=4,
                label="获取下载链接",
                cancel_event=cancel_event,
            )
        except asyncio.CancelledError:
            raise
        except PikpakRetryException:
            await token_mgr.refresh(force=True)
            last_error = PikpakRetryException("Token refreshed, retry")
        except PikpakException as exc:
            msg = str(exc).lower()
            if any(k in msg for k in ("token", "auth", "expired", "unauthorized")):
                await token_mgr.refresh(force=True)
                last_error = exc
                await asyncio.sleep(min(2**attempt, 8))
                continue
            if "max retries" in msg or "connection" in msg:
                last_error = exc
                await asyncio.sleep(min(2.0 * (2**attempt), 30))
                continue
            raise
        except Exception as exc:
            last_error = exc
            if attempt < max_retries - 1:
                await asyncio.sleep(min(2.0 * (2**attempt), 30))
                continue
            raise

    raise RuntimeError(f"获取下载链接失败（已重试 {max_retries} 次）: {last_error}")
