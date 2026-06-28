from __future__ import annotations

import httpx
import pytest

from pikpak_downloader.api_helpers import get_httpx_client_args, resolve_proxy


def test_resolve_proxy_prefers_explicit() -> None:
    assert resolve_proxy("http://127.0.0.1:10809") == "http://127.0.0.1:10809"


def test_resolve_proxy_reads_pikpak_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIKPAK_PROXY", "http://127.0.0.1:7890")
    assert resolve_proxy() == "http://127.0.0.1:7890"


def test_resolve_proxy_reads_https_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PIKPAK_PROXY", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:10809")
    assert resolve_proxy() == "http://127.0.0.1:10809"


def test_get_httpx_client_args_sets_proxy() -> None:
    args = get_httpx_client_args("http://127.0.0.1:10809")
    assert args["proxy"] == "http://127.0.0.1:10809"
    assert "trust_env" not in args


def test_get_httpx_client_args_trust_env_without_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in ("PIKPAK_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(key, raising=False)
    args = get_httpx_client_args()
    assert "proxy" not in args
    assert args["trust_env"] is True


def test_get_httpx_client_args_builds_async_client() -> None:
    client = httpx.AsyncClient(**get_httpx_client_args("http://127.0.0.1:10809"))
    try:
        assert client._mounts is not None
    finally:
        import asyncio

        asyncio.run(client.aclose())
