from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pikpak_downloader.relay import RelayOptions, relay_download_only, relay_magnet
from pikpak_downloader.token_helpers import TokenManager


@pytest.fixture
def token_mgr() -> TokenManager:
    client = MagicMock()
    return TokenManager(client, None)


@pytest.mark.asyncio
async def test_relay_magnet_full_pipeline(
    tmp_download_dir: Path,
    token_mgr: TokenManager,
) -> None:
    client = MagicMock()
    magnet = "magnet:?xt=urn:btih:abc"
    opts = RelayOptions(
        dest_dir=tmp_download_dir,
        cleanup=True,
        poll_interval=0.01,
        timeout=1.0,
    )

    with (
        patch("pikpak_downloader.relay.add_offline", new_callable=AsyncMock) as add_off,
        patch("pikpak_downloader.relay.parse_offline_create_result") as parse_res,
        patch("pikpak_downloader.relay.wait_offline_complete", new_callable=AsyncMock) as wait_off,
        patch("pikpak_downloader.relay.collect_downloadable_file_ids", new_callable=AsyncMock) as collect,
        patch("pikpak_downloader.relay.download_file_to_local", new_callable=AsyncMock) as dl,
        patch("pikpak_downloader.relay.cleanup_cloud", new_callable=AsyncMock) as cleanup,
    ):
        add_off.return_value = {"id": "f1", "task": {"id": "t1"}}
        parse_res.return_value = ("t1", "f1")
        wait_off.return_value = MagicMock(file_id="f1", name="a.mkv")
        collect.return_value = [("f1", "a.mkv")]
        dl.return_value = tmp_download_dir / "a.mkv"

        result = await relay_magnet(client, magnet, token_mgr, opts)

    assert result.task_id == "t1"
    assert result.file_ids == ["f1"]
    assert result.local_paths == [tmp_download_dir / "a.mkv"]
    assert result.cleaned is True
    add_off.assert_awaited_once()
    wait_off.assert_awaited_once()
    dl.assert_awaited_once()
    cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_relay_magnet_upload_false_raises(token_mgr: TokenManager) -> None:
    client = MagicMock()
    opts = RelayOptions(upload=False)
    with pytest.raises(ValueError, match="upload=True"):
        await relay_magnet(client, "magnet:?x=1", token_mgr, opts)


@pytest.mark.asyncio
async def test_relay_magnet_skip_download_and_cleanup(
    tmp_download_dir: Path,
    token_mgr: TokenManager,
) -> None:
    client = MagicMock()
    opts = RelayOptions(
        download=False,
        cleanup=False,
        dest_dir=tmp_download_dir,
        poll_interval=0.01,
        timeout=1.0,
    )

    with (
        patch("pikpak_downloader.relay.add_offline", new_callable=AsyncMock) as add_off,
        patch("pikpak_downloader.relay.parse_offline_create_result", return_value=("t1", "f1")),
        patch("pikpak_downloader.relay.wait_offline_complete", new_callable=AsyncMock) as wait_off,
        patch("pikpak_downloader.relay.collect_downloadable_file_ids", new_callable=AsyncMock) as collect,
        patch("pikpak_downloader.relay.download_file_to_local", new_callable=AsyncMock) as dl,
        patch("pikpak_downloader.relay.cleanup_cloud", new_callable=AsyncMock) as cleanup,
    ):
        wait_off.return_value = MagicMock(file_id="f1")
        collect.return_value = [("f1", "a.mkv")]
        result = await relay_magnet(client, "magnet:?x=1", token_mgr, opts)

    assert result.file_ids == ["f1"]
    assert result.local_paths == []
    assert result.cleaned is False
    dl.assert_not_awaited()
    cleanup.assert_not_awaited()


@pytest.mark.asyncio
async def test_relay_download_only_with_cleanup(
    tmp_download_dir: Path,
    token_mgr: TokenManager,
) -> None:
    client = MagicMock()
    opts = RelayOptions(
        dest_dir=tmp_download_dir,
        download=True,
        cleanup=True,
    )

    with (
        patch("pikpak_downloader.relay.collect_downloadable_file_ids", new_callable=AsyncMock) as collect,
        patch("pikpak_downloader.relay.download_file_to_local", new_callable=AsyncMock) as dl,
        patch("pikpak_downloader.relay.cleanup_cloud", new_callable=AsyncMock) as cleanup,
    ):
        collect.return_value = [("f1", "b.mkv")]
        dl.return_value = tmp_download_dir / "b.mkv"
        result = await relay_download_only(client, "f1", token_mgr, opts)

    assert result.local_paths == [tmp_download_dir / "b.mkv"]
    assert result.cleaned is True
    cleanup.assert_awaited_once()
