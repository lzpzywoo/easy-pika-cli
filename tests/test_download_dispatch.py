from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pikpak_downloader.aria2 import Aria2Client
from pikpak_downloader.download_dispatch import download_file_to_local
from pikpak_downloader.token_helpers import TokenManager


@pytest.fixture
def token_mgr() -> TokenManager:
    return TokenManager(MagicMock(), None)


@pytest.mark.asyncio
async def test_download_native_backend(
    tmp_download_dir: Path,
    token_mgr: TokenManager,
) -> None:
    client = MagicMock()
    file_info = {"name": "a.mkv", "medias": [{"link": {"url": "https://cdn/x"}}], "size": 1}

    with (
        patch(
            "pikpak_downloader.download_dispatch.get_download_url_with_retry",
            new_callable=AsyncMock,
            return_value=file_info,
        ),
        patch(
            "pikpak_downloader.download_dispatch.download_from_file_info",
            new_callable=AsyncMock,
            return_value=tmp_download_dir / "a.mkv",
        ) as native_dl,
    ):
        path = await download_file_to_local(
            client, "fid", tmp_download_dir, token_mgr=token_mgr, backend="native",
        )

    assert path == tmp_download_dir / "a.mkv"
    native_dl.assert_awaited_once()


@pytest.mark.asyncio
async def test_download_aria2_backend(
    tmp_download_dir: Path,
    token_mgr: TokenManager,
) -> None:
    client = MagicMock()
    file_info = {"name": "b.mkv", "medias": [{"link": {"url": "https://cdn/y"}}], "size": 1}
    aria2 = MagicMock(spec=Aria2Client)
    aria2.add_uri = AsyncMock(return_value="gid-1")
    aria2.wait_complete = AsyncMock(return_value={"status": "complete"})

    with patch(
        "pikpak_downloader.download_dispatch.get_download_url_with_retry",
        new_callable=AsyncMock,
        return_value=file_info,
    ):
        path = await download_file_to_local(
            client,
            "fid",
            tmp_download_dir,
            token_mgr=token_mgr,
            backend="aria2",
            aria2=aria2,
            filename="b.mkv",
        )

    assert path == tmp_download_dir / "b.mkv"
    aria2.add_uri.assert_awaited_once()
    aria2.wait_complete.assert_awaited_once_with("gid-1")


@pytest.mark.asyncio
async def test_download_aria2_without_client_raises(token_mgr: TokenManager) -> None:
    with (
        patch(
            "pikpak_downloader.download_dispatch.get_download_url_with_retry",
            new_callable=AsyncMock,
            return_value={"name": "x", "medias": [{"link": {"url": "https://cdn/x"}}]},
        ),
        pytest.raises(ValueError, match="Aria2 backend requires"),
    ):
        await download_file_to_local(
            MagicMock(), "fid", Path("/tmp"), token_mgr=token_mgr, backend="aria2",
        )
