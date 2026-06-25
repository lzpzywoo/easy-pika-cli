from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pikpak_downloader.aria2 import Aria2Client, Aria2Error


def test_rpc_url_normalization() -> None:
    client = Aria2Client("http://127.0.0.1:6800")
    assert client.rpc_url == "http://127.0.0.1:6800/jsonrpc"


def test_wrap_params_with_secret() -> None:
    client = Aria2Client("http://127.0.0.1:6800/jsonrpc", secret="s3cr3t")
    assert client._wrap_params([["url"]]) == ["token:s3cr3t", ["url"]]


def test_wrap_params_without_secret() -> None:
    client = Aria2Client("http://127.0.0.1:6800/jsonrpc")
    assert client._wrap_params([["url"]]) == [["url"]]


@pytest.mark.asyncio
async def test_call_success() -> None:
    client = Aria2Client("http://127.0.0.1:6800/jsonrpc")
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"result": "gid-123"}

    mock_http = MagicMock()
    mock_http.post = AsyncMock(return_value=mock_resp)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)

    with patch("pikpak_downloader.aria2.httpx.AsyncClient", return_value=mock_http):
        result = await client.call("aria2.addUri", [["http://example.com/file"]])

    assert result == "gid-123"
    payload = mock_http.post.await_args.kwargs["json"]
    assert payload["method"] == "aria2.addUri"


@pytest.mark.asyncio
async def test_call_rpc_error() -> None:
    client = Aria2Client("http://127.0.0.1:6800/jsonrpc")
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"error": {"message": "unauthorized"}}

    mock_http = MagicMock()
    mock_http.post = AsyncMock(return_value=mock_resp)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)

    with patch("pikpak_downloader.aria2.httpx.AsyncClient", return_value=mock_http):
        with pytest.raises(Aria2Error, match="unauthorized"):
            await client.call("aria2.tellStatus", ["gid"])


@pytest.mark.asyncio
async def test_add_uri_returns_gid() -> None:
    client = Aria2Client("http://127.0.0.1:6800/jsonrpc")
    mock_call = AsyncMock(return_value="abc")
    with patch.object(client, "call", mock_call):
        gid = await client.add_uri("http://x.com/f", dir_path="/tmp", out="f.bin")
    assert gid == "abc"
    mock_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_wait_complete_success() -> None:
    client = Aria2Client("http://127.0.0.1:6800/jsonrpc")
    statuses = [{"status": "active"}, {"status": "complete", "gid": "g1"}]

    async def tell(_gid: str) -> dict:
        return statuses.pop(0)

    with patch.object(client, "tell_status", side_effect=tell):
        result = await client.wait_complete("g1", poll_interval=0.001)
    assert result["status"] == "complete"
