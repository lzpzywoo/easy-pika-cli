"""PikPak relay pipeline: magnet → cloud offline → local download → cleanup."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from pikpakapi import PikPakApi

from .aria2 import Aria2Client
from .config import DownloadBackend
from .download_dispatch import download_file_to_local
from .offline_service import (
    OfflineTask,
    add_offline,
    cleanup_cloud,
    collect_downloadable_file_ids,
    parse_offline_create_result,
    wait_offline_complete,
)
from .token_helpers import TokenManager

LogFn = Callable[[str], None]


@dataclass
class RelayResult:
    magnet: str
    task_id: str = ""
    file_ids: List[str] = field(default_factory=list)
    local_paths: List[Path] = field(default_factory=list)
    cleaned: bool = False


@dataclass
class RelayOptions:
    upload: bool = True
    wait: bool = True
    download: bool = True
    cleanup: bool = True
    cleanup_forever: bool = True
    dest_dir: Path = field(default_factory=lambda: Path("./downloads"))
    backend: DownloadBackend = "native"
    aria2: Optional[Aria2Client] = None
    threads: int = 12
    parent_id: Optional[str] = None
    timeout: float = 7200.0
    poll_interval: float = 10.0


def _log_default(msg: str) -> None:
    print(msg)


async def relay_magnet(
    client: PikPakApi,
    magnet: str,
    token_mgr: TokenManager,
    options: RelayOptions,
    *,
    on_log: LogFn = _log_default,
) -> RelayResult:
    result = RelayResult(magnet=magnet)
    task_id = ""
    root_file_id = ""

    if options.upload:
        on_log(f"提交离线下载: {magnet[:80]}...")
        created = await add_offline(
            client, magnet, parent_id=options.parent_id,
        )
        task_id, root_file_id = parse_offline_create_result(created)
        result.task_id = task_id
        if root_file_id:
            result.file_ids = [root_file_id]
        on_log(f"离线任务已创建 task={task_id} file={root_file_id}")
    else:
        raise ValueError("relay 需要 upload=True，或请使用 relay download 子命令")

    offline_task: Optional[OfflineTask] = None
    if options.wait:
        if not task_id or not root_file_id:
            raise ValueError("缺少 task_id / file_id，无法等待离线完成")
        on_log("等待 PikPak 离线下载完成...")
        offline_task = await wait_offline_complete(
            client,
            task_id,
            root_file_id,
            timeout=options.timeout,
            poll_interval=options.poll_interval,
            on_log=on_log,
        )
        root_file_id = offline_task.file_id or root_file_id

    if not options.download and not options.cleanup:
        return result

    file_entries = await collect_downloadable_file_ids(client, root_file_id)
    result.file_ids = [fid for fid, _ in file_entries]
    on_log(f"可下载文件数: {len(file_entries)}")

    if options.download:
        options.dest_dir.mkdir(parents=True, exist_ok=True)
        for file_id, name in file_entries:
            on_log(f"下载到本地: {name}")
            path = await download_file_to_local(
                client,
                file_id,
                options.dest_dir,
                token_mgr=token_mgr,
                backend=options.backend,
                aria2=options.aria2,
                threads=options.threads,
                filename=name,
            )
            result.local_paths.append(path)
            on_log(f"本地完成: {path}")

    if options.cleanup and result.file_ids:
        on_log("清理 PikPak 网盘文件...")
        await cleanup_cloud(
            client,
            result.file_ids,
            delete_forever=options.cleanup_forever,
            task_ids=[task_id] if task_id else None,
            on_log=on_log,
        )
        result.cleaned = True

    return result


async def relay_download_only(
    client: PikPakApi,
    file_id: str,
    token_mgr: TokenManager,
    options: RelayOptions,
    *,
    on_log: LogFn = _log_default,
) -> RelayResult:
    result = RelayResult(magnet="", file_ids=[file_id])
    file_entries = await collect_downloadable_file_ids(client, file_id)
    result.file_ids = [fid for fid, _ in file_entries]

    if options.download:
        options.dest_dir.mkdir(parents=True, exist_ok=True)
        for fid, name in file_entries:
            path = await download_file_to_local(
                client,
                fid,
                options.dest_dir,
                token_mgr=token_mgr,
                backend=options.backend,
                aria2=options.aria2,
                threads=options.threads,
                filename=name,
            )
            result.local_paths.append(path)
            on_log(f"本地完成: {path}")

    if options.cleanup and result.file_ids:
        await cleanup_cloud(
            client,
            result.file_ids,
            delete_forever=options.cleanup_forever,
            on_log=on_log,
        )
        result.cleaned = True

    return result
