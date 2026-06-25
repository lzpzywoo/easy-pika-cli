from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pikpakapi.enums import DownloadStatus

from pikpak_downloader.offline_service import (
    PHASE_COMPLETE,
    PHASE_ERROR,
    OfflineTask,
    cleanup_cloud,
    collect_downloadable_file_ids,
    list_offline_tasks,
    parse_offline_create_result,
    wait_offline_complete,
)


def test_offline_task_from_api() -> None:
    task = OfflineTask.from_api(
        {
            "id": "task-1",
            "phase": PHASE_COMPLETE,
            "reference_resource": {"id": "file-1", "name": "movie.mkv"},
        }
    )
    assert task.task_id == "task-1"
    assert task.file_id == "file-1"
    assert task.name == "movie.mkv"
    assert task.phase == PHASE_COMPLETE


def test_parse_offline_create_result_nested_task() -> None:
    result = {
        "id": "root-file",
        "task": {"id": "task-99", "reference_resource": {"id": "file-99"}},
    }
    assert parse_offline_create_result(result) == ("task-99", "root-file")


def test_parse_offline_create_result_flat() -> None:
    result = {"id": "file-only", "task_id": "task-only"}
    assert parse_offline_create_result(result) == ("task-only", "file-only")


@pytest.mark.asyncio
async def test_list_offline_tasks() -> None:
    client = MagicMock()
    client.offline_list = AsyncMock(
        return_value={
            "tasks": [
                {"id": "t1", "phase": PHASE_COMPLETE, "reference_resource": {"id": "f1", "name": "a"}},
            ]
        }
    )
    tasks = await list_offline_tasks(client)
    assert len(tasks) == 1
    assert tasks[0].task_id == "t1"
    assert tasks[0].file_id == "f1"


@pytest.mark.asyncio
async def test_wait_offline_complete_via_status_done() -> None:
    client = MagicMock()
    client.get_task_status = AsyncMock(return_value=DownloadStatus.done)
    client.offline_list = AsyncMock(
        return_value={
            "tasks": [
                {
                    "id": "task-1",
                    "phase": PHASE_COMPLETE,
                    "reference_resource": {"id": "file-1", "name": "done.bin"},
                }
            ]
        }
    )
    task = await wait_offline_complete(
        client, "task-1", "file-1", timeout=1.0, poll_interval=0.01,
    )
    assert task.file_id == "file-1"
    assert task.phase == PHASE_COMPLETE


@pytest.mark.asyncio
async def test_wait_offline_complete_error_status() -> None:
    client = MagicMock()
    client.get_task_status = AsyncMock(return_value=DownloadStatus.error)
    with pytest.raises(RuntimeError, match="离线下载失败"):
        await wait_offline_complete(
            client, "task-1", "file-1", timeout=1.0, poll_interval=0.01,
        )


@pytest.mark.asyncio
async def test_wait_offline_complete_timeout() -> None:
    client = MagicMock()
    client.get_task_status = AsyncMock(return_value=DownloadStatus.downloading)
    client.offline_list = AsyncMock(return_value={"tasks": []})
    with pytest.raises(TimeoutError, match="超时"):
        await wait_offline_complete(
            client, "task-1", "file-1", timeout=0.05, poll_interval=0.01,
        )


@pytest.mark.asyncio
async def test_collect_downloadable_file_ids_single_file() -> None:
    client = MagicMock()
    client.offline_file_info = AsyncMock(
        return_value={"kind": "drive#file", "name": "single.mp4"},
    )
    entries = await collect_downloadable_file_ids(client, "file-1")
    assert entries == [("file-1", "single.mp4")]


@pytest.mark.asyncio
async def test_collect_downloadable_file_ids_folder() -> None:
    client = MagicMock()

    async def offline_file_info(fid: str):
        if fid == "root":
            return {"kind": "drive#folder", "name": "dir"}
        if fid == "f2":
            return {"kind": "drive#folder", "name": "sub"}
        return {"kind": "drive#file", "name": "nested.txt"}

    async def file_list(*, size, parent_id, next_page_token=None):
        if parent_id == "root":
            return {
                "files": [
                    {"id": "f1", "kind": "drive#file", "name": "a.bin"},
                    {"id": "f2", "kind": "drive#folder", "name": "sub"},
                ],
                "next_page_token": None,
            }
        if parent_id == "f2":
            return {
                "files": [{"id": "f3", "kind": "drive#file", "name": "nested.txt"}],
                "next_page_token": None,
            }
        raise AssertionError(f"unexpected parent_id={parent_id}")

    client.offline_file_info = AsyncMock(side_effect=offline_file_info)
    client.file_list = AsyncMock(side_effect=file_list)

    entries = await collect_downloadable_file_ids(client, "root")
    assert ("f1", "a.bin") in entries
    assert ("f3", "nested.txt") in entries


@pytest.mark.asyncio
async def test_cleanup_cloud_delete_forever() -> None:
    client = MagicMock()
    client.delete_tasks = AsyncMock()
    client.delete_forever = AsyncMock()
    client.delete_to_trash = AsyncMock()
    logs: list[str] = []

    await cleanup_cloud(
        client,
        ["file-1", "file-2"],
        delete_forever=True,
        task_ids=["task-1"],
        on_log=logs.append,
    )
    client.delete_tasks.assert_awaited_once_with(["task-1"], delete_files=True)
    client.delete_forever.assert_awaited_once_with(["file-1", "file-2"])
    client.delete_to_trash.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_cloud_trash_only() -> None:
    client = MagicMock()
    client.delete_forever = AsyncMock()
    client.delete_to_trash = AsyncMock()
    await cleanup_cloud(client, ["file-1"], delete_forever=False)
    client.delete_to_trash.assert_awaited_once_with(["file-1"])
