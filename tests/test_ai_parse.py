from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pikpak_downloader.ai_parse import parse_message_with_llm, resolve_links
from pikpak_downloader.magnets import extract_links


@pytest.mark.asyncio
async def test_parse_message_without_api_key_falls_back_to_regex() -> None:
    text = "magnet:?xt=urn:btih:abc"
    links = await parse_message_with_llm(text, api_key="")
    assert links == extract_links(text)


@pytest.mark.asyncio
async def test_parse_message_with_llm_json_response() -> None:
    text = "please download this"
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": '{"links": ["magnet:?xt=urn:btih:xyz"]}'}}]
    }
    mock_http = MagicMock()
    mock_http.post = AsyncMock(return_value=mock_resp)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)

    with patch("pikpak_downloader.ai_parse.httpx.AsyncClient", return_value=mock_http):
        links = await parse_message_with_llm(
            text, api_key="sk-test", base_url="https://api.example.com/v1",
        )

    assert links == ["magnet:?xt=urn:btih:xyz"]


@pytest.mark.asyncio
async def test_parse_message_llm_invalid_json_fallback() -> None:
    text = "magnet:?xt=urn:btih:fallback"
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "not json"}}]
    }
    mock_http = MagicMock()
    mock_http.post = AsyncMock(return_value=mock_resp)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)

    with patch("pikpak_downloader.ai_parse.httpx.AsyncClient", return_value=mock_http):
        links = await parse_message_with_llm(text, api_key="sk-test")

    assert links == ["magnet:?xt=urn:btih:fallback"]


@pytest.mark.asyncio
async def test_resolve_links_without_llm() -> None:
    text = "https://x.com/a.torrent"
    links = await resolve_links(
        text, use_llm=False, api_key="", base_url="", model="",
    )
    assert links == ["https://x.com/a.torrent"]


@pytest.mark.asyncio
async def test_resolve_links_with_llm() -> None:
    with patch(
        "pikpak_downloader.ai_parse.parse_message_with_llm",
        new_callable=AsyncMock,
        return_value=["magnet:?xt=1"],
    ) as llm:
        links = await resolve_links(
            "hi", use_llm=True, api_key="k", base_url="https://api.openai.com/v1", model="m",
        )
    assert links == ["magnet:?xt=1"]
    llm.assert_awaited_once()
