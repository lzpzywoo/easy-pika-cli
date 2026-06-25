import argparse
import asyncio
import sys
from pathlib import Path
from typing import List, Optional

import httpx
from pikpakapi import PikPakApi
from pikpakapi.PikpakException import PikpakException
from rich.console import Console
from rich.table import Table

from .downloader import download_from_file_info
from .session import load_session, save_session
from .token_helpers import TokenManager, get_download_url_with_retry
from .api_helpers import apply_client_defaults, get_client_kwargs, retry_api_call

console = Console()


def _format_size(size: Optional[str | int]) -> str:
    if not size:
        return "-"
    n = int(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} PB"


def _is_folder(file_item: dict) -> bool:
    return "folder" in (file_item.get("kind") or "")


async def cmd_login(args: argparse.Namespace) -> int:
    client = PikPakApi(
        username=args.username,
        password=args.password,
        **get_client_kwargs(),
    )
    try:
        console.print("[yellow]正在登录 PikPak...[/yellow]")
        await client.login()
        await client.refresh_access_token()
    except PikpakException as e:
        console.print(f"[red]登录失败: {e}[/red]")
        return 1

    path = save_session(client, args.session)
    info = client.get_user_info()
    console.print(f"[green]登录成功[/green]  user_id={info.get('user_id')}")
    console.print(f"会话已保存: {path}")
    return 0


async def _ensure_token(client: PikPakApi) -> None:
    await retry_api_call(client.refresh_access_token, label="Token 刷新")


async def cmd_ls(args: argparse.Namespace) -> int:
    try:
        client = load_session(args.session)
        await apply_client_defaults(client)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return 1

    await _ensure_token(client)

    parent_id = None
    if args.path and args.path != "/":
        records = await client.path_to_id(args.path)
        if not records:
            console.print(f"[red]路径不存在: {args.path}[/red]")
            return 1
        last = records[-1]
        if last.get("file_type") != "folder":
            console.print(f"[red]不是文件夹: {args.path}[/red]")
            return 1
        parent_id = last["id"]

    result = await retry_api_call(
        lambda: client.file_list(size=args.limit, parent_id=parent_id),
        label="文件列表",
    )
    files = result.get("files") or []

    table = Table(title=f"PikPak 文件列表  {args.path or '/'}")
    table.add_column("类型", style="cyan", width=6)
    table.add_column("名称")
    table.add_column("大小", justify="right")
    table.add_column("ID", style="dim")

    for f in files:
        kind = "文件夹" if _is_folder(f) else "文件"
        table.add_row(kind, f.get("name", ""), _format_size(f.get("size")), f.get("id", ""))

    console.print(table)
    if result.get("next_page_token"):
        console.print("[dim]还有更多文件，使用 --limit 增大数量[/dim]")
    return 0


async def _resolve_file(client: PikPakApi, target: str) -> dict:
    """通过 file_id 或网盘路径解析文件信息。"""
    if target.startswith("/"):
        records = await client.path_to_id(target)
        if not records:
            raise ValueError(f"路径不存在: {target}")
        last = records[-1]
        if last.get("file_type") == "folder":
            raise ValueError(f"目标是文件夹，请指定具体文件: {target}")
        file_id = last["id"]
    else:
        file_id = target

    return await client.get_download_url(file_id)


async def cmd_download(args: argparse.Namespace) -> int:
    try:
        client = load_session(args.session)
        await apply_client_defaults(client)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return 1

    await _ensure_token(client)
    dest_dir = Path(args.output).expanduser().resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    targets: List[str] = args.targets
    if not targets:
        console.print("[red]请指定要下载的文件 ID 或路径[/red]")
        return 1

    token_mgr = TokenManager(client, args.session)
    sem = asyncio.Semaphore(max(1, args.concurrent))

    async def download_one(target: str) -> int:
        async with sem:
            try:
                console.print(f"[yellow]获取下载链接: {target}[/yellow]")
                if target.startswith("/"):
                    records = await client.path_to_id(target)
                    if not records:
                        raise ValueError(f"路径不存在: {target}")
                    last = records[-1]
                    if last.get("file_type") == "folder":
                        raise ValueError(f"目标是文件夹: {target}")
                    file_id = last["id"]
                else:
                    file_id = target
                file_info = await get_download_url_with_retry(client, file_id, token_mgr)
                dest = await download_from_file_info(
                    file_info,
                    dest_dir,
                    threads=args.threads,
                    headers=token_mgr.get_headers(),
                    filename=args.filename if len(targets) == 1 else None,
                )
                console.print(f"[green]下载完成:[/green] {dest}")
                return 0
            except (PikpakException, ValueError, OSError, httpx.HTTPError, RuntimeError) as e:
                console.print(f"[red]下载失败 ({target}): {e}[/red]")
                return 1

    results = await asyncio.gather(*[download_one(t) for t in targets])
    errors = sum(results)
    return 1 if errors else 0


async def cmd_quota(args: argparse.Namespace) -> int:
    try:
        client = load_session(args.session)
        await apply_client_defaults(client)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return 1

    await _ensure_token(client)
    info = await client.get_quota_info()
    quota = info.get("quota") or {}
    console.print(f"总空间: {_format_size(quota.get('limit'))}")
    console.print(f"已使用: {_format_size(quota.get('usage'))}")
    console.print(f"回收站: {_format_size(quota.get('usage_in_trash'))}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="PikPak 网盘下载工具 — 账号密码登录、浏览文件、多线程下载",
    )
    parser.add_argument(
        "--session",
        help=f"会话文件路径（默认: ~/.pikpak-downloader/session.json）",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="使用账号密码登录")
    p_login.add_argument("-u", "--username", required=True, help="邮箱 / 手机号 / 用户名")
    p_login.add_argument("-p", "--password", required=True, help="密码")

    p_ls = sub.add_parser("ls", help="列出网盘文件")
    p_ls.add_argument("path", nargs="?", default="/", help="文件夹路径，默认根目录")
    p_ls.add_argument("--limit", type=int, default=100, help="最多显示条数")

    p_dl = sub.add_parser("download", help="下载文件到本地")
    p_dl.add_argument("targets", nargs="+", help="文件 ID 或网盘路径，如 /Movies/a.mp4")
    p_dl.add_argument("-o", "--output", default=".", help="保存目录，默认当前目录")
    p_dl.add_argument("-t", "--threads", type=int, default=12, help="单文件线程数，默认 12（500Mbps 宽带）")
    p_dl.add_argument("-c", "--concurrent", type=int, default=2, help="同时下载文件数，默认 2")
    p_dl.add_argument("-n", "--filename", help="保存文件名（仅单文件时有效）")

    sub.add_parser("quota", help="查看网盘空间")

    sub.add_parser("gui", help="打开图形界面")

    return parser


def cmd_gui(_args: argparse.Namespace) -> int:
    from .gui import run_gui

    run_gui()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "gui":
        return cmd_gui(args)

    commands = {
        "login": cmd_login,
        "ls": cmd_ls,
        "download": cmd_download,
        "quota": cmd_quota,
    }
    return asyncio.run(commands[args.command](args))


if __name__ == "__main__":
    sys.exit(main())
