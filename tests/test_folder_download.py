from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pikpak_downloader.folder_download import (
    collect_downloadable_files,
    resolve_download_targets,
)


@pytest.mark.asyncio
async def test_collect_downloadable_files_single_file() -> None:
    client = MagicMock()
    client.offline_file_info = AsyncMock(
        return_value={"kind": "drive#file", "name": "single.mp4", "size": "100"},
    )
    rows = await collect_downloadable_files(client, "file-1")
    assert rows == [("file-1", "single.mp4", 100)]


@pytest.mark.asyncio
async def test_collect_downloadable_files_nested_folder() -> None:
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
                    {"id": "f1", "kind": "drive#file", "name": "a.bin", "size": "10"},
                    {"id": "f2", "kind": "drive#folder", "name": "sub"},
                ],
                "next_page_token": None,
            }
        if parent_id == "f2":
            return {
                "files": [{"id": "f3", "kind": "drive#file", "name": "nested.txt", "size": "5"}],
                "next_page_token": None,
            }
        raise AssertionError(f"unexpected parent_id={parent_id}")

    client.offline_file_info = AsyncMock(side_effect=offline_file_info)
    client.file_list = AsyncMock(side_effect=file_list)

    rows = await collect_downloadable_files(client, "root", prefix="MyFolder/")
    assert ("f1", "MyFolder/a.bin", 10) in rows
    assert ("f3", "MyFolder/sub/nested.txt", 5) in rows


@pytest.mark.asyncio
async def test_resolve_download_targets_folder_path() -> None:
    client = MagicMock()
    client.path_to_id = AsyncMock(
        return_value=[{"id": "root", "name": "Show", "file_type": "folder"}],
    )
    client.offline_file_info = AsyncMock(
        return_value={"kind": "drive#folder", "name": "Show"},
    )
    client.file_list = AsyncMock(
        return_value={
            "files": [{"id": "f1", "kind": "drive#file", "name": "ep1.mkv", "size": "1"}],
            "next_page_token": None,
        },
    )
    entries = await resolve_download_targets(client, "/Videos/Show")
    assert len(entries) == 1
    assert entries[0].file_id == "f1"
    assert entries[0].rel_path == "Show/ep1.mkv"


@pytest.mark.asyncio
async def test_resolve_download_targets_empty_folder_raises() -> None:
    client = MagicMock()
    client.offline_file_info = AsyncMock(
        return_value={"kind": "drive#folder", "name": "empty"},
    )
    client.file_list = AsyncMock(return_value={"files": [], "next_page_token": None})
    with pytest.raises(ValueError, match="文件夹为空"):
        await resolve_download_targets(client, "folder-id")
