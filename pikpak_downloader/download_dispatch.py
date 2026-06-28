"""Dispatch downloads to native httpx engine or Aria2 RPC."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pikpakapi import PikPakApi

from .aria2 import Aria2Client
from .config import DownloadBackend
from .downloader import _pick_download_url, download_from_file_info
from .token_helpers import TokenManager, get_download_url_with_retry


async def download_file_to_local(
    client: PikPakApi,
    file_id: str,
    dest_dir: Path,
    *,
    token_mgr: TokenManager,
    backend: DownloadBackend = "native",
    aria2: Optional[Aria2Client] = None,
    threads: int = 12,
    filename: Optional[str] = None,
    rel_path: Optional[str] = None,
) -> Path:
    file_info = await get_download_url_with_retry(client, file_id, token_mgr)
    if rel_path:
        dest_sub = dest_dir / rel_path
        dest_sub.parent.mkdir(parents=True, exist_ok=True)
        out_name = filename or dest_sub.name
        out_dir = dest_sub.parent
    else:
        out_name = filename or file_info.get("name") or file_id
        out_dir = dest_dir

    if backend == "aria2":
        if aria2 is None:
            raise ValueError("Aria2 backend requires aria2 client")
        url = _pick_download_url(file_info)
        out_dir.mkdir(parents=True, exist_ok=True)
        gid = await aria2.add_uri(url, dir_path=str(out_dir), out=out_name)
        await aria2.wait_complete(gid)
        return out_dir / out_name

    return await download_from_file_info(
        file_info,
        out_dir,
        threads=threads,
        headers=token_mgr.get_headers(),
        filename=out_name,
    )
