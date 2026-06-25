import json
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from pikpakapi import PikPakApi

from .api_helpers import apply_client_defaults

DEFAULT_SESSION_PATH = Path.home() / ".easy-pika-cli" / "session.json"


def disk_free_gb(path: Path) -> float | None:
    """Return free space in GB for the volume containing path."""
    try:
        usage = shutil.disk_usage(path if path.exists() else path.anchor or path)
        return usage.free / (1024 ** 3)
    except OSError:
        return None


def get_session_path(custom: Optional[str] = None) -> Path:
    return Path(custom) if custom else DEFAULT_SESSION_PATH


def save_session(client: PikPakApi, path: Optional[str] = None) -> Path:
    session_path = get_session_path(path)
    try:
        session_path.parent.mkdir(parents=True, exist_ok=True)
        data = client.to_dict()
        session_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8",
        )
    except OSError as exc:
        free = disk_free_gb(session_path)
        free_hint = f"，该盘剩余约 {free:.1f} GB" if free is not None else ""
        raise OSError(
            f"无法写入会话文件 {session_path}{free_hint}: {exc}"
        ) from exc
    return session_path


def load_session(path: Optional[str] = None) -> PikPakApi:
    session_path = get_session_path(path)
    if not session_path.exists():
        raise FileNotFoundError(
            f"未找到登录会话: {session_path}\n请先运行: python main.py login -u 账号 -p 密码"
        )
    data: Dict[str, Any] = json.loads(session_path.read_text(encoding="utf-8"))
    client = PikPakApi.from_dict(data)
    return client


async def load_session_async(path: Optional[str] = None) -> PikPakApi:
    client = load_session(path)
    await apply_client_defaults(client)
    return client
