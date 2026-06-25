from __future__ import annotations

from pathlib import Path

import pytest

from pikpak_downloader.downloader import (
    LEGACY_MULTIPART_SLICE,
    MULTIPART_SLICE,
    multipart_slice_for,
    read_file_size,
    reconcile_file_size,
    save_file_size,
    trim_orphan_parts,
    _part_ranges,
    _pick_download_url,
)


def test_save_and_read_file_size(tmp_path: Path) -> None:
    dest = tmp_path / "video.mkv"
    save_file_size(dest, 1024)
    assert read_file_size(dest, fallback=0) == 1024
    assert read_file_size(dest, fallback=999) == 1024


def test_multipart_slice_for_new_download(tmp_path: Path) -> None:
    dest = tmp_path / "file.bin"
    assert multipart_slice_for(dest, 300 * 1024 * 1024) == MULTIPART_SLICE
    part_dir = dest.with_suffix(dest.suffix + ".parts")
    assert (part_dir / ".slice_size").read_text() == str(MULTIPART_SLICE)


def test_multipart_slice_for_legacy_parts(tmp_path: Path) -> None:
    dest = tmp_path / "legacy.bin"
    part_dir = dest.with_suffix(dest.suffix + ".parts")
    part_dir.mkdir()
    file_size = 200 * 1024 * 1024
    for i in range(10):
        (part_dir / f"part_{i:06d}").write_bytes(b"x")
    slice_sz = multipart_slice_for(dest, file_size)
    assert slice_sz == LEGACY_MULTIPART_SLICE


def test_part_ranges_cover_file() -> None:
    dest = Path("/tmp/x.bin")
    ranges = _part_ranges(dest, file_size=100, slice_size=30)
    assert len(ranges) == 4
    assert ranges[0][0] == 0
    assert ranges[-1][1] == 99


def test_trim_orphan_parts(tmp_path: Path) -> None:
    dest = tmp_path / "f.bin"
    file_size = 100
    slice_size = 50
    part_dir = dest.with_suffix(dest.suffix + ".parts")
    part_dir.mkdir()
    for p in _part_ranges(dest, file_size, slice_size):
        p[2].write_bytes(b"a" * 10)
    orphan = part_dir / "part_999999"
    orphan.write_bytes(b"orphan")
    trim_orphan_parts(dest, file_size, slice_size)
    assert not orphan.exists()
    assert all(p[2].exists() for p in _part_ranges(dest, file_size, slice_size))


def test_reconcile_file_size_prefers_cdn(tmp_path: Path) -> None:
    dest = tmp_path / "f.bin"
    size = reconcile_file_size(dest, api_size=1000, probed_size=2000)
    assert size == 2000
    assert read_file_size(dest) == 2000


def test_pick_download_url_from_medias() -> None:
    url, size = _pick_download_url(
        {"size": "42", "medias": [{"link": {"url": "https://cdn.example/a"}}]},
    )
    assert url == "https://cdn.example/a"
    assert size == 42


def test_pick_download_url_from_web_content_link() -> None:
    url, size = _pick_download_url({"size": 10, "web_content_link": "https://cdn.example/b"})
    assert url == "https://cdn.example/b"
    assert size == 10


def test_pick_download_url_missing_raises() -> None:
    with pytest.raises(ValueError, match="无法获取下载链接"):
        _pick_download_url({"size": 1})
