"""Resolve PikPak folders into downloadable file entries with relative paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import List

from pikpakapi import PikPakApi

from .api_helpers import retry_api_call


@dataclass(frozen=True)
class DownloadEntry:
    file_id: str
    rel_path: str
    size: int = 0


async def collect_downloadable_files(
    client: PikPakApi,
    root_file_id: str,
    prefix: str = "",
) -> List[tuple[str, str, int]]:
    """Return (file_id, relative_path, size_bytes) under *root_file_id*."""
    info = await retry_api_call(
        lambda: client.offline_file_info(root_file_id),
        label="文件信息",
    )
    kind = info.get("kind") or ""
    if "folder" not in kind:
        name = info.get("name") or root_file_id
        rel = f"{prefix}{name}" if prefix else name
        return [(root_file_id, rel, int(info.get("size") or 0))]

    out: List[tuple[str, str, int]] = []
    page_token: str | None = None
    while True:
        result = await retry_api_call(
            lambda pt=page_token: client.file_list(
                size=200, parent_id=root_file_id, next_page_token=pt,
            ),
            label="文件夹列表",
        )
        for f in result.get("files") or []:
            fname = f.get("name") or f["id"]
            if "folder" in (f.get("kind") or ""):
                sub_prefix = f"{prefix}{fname}/"
                out.extend(await collect_downloadable_files(client, f["id"], sub_prefix))
            else:
                out.append((f["id"], f"{prefix}{fname}", int(f.get("size") or 0)))
        page_token = result.get("next_page_token")
        if not page_token:
            break
    return out


async def resolve_download_targets(
    client: PikPakApi,
    target: str,
) -> List[DownloadEntry]:
    """Expand a file ID or cloud path into downloadable entries."""
    if target.startswith("/"):
        records = await client.path_to_id(target)
        if not records:
            raise ValueError(f"路径不存在: {target}")
        last = records[-1]
        fid = last["id"]
        if last.get("file_type") == "folder":
            folder_name = last.get("name") or PurePosixPath(target.rstrip("/")).name
            rows = await collect_downloadable_files(client, fid, prefix=f"{folder_name}/")
        else:
            name = last.get("name") or fid
            rows = [(fid, name, 0)]
    else:
        info = await retry_api_call(
            lambda: client.offline_file_info(target),
            label="文件信息",
        )
        if "folder" in (info.get("kind") or ""):
            folder_name = info.get("name") or target
            rows = await collect_downloadable_files(
                client, target, prefix=f"{folder_name}/",
            )
        else:
            name = info.get("name") or target
            rows = [(target, name, int(info.get("size") or 0))]

    if not rows:
        raise ValueError(f"文件夹为空: {target}")

    return [DownloadEntry(fid, rel, sz) for fid, rel, sz in rows]
