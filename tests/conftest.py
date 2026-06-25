from __future__ import annotations

import os
from pathlib import Path

import pytest

from pikpak_downloader.config import AppConfig


@pytest.fixture
def tmp_download_dir(tmp_path: Path) -> Path:
    d = tmp_path / "downloads"
    d.mkdir()
    return d


@pytest.fixture
def app_config(tmp_download_dir: Path) -> AppConfig:
    return AppConfig(
        session_path=None,
        download_dir=tmp_download_dir,
        download_backend="native",
        aria2_rpc_url="http://127.0.0.1:6800/jsonrpc",
        aria2_rpc_secret="secret",
        telegram_token="tg-token",
        telegram_allowed_users=frozenset({42, 99}),
        ai_api_key="test-api-key",
        ai_api_host="127.0.0.1",
        ai_api_port=8765,
        openai_api_key="",
        openai_base_url="https://api.openai.com/v1",
        openai_model="gpt-4o-mini",
        relay_cleanup_cloud=True,
        relay_poll_interval=0.01,
        relay_timeout=5.0,
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith(
            (
                "DOWNLOAD_",
                "RELAY_",
                "TELEGRAM_",
                "AI_API_",
                "ARIA2_",
                "OPENAI_",
                "SESSION_",
                "EASY_PIKA_",
            )
        ):
            monkeypatch.delenv(key, raising=False)
