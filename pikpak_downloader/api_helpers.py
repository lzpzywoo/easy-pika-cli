"""Network resilience helpers for PikPak API client."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine, TypeVar

import httpx
from pikpakapi import PikPakApi
from pikpakapi.PikpakException import PikpakException

T = TypeVar("T")

_RETRYABLE_KEYWORDS = (
    "connection",
    "connect",
    "timeout",
    "network",
    "max retries",
    "all connection",
    "temporary failure",
    "reset by peer",
    "disconnected",
)


def get_httpx_client_args() -> dict[str, Any]:
    return {
        "timeout": httpx.Timeout(connect=60.0, read=120.0, write=30.0, pool=60.0),
        "limits": httpx.Limits(max_connections=24, max_keepalive_connections=12),
        "transport": httpx.AsyncHTTPTransport(retries=3),
    }


def get_client_kwargs() -> dict[str, Any]:
    return {
        "httpx_client_args": get_httpx_client_args(),
        "request_max_retries": 8,
        "request_initial_backoff": 2.0,
    }


async def apply_client_defaults(client: PikPakApi) -> None:
    """Re-apply robust network settings after loading session from disk."""
    client.max_retries = 8
    client.initial_backoff = 2.0
    try:
        await client.httpx_client.aclose()
    except Exception:
        pass
    client.httpx_client = httpx.AsyncClient(**get_httpx_client_args())


def is_retryable_error(exc: BaseException) -> bool:
    if isinstance(
        exc,
        (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.ReadError,
            httpx.WriteError,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
            ConnectionError,
            OSError,
        ),
    ):
        return True
    if isinstance(exc, PikpakException):
        msg = str(exc).lower()
        return any(k in msg for k in _RETRYABLE_KEYWORDS)
    msg = str(exc).lower()
    return any(k in msg for k in _RETRYABLE_KEYWORDS)


async def retry_api_call(
    fn: Callable[[], Coroutine[Any, Any, T]],
    *,
    max_retries: int = 8,
    label: str = "API",
    cancel_event: asyncio.Event | None = None,
) -> T:
    """Retry transient network / connection failures with exponential backoff."""
    last_error: BaseException | None = None

    for attempt in range(max_retries):
        if cancel_event and cancel_event.is_set():
            raise asyncio.CancelledError("任务已取消")

        try:
            return await fn()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_error = exc
            if not is_retryable_error(exc):
                raise
            if attempt >= max_retries - 1:
                break
            delay = min(2.0 * (2**attempt), 30.0)
            await asyncio.sleep(delay)

    raise RuntimeError(
        f"{label} 网络失败（已重试 {max_retries} 次）: {last_error}"
    ) from last_error
