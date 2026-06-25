from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sys
from dataclasses import replace

from pikpak_downloader.telegram_bot import TelegramRelayBot


def test_allowed_user_empty_whitelist_allows_all(app_config) -> None:
    cfg = replace(app_config, telegram_allowed_users=frozenset())
    bot = TelegramRelayBot(cfg)
    assert bot._allowed(12345) is True


def test_allowed_user_in_whitelist(app_config) -> None:
    bot = TelegramRelayBot(app_config)
    assert bot._allowed(42) is True
    assert bot._allowed(1) is False


@pytest.mark.asyncio
async def test_handle_links_no_links_replies(app_config) -> None:
    bot = TelegramRelayBot(app_config)
    replies: list[str] = []

    async def reply(msg: str) -> None:
        replies.append(msg)

    with patch(
        "pikpak_downloader.telegram_bot.resolve_links",
        new_callable=AsyncMock,
        return_value=[],
    ):
        await bot._handle_links(1, "hello", reply)

    assert replies == ["未识别到磁链或 .torrent 链接。"]


@pytest.mark.asyncio
async def test_handle_links_runs_relay(app_config) -> None:
    bot = TelegramRelayBot(app_config)
    replies: list[str] = []

    async def reply(msg: str) -> None:
        replies.append(msg)

    mock_client = MagicMock()
    mock_token_mgr = MagicMock()
    mock_token_mgr.refresh = AsyncMock()
    relay_result = MagicMock(
        task_id="t1",
        file_ids=["f1"],
        local_paths=[],
        cleaned=True,
    )

    with (
        patch(
            "pikpak_downloader.telegram_bot.resolve_links",
            new_callable=AsyncMock,
            return_value=["magnet:?xt=urn:btih:abc"],
        ),
        patch.object(
            bot,
            "_ensure_client",
            new_callable=AsyncMock,
            return_value=(mock_client, mock_token_mgr),
        ),
        patch(
            "pikpak_downloader.telegram_bot.relay_magnet",
            new_callable=AsyncMock,
            return_value=relay_result,
        ),
    ):
        await bot._handle_links(1, "magnet:?xt=urn:btih:abc", reply)

    assert any("开始" in r for r in replies)
    assert any("完成" in r for r in replies)


@pytest.mark.asyncio
async def test_run_polling_missing_token(app_config) -> None:
    cfg = replace(app_config, telegram_token="")
    bot = TelegramRelayBot(cfg)
    fake_telegram = MagicMock()
    fake_ext = MagicMock()
    with patch.dict(
        "sys.modules",
        {"telegram": fake_telegram, "telegram.ext": fake_ext},
    ):
        with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
            await bot.run_polling()
