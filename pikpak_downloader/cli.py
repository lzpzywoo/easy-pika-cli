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

from .aria2 import Aria2Client
from .config import AppConfig
from .download_dispatch import download_file_to_local
from .downloader import download_from_file_info
from .magnets import is_magnet_or_torrent
from .offline_service import (
    add_offline,
    cleanup_cloud,
    list_offline_tasks,
    parse_offline_create_result,
    wait_offline_complete,
)
from .relay import RelayOptions, relay_download_only, relay_magnet
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


def _log(msg: str) -> None:
    console.print(msg)


async def _load_authed_client(session: Optional[str]) -> PikPakApi:
    client = load_session(session)
    await apply_client_defaults(client)
    await retry_api_call(client.refresh_access_token, label="Token 刷新")
    return client


def _backend_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--backend",
        choices=("native", "aria2"),
        default=None,
        help="下载后端：native（内置多线程）或 aria2（JSON-RPC），默认 native",
    )
    parser.add_argument("--aria2-rpc", help="Aria2 RPC URL，默认 http://127.0.0.1:6800/jsonrpc")
    parser.add_argument("--aria2-secret", default="", help="Aria2 RPC secret")


def _resolve_backend(args: argparse.Namespace) -> tuple[str, Optional[Aria2Client]]:
    cfg = AppConfig.from_env(args.session)
    backend = args.backend or cfg.download_backend
    aria2 = None
    if backend == "aria2":
        rpc = args.aria2_rpc or cfg.aria2_rpc_url
        secret = args.aria2_secret if args.aria2_secret else cfg.aria2_rpc_secret
        aria2 = Aria2Client(rpc, secret)
    return backend, aria2


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


async def cmd_ls(args: argparse.Namespace) -> int:
    try:
        client = await _load_authed_client(args.session)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return 1

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


async def cmd_download(args: argparse.Namespace) -> int:
    try:
        client = await _load_authed_client(args.session)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return 1

    dest_dir = Path(args.output).expanduser().resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    targets: List[str] = args.targets
    if not targets:
        console.print("[red]请指定要下载的文件 ID 或路径[/red]")
        return 1

    token_mgr = TokenManager(client, args.session)
    backend, aria2 = _resolve_backend(args)
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

                if backend == "aria2":
                    dest = await download_file_to_local(
                        client,
                        file_id,
                        dest_dir,
                        token_mgr=token_mgr,
                        backend="aria2",
                        aria2=aria2,
                        threads=args.threads,
                        filename=args.filename if len(targets) == 1 else None,
                    )
                else:
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
    return 1 if sum(results) else 0


async def cmd_quota(args: argparse.Namespace) -> int:
    try:
        client = await _load_authed_client(args.session)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return 1

    info = await client.get_quota_info()
    quota = info.get("quota") or {}
    console.print(f"总空间: {_format_size(quota.get('limit'))}")
    console.print(f"已使用: {_format_size(quota.get('usage'))}")
    console.print(f"回收站: {_format_size(quota.get('usage_in_trash'))}")
    return 0


async def cmd_offline_add(args: argparse.Namespace) -> int:
    try:
        client = await _load_authed_client(args.session)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return 1

    url = args.url
    if not is_magnet_or_torrent(url):
        console.print("[red]请提供 magnet: 或 .torrent URL[/red]")
        return 1

    result = await add_offline(client, url, parent_id=args.parent_id, name=args.name)
    task_id, file_id = parse_offline_create_result(result)
    console.print(f"[green]已提交离线下载[/green]")
    console.print(f"  task_id: {task_id}")
    console.print(f"  file_id: {file_id}")
    return 0


async def cmd_offline_list(args: argparse.Namespace) -> int:
    try:
        client = await _load_authed_client(args.session)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return 1

    phase_map = {
        "all": None,
        "running": ["PHASE_TYPE_RUNNING", "PHASE_TYPE_PENDING"],
        "complete": ["PHASE_TYPE_COMPLETE"],
        "error": ["PHASE_TYPE_ERROR"],
    }
    tasks = await list_offline_tasks(client, phases=phase_map.get(args.phase))

    table = Table(title="PikPak 离线任务")
    table.add_column("状态")
    table.add_column("名称")
    table.add_column("task_id", style="dim")
    table.add_column("file_id", style="dim")

    for t in tasks:
        table.add_row(t.phase, t.name, t.task_id, t.file_id)
    console.print(table)
    return 0


async def cmd_offline_wait(args: argparse.Namespace) -> int:
    try:
        client = await _load_authed_client(args.session)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return 1

    try:
        task = await wait_offline_complete(
            client,
            args.task_id,
            args.file_id,
            timeout=args.timeout,
            poll_interval=args.interval,
            on_log=_log,
        )
        console.print(f"[green]完成[/green] {task.name} ({task.file_id})")
        return 0
    except (TimeoutError, RuntimeError) as e:
        console.print(f"[red]{e}[/red]")
        return 1


def _relay_options_from_args(args: argparse.Namespace) -> RelayOptions:
    cfg = AppConfig.from_env(args.session)
    backend, aria2 = _resolve_backend(args)
    return RelayOptions(
        upload=getattr(args, "upload", True),
        wait=getattr(args, "wait", True),
        download=getattr(args, "download", True),
        cleanup=getattr(args, "cleanup", cfg.relay_cleanup_cloud),
        cleanup_forever=not getattr(args, "trash_only", False),
        dest_dir=Path(getattr(args, "output", ".") or ".").expanduser().resolve(),
        backend=backend,  # type: ignore[arg-type]
        aria2=aria2,
        threads=getattr(args, "threads", 12),
        parent_id=getattr(args, "parent_id", None),
        timeout=getattr(args, "timeout", cfg.relay_timeout),
        poll_interval=getattr(args, "interval", cfg.relay_poll_interval),
    )


async def cmd_relay_run(args: argparse.Namespace) -> int:
    try:
        client = await _load_authed_client(args.session)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return 1

    magnets = args.magnets or []
    if not magnets:
        console.print("[red]请提供磁链[/red]")
        return 1

    token_mgr = TokenManager(client, args.session)
    opts = _relay_options_from_args(args)
    errors = 0
    for m in magnets:
        try:
            result = await relay_magnet(client, m, token_mgr, opts, on_log=_log)
            console.print(f"[green]中转完成[/green] files={len(result.file_ids)} cleaned={result.cleaned}")
        except Exception as e:
            console.print(f"[red]中转失败 ({m[:40]}...): {e}[/red]")
            errors += 1
    return 1 if errors else 0


async def cmd_relay_upload(args: argparse.Namespace) -> int:
    args.upload, args.wait, args.download, args.cleanup = True, False, False, False
    return await cmd_relay_run(args)


async def cmd_relay_download(args: argparse.Namespace) -> int:
    try:
        client = await _load_authed_client(args.session)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return 1

    token_mgr = TokenManager(client, args.session)
    opts = _relay_options_from_args(args)
    opts.upload = opts.wait = False
    opts.download = True
    opts.cleanup = args.cleanup

    errors = 0
    for fid in args.file_ids:
        try:
            result = await relay_download_only(client, fid, token_mgr, opts, on_log=_log)
            console.print(f"[green]下载完成[/green] {len(result.local_paths)} 个文件")
        except Exception as e:
            console.print(f"[red]失败 {fid}: {e}[/red]")
            errors += 1
    return 1 if errors else 0


async def cmd_relay_cleanup(args: argparse.Namespace) -> int:
    try:
        client = await _load_authed_client(args.session)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return 1

    await cleanup_cloud(
        client,
        args.file_ids,
        delete_forever=not args.trash_only,
        task_ids=args.task_ids or [],
        on_log=_log,
    )
    return 0


def cmd_telegram(args: argparse.Namespace) -> int:
    from .telegram_bot import run_telegram_bot

    cfg = AppConfig.from_env(args.session)
    if args.token:
        import os
        os.environ["TELEGRAM_BOT_TOKEN"] = args.token
        cfg = AppConfig.from_env(args.session)
    try:
        run_telegram_bot(cfg)
    except KeyboardInterrupt:
        console.print("[yellow]Telegram bot 已停止[/yellow]")
    except Exception as e:
        console.print(f"[red]{e}[/red]")
        return 1
    return 0


def cmd_ai_serve(args: argparse.Namespace) -> int:
    from .ai_api import run_ai_server

    cfg = AppConfig.from_env(args.session)
    if args.host:
        import os
        os.environ["AI_API_HOST"] = args.host
    if args.port:
        import os
        os.environ["AI_API_PORT"] = str(args.port)
    if args.api_key:
        import os
        os.environ["AI_API_KEY"] = args.api_key
    cfg = AppConfig.from_env(args.session)
    try:
        run_ai_server(cfg)
    except KeyboardInterrupt:
        console.print("[yellow]API 服务已停止[/yellow]")
    except Exception as e:
        console.print(f"[red]{e}[/red]")
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="easy-pika-cli — PikPak 下载 / 离线上传 / 中转 / Telegram / AI API",
    )
    parser.add_argument(
        "--session",
        help="会话文件路径（默认: ~/.easy-pika-cli/session.json）",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="使用账号密码登录")
    p_login.add_argument("-u", "--username", required=True)
    p_login.add_argument("-p", "--password", required=True)

    p_ls = sub.add_parser("ls", help="列出网盘文件")
    p_ls.add_argument("path", nargs="?", default="/")
    p_ls.add_argument("--limit", type=int, default=100)

    p_dl = sub.add_parser("download", help="下载文件到本地")
    p_dl.add_argument("targets", nargs="+")
    p_dl.add_argument("-o", "--output", default=".")
    p_dl.add_argument("-t", "--threads", type=int, default=12)
    p_dl.add_argument("-c", "--concurrent", type=int, default=2)
    p_dl.add_argument("-n", "--filename")
    _backend_args(p_dl)

    sub.add_parser("quota", help="查看网盘空间")

    # offline
    p_off = sub.add_parser("offline", help="PikPak 离线下载（磁链上传）")
    off_sub = p_off.add_subparsers(dest="offline_cmd", required=True)

    p_off_add = off_sub.add_parser("add", help="提交磁链到 PikPak")
    p_off_add.add_argument("url", help="magnet: 或 .torrent URL")
    p_off_add.add_argument("--parent-id", help="目标文件夹 ID")
    p_off_add.add_argument("--name", help="自定义名称")

    p_off_list = off_sub.add_parser("list", help="列出离线任务")
    p_off_list.add_argument(
        "--phase",
        choices=("all", "running", "complete", "error"),
        default="all",
    )

    p_off_wait = off_sub.add_parser("wait", help="等待离线任务完成")
    p_off_wait.add_argument("task_id")
    p_off_wait.add_argument("file_id")
    p_off_wait.add_argument("--timeout", type=float, default=7200)
    p_off_wait.add_argument("--interval", type=float, default=10)

    # relay
    p_relay = sub.add_parser("relay", help="PikPak 中转：磁链 → 云端 → 本地下载 → 清理")
    relay_sub = p_relay.add_subparsers(dest="relay_cmd", required=True)

    def _relay_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("-o", "--output", default="./downloads")
        p.add_argument("-t", "--threads", type=int, default=12)
        p.add_argument("--timeout", type=float, default=7200)
        p.add_argument("--interval", type=float, default=10)
        p.add_argument("--parent-id")
        _backend_args(p)

    p_relay_run = relay_sub.add_parser("run", help="完整中转流程")
    p_relay_run.add_argument("magnets", nargs="+")
    _relay_common(p_relay_run)
    p_relay_run.add_argument("--no-cleanup", action="store_true", help="下载后保留网盘文件")
    p_relay_run.add_argument("--trash-only", action="store_true", help="清理时仅移入回收站")

    p_relay_up = relay_sub.add_parser("upload", help="仅提交磁链（离线上传）")
    p_relay_up.add_argument("magnets", nargs="+")
    p_relay_up.add_argument("--parent-id")

    p_relay_dl = relay_sub.add_parser("download", help="仅下载已有 file_id")
    p_relay_dl.add_argument("file_ids", nargs="+")
    _relay_common(p_relay_dl)
    p_relay_dl.add_argument("--cleanup", action="store_true", help="下载后清理网盘")

    p_relay_cl = relay_sub.add_parser("cleanup", help="清理网盘文件/离线任务")
    p_relay_cl.add_argument("file_ids", nargs="+")
    p_relay_cl.add_argument("--task-ids", nargs="*", default=[])
    p_relay_cl.add_argument("--trash-only", action="store_true")

    p_tg = sub.add_parser("telegram", help="运行 Telegram 机器人（磁链中转）")
    p_tg.add_argument("--token", help="Bot Token（或设 TELEGRAM_BOT_TOKEN）")

    p_ai = sub.add_parser("ai", help="AI / 自动化 HTTP API")
    ai_sub = p_ai.add_subparsers(dest="ai_cmd", required=True)
    p_ai_serve = ai_sub.add_parser("serve", help="启动 HTTP API 服务")
    p_ai_serve.add_argument("--host", default=None)
    p_ai_serve.add_argument("--port", type=int, default=None)
    p_ai_serve.add_argument("--api-key", help="API 鉴权密钥（或设 AI_API_KEY）")

    sub.add_parser("gui", help="打开图形界面（需安装 customtkinter）")

    return parser


def cmd_gui(_args: argparse.Namespace) -> int:
    try:
        from .gui import run_gui
    except ImportError as e:
        console.print(f"[red]GUI 需要 customtkinter: pip install customtkinter[/red]")
        console.print(f"[dim]{e}[/dim]")
        return 1
    run_gui()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "gui":
        return cmd_gui(args)
    if args.command == "telegram":
        return cmd_telegram(args)
    if args.command == "ai" and args.ai_cmd == "serve":
        return cmd_ai_serve(args)

    # relay flags
    if args.command == "relay":
        if args.relay_cmd == "run":
            args.upload = args.wait = args.download = True
            args.cleanup = not getattr(args, "no_cleanup", False)
        elif args.relay_cmd == "upload":
            return asyncio.run(cmd_relay_upload(args))
        elif args.relay_cmd == "download":
            return asyncio.run(cmd_relay_download(args))
        elif args.relay_cmd == "cleanup":
            return asyncio.run(cmd_relay_cleanup(args))

    if args.command == "offline":
        offline_cmds = {
            "add": cmd_offline_add,
            "list": cmd_offline_list,
            "wait": cmd_offline_wait,
        }
        return asyncio.run(offline_cmds[args.offline_cmd](args))

    commands = {
        "login": cmd_login,
        "ls": cmd_ls,
        "download": cmd_download,
        "quota": cmd_quota,
        "relay": cmd_relay_run,
    }
    return asyncio.run(commands[args.command](args))


if __name__ == "__main__":
    sys.exit(main())
