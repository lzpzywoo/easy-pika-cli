"""Runtime configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DownloadBackend = Literal["native", "aria2"]


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class AppConfig:
    session_path: str | None
    download_dir: Path
    download_backend: DownloadBackend
    aria2_rpc_url: str
    aria2_rpc_secret: str
    telegram_token: str
    telegram_allowed_users: frozenset[int]
    ai_api_key: str
    ai_api_host: str
    ai_api_port: int
    openai_api_key: str
    openai_base_url: str
    openai_model: str
    relay_cleanup_cloud: bool
    relay_poll_interval: float
    relay_timeout: float

    @classmethod
    def from_env(cls, session_path: str | None = None) -> "AppConfig":
        allowed_raw = _env("TELEGRAM_ALLOWED_USERS")
        allowed: set[int] = set()
        if allowed_raw:
            for part in allowed_raw.split(","):
                part = part.strip()
                if part.isdigit():
                    allowed.add(int(part))

        backend = _env("DOWNLOAD_BACKEND", "native").lower()
        if backend not in ("native", "aria2"):
            backend = "native"

        return cls(
            session_path=session_path,
            download_dir=Path(_env("DOWNLOAD_DIR", "./downloads")).expanduser(),
            download_backend=backend,  # type: ignore[arg-type]
            aria2_rpc_url=_env("ARIA2_RPC_URL", "http://127.0.0.1:6800/jsonrpc"),
            aria2_rpc_secret=_env("ARIA2_RPC_SECRET"),
            telegram_token=_env("TELEGRAM_BOT_TOKEN"),
            telegram_allowed_users=frozenset(allowed),
            ai_api_key=_env("AI_API_KEY", _env("EASY_PIKA_API_KEY")),
            ai_api_host=_env("AI_API_HOST", "0.0.0.0"),
            ai_api_port=int(_env("AI_API_PORT", "8765") or "8765"),
            openai_api_key=_env("OPENAI_API_KEY"),
            openai_base_url=_env("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            openai_model=_env("OPENAI_MODEL", "gpt-4o-mini"),
            relay_cleanup_cloud=_env("RELAY_CLEANUP_CLOUD", "true").lower()
            not in ("0", "false", "no"),
            relay_poll_interval=float(_env("RELAY_POLL_INTERVAL", "10") or "10"),
            relay_timeout=float(_env("RELAY_TIMEOUT", "7200") or "7200"),
        )
