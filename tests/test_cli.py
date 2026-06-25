from __future__ import annotations

import pytest

from pikpak_downloader.cli import build_parser, main


@pytest.fixture
def parser():
    return build_parser()


def test_parser_has_core_commands(parser) -> None:
    for cmd in ("login", "ls", "download", "quota", "offline", "relay", "telegram", "ai", "gui"):
        assert cmd in parser._subparsers._group_actions[0].choices


def test_parse_relay_run(parser) -> None:
    args = parser.parse_args(
        ["relay", "run", "magnet:?xt=1", "-o", "./out", "--no-cleanup"],
    )
    assert args.command == "relay"
    assert args.relay_cmd == "run"
    assert args.magnets == ["magnet:?xt=1"]
    assert args.output == "./out"
    assert args.no_cleanup is True


def test_parse_offline_add(parser) -> None:
    args = parser.parse_args(["offline", "add", "magnet:?xt=2", "--name", "x"])
    assert args.offline_cmd == "add"
    assert args.url == "magnet:?xt=2"
    assert args.name == "x"


def test_parse_download_with_aria2_backend(parser) -> None:
    args = parser.parse_args(
        ["download", "file-id", "-o", "/tmp", "--backend", "aria2", "--aria2-rpc", "http://localhost:6800/jsonrpc"],
    )
    assert args.backend == "aria2"
    assert args.aria2_rpc == "http://localhost:6800/jsonrpc"


def test_parse_ai_serve(parser) -> None:
    args = parser.parse_args(["ai", "serve", "--port", "9000", "--api-key", "secret"])
    assert args.command == "ai"
    assert args.ai_cmd == "serve"
    assert args.port == 9000
    assert args.api_key == "secret"


def test_main_gui_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "pikpak_downloader.cli.cmd_gui",
        lambda _a: 1,
    )
    assert main(["gui"]) == 1
