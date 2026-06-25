from __future__ import annotations

import os

import pytest

from pikpak_downloader.config import AppConfig


def test_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOWNLOAD_BACKEND", raising=False)
    cfg = AppConfig.from_env()
    assert cfg.download_backend == "native"
    assert cfg.download_dir.name == "downloads"
    assert cfg.relay_cleanup_cloud is True
    assert cfg.ai_api_port == 8765
    assert cfg.telegram_allowed_users == frozenset()


def test_from_env_telegram_allowed_users(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "1, 42, bad, 7")
    cfg = AppConfig.from_env()
    assert cfg.telegram_allowed_users == frozenset({1, 42, 7})


def test_from_env_invalid_backend_falls_back_to_native(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOWNLOAD_BACKEND", "invalid")
    cfg = AppConfig.from_env()
    assert cfg.download_backend == "native"


def test_from_env_relay_cleanup_false(monkeypatch: pytest.MonkeyPatch) -> None:
    for val in ("0", "false", "no"):
        monkeypatch.setenv("RELAY_CLEANUP_CLOUD", val)
        assert AppConfig.from_env().relay_cleanup_cloud is False


def test_from_env_ai_api_key_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.setenv("EASY_PIKA_API_KEY", "alias-key")
    assert AppConfig.from_env().ai_api_key == "alias-key"


def test_from_env_custom_session_path() -> None:
    cfg = AppConfig.from_env(session_path="/tmp/custom.json")
    assert cfg.session_path == "/tmp/custom.json"
