"""PikPak offline (magnet) task helpers."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from pikpakapi import PikPakApi
from pikpakapi.enums import DownloadStatus

from .api_helpers import retry_api_call

LogFn = Callable[[str], None]

PHASE_COMPLETE = "PHASE_TYPE_COMPLETE"
PHASE_RUNNING = "PHASE_TYPE_RUNNING"
PHASE_ERROR = "PHASE_TYPE_ERROR"
PHASE_PENDING = "PHASE_TYPE_PENDING"


@dataclass
class OfflineTask:
    task_id: str
    file_id: str
    name: str
    phase: str
    raw: dict

    @classmethod
    def from_api(cls, task: dict) -> "OfflineTask":
        ref = task.get("reference_resource") or {}
        file_id = ref.get("id") or task.get("file_id") or ""
        name = ref.get("name") or task.get("name") or file_id
        return cls(
            task_id=task.get("id", ""),
            file_id=file_id,
            name=name,
            phase=task.get("phase", ""),
            raw=task,
        )


def _noop_log(_msg: str) -> None:
    pass


async def add_offline(
    client: PikPakApi,
    url: str,
    *,
    parent_id: Optional[str] = None,
    name: Optional[str] = None,
) -> dict:
    return await retry_api_call(
        lambda: client.offline_download(url, parent_id=parent_id, name=name),
        label="离线下载",
    )


def parse_offline_create_result(result: dict) -> tuple[str, str]:
    """Return (task_id, file_id) from offline_download response."""
    file_id = result.get("id") or ""
    task = result.get("task") or {}
    task_id = task.get("id") or result.get("task_id") or ""
    if not file_id and task:
        ref = task.get("reference_resource") or {}
        file_id = ref.get("id") or ""
    if not task_id:
        task_id = result.get("id") or ""
    return task_id, file_id


async def list_offline_tasks(
    client: PikPakApi,
    *,
    phases: Optional[List[str]] = None,
    limit: int = 100,
) -> List[OfflineTask]:
    if phases is None:
        phases = [PHASE_RUNNING, PHASE_ERROR, PHASE_COMPLETE, PHASE_PENDING]
    result = await retry_api_call(
        lambda: client.offline_list(size=limit, phase=phases),
        label="离线任务列表",
    )
    tasks = result.get("tasks") or []
    return [OfflineTask.from_api(t) for t in tasks]


async def wait_offline_complete(
    client: PikPakApi,
    task_id: str,
    file_id: str,
    *,
    timeout: float = 7200.0,
    poll_interval: float = 10.0,
    on_log: LogFn = _noop_log,
) -> OfflineTask:
    deadline = time.monotonic() + timeout
    last_phase = ""

    while time.monotonic() < deadline:
        status = await client.get_task_status(task_id, file_id)
        if status == DownloadStatus.done:
            on_log(f"离线下载完成: {file_id}")
            tasks = await list_offline_tasks(
                client, phases=[PHASE_COMPLETE, PHASE_RUNNING], limit=200,
            )
            for t in tasks:
                if t.task_id == task_id or t.file_id == file_id:
                    return t
            try:
                info = await client.offline_file_info(file_id)
                return OfflineTask(
                    task_id=task_id,
                    file_id=file_id,
                    name=info.get("name", file_id),
                    phase=PHASE_COMPLETE,
                    raw=info,
                )
            except Exception:
                return OfflineTask(
                    task_id=task_id,
                    file_id=file_id,
                    name=file_id,
                    phase=PHASE_COMPLETE,
                    raw={},
                )

        if status == DownloadStatus.error:
            raise RuntimeError(f"离线下载失败: task={task_id} file={file_id}")

        tasks = await list_offline_tasks(client, limit=50)
        for t in tasks:
            if t.task_id == task_id or t.file_id == file_id:
                if t.phase != last_phase:
                    on_log(f"离线状态: {t.phase} — {t.name}")
                    last_phase = t.phase
                if t.phase == PHASE_ERROR:
                    raise RuntimeError(f"离线下载错误: {t.name}")
                if t.phase == PHASE_COMPLETE:
                    on_log(f"离线下载完成: {t.name}")
                    return t
                break

        await asyncio.sleep(poll_interval)

    raise TimeoutError(f"等待离线下载超时 ({timeout}s): task={task_id}")


async def collect_downloadable_file_ids(
    client: PikPakApi,
    root_file_id: str,
) -> List[tuple[str, str]]:
    """Return list of (file_id, name) under *root_file_id* (file or folder)."""
    info = await retry_api_call(
        lambda: client.offline_file_info(root_file_id),
        label="文件信息",
    )
    kind = info.get("kind") or ""
    if "folder" in kind:
        out: List[tuple[str, str]] = []
        page_token: Optional[str] = None
        while True:
            result = await retry_api_call(
                lambda pt=page_token: client.file_list(
                    size=200, parent_id=root_file_id, next_page_token=pt,
                ),
                label="文件夹列表",
            )
            for f in result.get("files") or []:
                if "folder" in (f.get("kind") or ""):
                    out.extend(await collect_downloadable_file_ids(client, f["id"]))
                else:
                    out.append((f["id"], f.get("name", f["id"])))
            page_token = result.get("next_page_token")
            if not page_token:
                break
        return out

    return [(root_file_id, info.get("name", root_file_id))]


async def cleanup_cloud(
    client: PikPakApi,
    file_ids: List[str],
    *,
    delete_forever: bool = True,
    task_ids: Optional[List[str]] = None,
    on_log: LogFn = _noop_log,
) -> None:
    if task_ids:
        for tid in task_ids:
            if not tid:
                continue
            try:
                await client.delete_tasks([tid], delete_files=True)
                on_log(f"已删除离线任务: {tid}")
            except Exception as exc:
                on_log(f"删除离线任务失败 {tid}: {exc}")

    ids = [i for i in file_ids if i]
    if not ids:
        return

    try:
        if delete_forever:
            await client.delete_forever(ids)
        else:
            await client.delete_to_trash(ids)
        on_log(f"已清理网盘文件 ({len(ids)}): {', '.join(ids)}")
    except Exception as exc:
        on_log(f"清理网盘文件失败: {exc}")
        raise
