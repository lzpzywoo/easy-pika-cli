from __future__ import annotations

from pikpak_downloader.magnets import extract_links, is_magnet_or_torrent


def test_extract_magnet_link() -> None:
    text = "download this magnet:?xt=urn:btih:abc123&dn=file"
    assert extract_links(text) == ["magnet:?xt=urn:btih:abc123&dn=file"]


def test_extract_torrent_url() -> None:
    text = "get https://example.com/a/file.torrent?token=1 here"
    assert extract_links(text) == ["https://example.com/a/file.torrent?token=1"]


def test_extract_multiple_unique_preserves_order() -> None:
    text = (
        "magnet:?xt=urn:btih:aaa "
        "https://x.com/b.torrent "
        "magnet:?xt=urn:btih:bbb "
        "magnet:?xt=urn:btih:aaa"
    )
    links = extract_links(text)
    # magnets are collected before torrent URLs (per pattern order in extract_links)
    assert links == [
        "magnet:?xt=urn:btih:aaa",
        "magnet:?xt=urn:btih:bbb",
        "https://x.com/b.torrent",
    ]


def test_extract_strips_trailing_punctuation() -> None:
    text = "(magnet:?xt=urn:btih:abc)."
    assert extract_links(text) == ["magnet:?xt=urn:btih:abc"]


def test_extract_empty_text() -> None:
    assert extract_links("") == []


def test_is_magnet_or_torrent() -> None:
    assert is_magnet_or_torrent("magnet:?xt=1") is True
    assert is_magnet_or_torrent("https://x.com/a.torrent") is True
    assert is_magnet_or_torrent("https://x.com/a.zip") is False
    assert is_magnet_or_torrent("") is False
