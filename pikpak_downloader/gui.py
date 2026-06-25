"""PikPak Download — desktop GUI (CustomTkinter)."""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk
from pikpakapi import PikPakApi
from pikpakapi.PikpakException import PikpakException

from .api_helpers import get_client_kwargs, retry_api_call
from .download_manager import DownloadJob, DownloadOrchestrator
from .downloader import MAX_HTTP_CONCURRENCY
from .session import disk_free_gb, get_session_path, load_session_async, save_session

BG = "#0f0f0f"
SIDEBAR = "#141414"
CARD = "#1a1a1a"
ELEVATED = "#222222"
BORDER = "#2e2e2e"
TEXT = "#f0f0f0"
TEXT_MUTED = "#8a8a8a"
ACCENT = "#f5c518"
ACCENT_HOVER = "#e6b800"
ACCENT_FG = "#0f0f0f"
SUCCESS = "#4caf50"
DANGER = "#e04545"
WARN = "#ff9800"

FONT = ("Segoe UI", 12)
FONT_SM = ("Segoe UI", 11)
FONT_HEAD = ("Segoe UI", 14, "bold")
FONT_TITLE = ("Segoe UI", 22, "bold")
FONT_MONO = ("Consolas", 10)

CONCURRENT_OPTIONS = ["1", "2", "3", "4", "6"]
CONNECTION_OPTIONS = ["4", "6", "8"]
DEFAULT_CONNECTIONS = 8

STATUS_ZH = {
    "queued": "排队中",
    "linking": "获取链接",
    "downloading": "下载中",
    "merging": "合并中",
    "done": "完成",
    "failed": "失败",
    "cancelled": "已取消",
    "retrying": "续传重试",
    "paused": "已暂停",
}


def format_size(value: str | int | None) -> str:
    if value in (None, "", "0"):
        return "-"
    size = int(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def format_speed(bps: float) -> str:
    """bytes/s -> human readable, with decimal Mbps for comparison with Task Manager."""
    if bps <= 0:
        return "-"
    mib = bps / (1024 * 1024)
    mbps = bps * 8 / 1_000_000
    if mib >= 1:
        return f"{mib:.1f} MB/s ({mbps:.0f} Mbps)"
    kib = bps / 1024
    if kib >= 1:
        return f"{kib:.0f} KB/s ({mbps:.1f} Mbps)"
    return f"{int(bps)} B/s"


def format_duration(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    s = int(seconds)
    if s < 60:
        return f"{s}秒"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}分{s}秒"
    h, m = divmod(m, 60)
    return f"{h}时{m}分"


def is_folder(item: dict) -> bool:
    return "folder" in (item.get("kind") or "")


def parse_connection_count(value: str) -> int:
    try:
        return max(1, min(8, int((value or str(DEFAULT_CONNECTIONS)).strip())))
    except ValueError:
        return DEFAULT_CONNECTIONS


@dataclass
class BrowseState:
    path: str = "/"
    parent_id: str | None = None
    files: list[dict] = field(default_factory=list)


class PikPakGui:
    def __init__(self) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.root = ctk.CTk()
        self.root.title("PikPak Download")
        self.root.minsize(1000, 640)
        self.root.geometry("1200x820")
        self.root.configure(fg_color=BG)

        self.loop = asyncio.new_event_loop()
        self.ui_queue: queue.Queue = queue.Queue()
        self.session_path = get_session_path()

        self.client: PikPakApi | None = None
        self.orchestrator: DownloadOrchestrator | None = None
        self.browse = BrowseState()
        self.logged_in = False
        self._list_loading = False
        self._dl_rows: dict[str, dict] = {}
        self._dl_iid_to_job: dict[str, str] = {}  # job_id -> {iid, speed, ...}
        self._log_history: list[str] = []

        self._build_ui()
        threading.Thread(target=self._run_loop, daemon=True).start()
        self.root.after(100, self._poll)
        self.root.after(500, self._tick_times)
        self._run_async(self._try_restore())

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _run_async(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                handlers = {
                    "log": self._append_log,
                    "login_ok": self._on_login_ok,
                    "login_fail": self._on_login_fail,
                    "browse": self._apply_browse,
                    "quota": self._set_quota,
                    "dl_add": self._dl_add_row,
                    "dl_update": self._dl_update_row,
                    "error": lambda m: messagebox.showerror("错误", m, parent=self.root),
                }
                fn = handlers.get(kind)
                if fn:
                    fn(payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _log(self, msg: str) -> None:
        self.ui_queue.put(("log", msg))

    def _sync_login_state(self) -> None:
        if self.logged_in:
            self.refresh_btn.configure(state="normal" if not self._list_loading else "disabled")
            self.download_btn.configure(state="normal")
            self.up_btn.configure(state="normal")
            self.login_btn.configure(state="disabled")
            self.user_entry.configure(state="disabled")
            self.pass_entry.configure(state="disabled")
        else:
            self.refresh_btn.configure(state="disabled")
            self.download_btn.configure(state="disabled")
            self.up_btn.configure(state="disabled")
            self.login_btn.configure(state="normal")
            self.user_entry.configure(state="normal")
            self.pass_entry.configure(state="normal")

    def _build_ui(self) -> None:
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_main()
        self._build_log_bar()

    def _card(self, parent, **kw) -> ctk.CTkFrame:
        return ctk.CTkFrame(parent, fg_color=CARD, corner_radius=10, **kw)

    def _build_sidebar(self) -> None:
        side = ctk.CTkFrame(self.root, width=270, fg_color=SIDEBAR, corner_radius=0)
        side.grid(row=0, column=0, sticky="nsew")
        side.grid_propagate(False)

        ctk.CTkLabel(side, text="PikPak", font=FONT_TITLE, text_color=ACCENT, anchor="w").pack(
            fill="x", padx=20, pady=(22, 0),
        )
        ctk.CTkLabel(side, text="Download", font=FONT_SM, text_color=TEXT_MUTED, anchor="w").pack(
            fill="x", padx=20, pady=(0, 12),
        )
        ctk.CTkFrame(side, height=1, fg_color=BORDER).pack(fill="x", padx=16, pady=8)

        login = self._card(side)
        login.pack(fill="x", padx=14, pady=6)
        li = ctk.CTkFrame(login, fg_color="transparent")
        li.pack(fill="x", padx=12, pady=12)

        ctk.CTkLabel(li, text="账号", font=FONT_SM, text_color=TEXT_MUTED, anchor="w").pack(fill="x")
        self.user_var = ctk.StringVar()
        self.user_entry = ctk.CTkEntry(
            li, textvariable=self.user_var, placeholder_text="邮箱 / 手机 / 用户名",
            fg_color=ELEVATED, border_color=BORDER, height=36,
        )
        self.user_entry.pack(fill="x", pady=(4, 10))

        ctk.CTkLabel(li, text="密码", font=FONT_SM, text_color=TEXT_MUTED, anchor="w").pack(fill="x")
        self.pass_var = ctk.StringVar()
        self.pass_entry = ctk.CTkEntry(
            li, textvariable=self.pass_var, show="•", placeholder_text="密码",
            fg_color=ELEVATED, border_color=BORDER, height=36,
        )
        self.pass_entry.pack(fill="x", pady=(4, 10))
        self.pass_entry.bind("<Return>", lambda _e: self._on_login())

        brow = ctk.CTkFrame(li, fg_color="transparent")
        brow.pack(fill="x")
        self.login_btn = ctk.CTkButton(
            brow, text="登录", width=90, height=36,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ACCENT_FG,
            font=("Segoe UI", 12, "bold"), command=self._on_login,
        )
        self.login_btn.pack(side="left")
        ctk.CTkButton(
            brow, text="退出", width=64, height=36,
            fg_color=ELEVATED, hover_color=BORDER, command=self._on_logout,
        ).pack(side="left", padx=(8, 0))

        self.status_label = ctk.CTkLabel(li, text="未登录", font=FONT_SM, text_color=TEXT_MUTED, anchor="w")
        self.status_label.pack(fill="x", pady=(10, 0))

        opts = self._card(side)
        opts.pack(fill="x", padx=14, pady=6)
        oi = ctk.CTkFrame(opts, fg_color="transparent")
        oi.pack(fill="x", padx=12, pady=12)
        ctk.CTkLabel(oi, text="下载设置", font=FONT_HEAD, anchor="w").pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(oi, text="保存目录", font=FONT_SM, text_color=TEXT_MUTED, anchor="w").pack(fill="x")
        pr = ctk.CTkFrame(oi, fg_color="transparent")
        pr.pack(fill="x", pady=(4, 10))
        self.out_var = ctk.StringVar(value=str(Path.home() / "Downloads"))
        ctk.CTkEntry(pr, textvariable=self.out_var, fg_color=ELEVATED, border_color=BORDER, height=32).pack(
            side="left", fill="x", expand=True,
        )
        ctk.CTkButton(pr, text="浏览", width=56, height=32, fg_color=ELEVATED, hover_color=BORDER,
                      command=self._pick_dir).pack(side="left", padx=(6, 0))

        ctk.CTkLabel(oi, text="并行连接数", font=FONT_SM, text_color=TEXT_MUTED, anchor="w").pack(fill="x")
        self.connection_combo = ctk.CTkComboBox(
            oi, values=CONNECTION_OPTIONS, width=200, height=32,
            fg_color=ELEVATED, border_color=BORDER, button_color=BORDER,
            dropdown_fg_color=CARD, state="readonly",
        )
        self.connection_combo.set(str(DEFAULT_CONNECTIONS))
        self.connection_combo.pack(anchor="w", pady=(4, 0))
        ctk.CTkLabel(
            oi, text="128MB 大块 + 多连接，兼顾速度与稳定",
            font=FONT_SM, text_color=TEXT_MUTED, anchor="w", wraplength=220,
        ).pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(oi, text="同时下载数（>1 分摊带宽）", font=FONT_SM, text_color=TEXT_MUTED, anchor="w").pack(fill="x")
        self.concurrent_combo = ctk.CTkComboBox(
            oi, values=CONCURRENT_OPTIONS, width=200, height=32,
            fg_color=ELEVATED, border_color=BORDER, button_color=BORDER,
            dropdown_fg_color=CARD, state="readonly",
        )
        self.concurrent_combo.set("1")
        self.concurrent_combo.pack(anchor="w", pady=(4, 0))

        quota = self._card(side)
        quota.pack(fill="x", padx=14, pady=6)
        qi = ctk.CTkFrame(quota, fg_color="transparent")
        qi.pack(fill="x", padx=12, pady=12)
        ctk.CTkLabel(qi, text="网盘空间", font=FONT_HEAD, anchor="w").pack(fill="x")
        self.quota_bar = ctk.CTkProgressBar(qi, height=10, progress_color=ACCENT, fg_color=ELEVATED)
        self.quota_bar.pack(fill="x", pady=(10, 6))
        self.quota_bar.set(0)
        self.quota_label = ctk.CTkLabel(qi, text="—", font=FONT_SM, text_color=TEXT_MUTED, anchor="w", justify="left")
        self.quota_label.pack(fill="x")
        ctk.CTkButton(qi, text="刷新", height=28, width=64, fg_color=ELEVATED, hover_color=BORDER,
                      command=lambda: self._run_async(self._fetch_quota())).pack(anchor="w", pady=(8, 0))

    def _build_main(self) -> None:
        main = ctk.CTkFrame(self.root, fg_color=BG, corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=2)
        main.grid_rowconfigure(3, weight=2)

        bar = self._card(main)
        bar.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 8))
        bar.grid_columnconfigure(1, weight=1)

        self.up_btn = ctk.CTkButton(bar, text="↑ 上级", width=76, height=34, fg_color=ELEVATED,
                                    hover_color=BORDER, command=self._go_up, state="disabled")
        self.up_btn.grid(row=0, column=0, padx=(12, 8), pady=10)

        self.path_var = ctk.StringVar(value="/")
        ctk.CTkEntry(bar, textvariable=self.path_var, height=34, font=FONT_MONO,
                     fg_color=ELEVATED, border_color=BORDER).grid(row=0, column=1, sticky="ew", pady=10)

        self.refresh_btn = ctk.CTkButton(bar, text="刷新", width=68, height=34, fg_color=ELEVATED,
                                         hover_color=BORDER, command=self._refresh, state="disabled")
        self.refresh_btn.grid(row=0, column=2, padx=8, pady=10)

        self.download_btn = ctk.CTkButton(
            bar, text="⬇ 加入下载", width=110, height=34,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=ACCENT_FG,
            font=("Segoe UI", 12, "bold"), command=self._on_download, state="disabled",
        )
        self.download_btn.grid(row=0, column=3, padx=(0, 12), pady=10)

        files_outer = self._card(main)
        files_outer.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 8))
        files_outer.grid_columnconfigure(0, weight=1)
        files_outer.grid_rowconfigure(1, weight=1)

        fh = ctk.CTkFrame(files_outer, fg_color="transparent")
        fh.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        ctk.CTkLabel(fh, text="网盘文件", font=FONT_HEAD).pack(side="left")
        self.count_label = ctk.CTkLabel(fh, text="", font=FONT_SM, text_color=TEXT_MUTED)
        self.count_label.pack(side="right")

        tf = ctk.CTkFrame(files_outer, fg_color=ELEVATED, corner_radius=6)
        tf.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        tf.grid_columnconfigure(0, weight=1)
        tf.grid_rowconfigure(0, weight=1)
        self._setup_file_tree(tf)

        dl_outer = self._card(main)
        dl_outer.grid(row=3, column=0, sticky="nsew", padx=14, pady=(0, 14))
        dl_outer.grid_columnconfigure(0, weight=1)
        dl_outer.grid_rowconfigure(1, weight=1)

        dh = ctk.CTkFrame(dl_outer, fg_color="transparent")
        dh.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        ctk.CTkLabel(dh, text="下载队列", font=FONT_HEAD).pack(side="left")
        self.dl_summary = ctk.CTkLabel(dh, text="无任务", font=FONT_SM, text_color=TEXT_MUTED)
        self.dl_summary.pack(side="right")
        ctk.CTkButton(dh, text="继续选中", width=90, height=28, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      text_color=ACCENT_FG, command=self._resume_selected).pack(side="right", padx=(0, 8))
        ctk.CTkButton(dh, text="暂停选中", width=90, height=28, fg_color=ELEVATED, hover_color=WARN,
                      command=self._pause_selected).pack(side="right", padx=(0, 8))
        ctk.CTkButton(dh, text="取消选中", width=90, height=28, fg_color=ELEVATED, hover_color=DANGER,
                      command=self._cancel_selected).pack(side="right", padx=(0, 8))
        ctk.CTkButton(dh, text="清空已完成", width=90, height=28, fg_color=ELEVATED, hover_color=BORDER,
                      command=self._clear_finished).pack(side="right", padx=(0, 12))

        dtf = ctk.CTkFrame(dl_outer, fg_color=ELEVATED, corner_radius=6)
        dtf.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        dtf.grid_columnconfigure(0, weight=1)
        dtf.grid_rowconfigure(0, weight=1)
        self._setup_dl_tree(dtf)

    def _tree_style(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Pik.Treeview", background=ELEVATED, fieldbackground=ELEVATED, foreground=TEXT,
            borderwidth=0, rowheight=28, font=FONT,
        )
        style.configure(
            "Pik.Treeview.Heading", background=CARD, foreground=TEXT_MUTED,
            borderwidth=0, font=("Segoe UI", 11, "bold"),
        )
        style.map("Pik.Treeview", background=[("selected", "#3d3500")], foreground=[("selected", ACCENT)])

    def _setup_file_tree(self, parent) -> None:
        self._tree_style()
        cols = ("type", "name", "size", "id")
        self.tree = ttk.Treeview(parent, columns=cols, show="headings", selectmode="extended", style="Pik.Treeview")
        self.tree.heading("type", text="类型")
        self.tree.heading("name", text="名称")
        self.tree.heading("size", text="大小")
        self.tree.heading("id", text="ID")
        self.tree.column("type", width=72, stretch=False)
        self.tree.column("name", width=320, stretch=True)
        self.tree.column("size", width=90, stretch=False)
        self.tree.column("id", width=160, stretch=False)
        vsb = ttk.Scrollbar(parent, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Double-1>", self._on_double_click)

    def _setup_dl_tree(self, parent) -> None:
        cols = ("name", "status", "progress", "speed", "time")
        self.dl_tree = ttk.Treeview(parent, columns=cols, show="headings", selectmode="extended", style="Pik.Treeview")
        self.dl_tree.heading("name", text="文件名")
        self.dl_tree.heading("status", text="状态")
        self.dl_tree.heading("progress", text="进度")
        self.dl_tree.heading("speed", text="速度")
        self.dl_tree.heading("time", text="等待/用时")
        self.dl_tree.column("name", width=340, stretch=True)
        self.dl_tree.column("status", width=80, stretch=False)
        self.dl_tree.column("progress", width=120, stretch=False)
        self.dl_tree.column("speed", width=180, stretch=False)
        self.dl_tree.column("time", width=100, stretch=False)
        vsb = ttk.Scrollbar(parent, orient="vertical", command=self.dl_tree.yview)
        self.dl_tree.configure(yscrollcommand=vsb.set)
        self.dl_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

    def _build_log_bar(self) -> None:
        log_outer = ctk.CTkFrame(self.root, fg_color=SIDEBAR, corner_radius=0, height=100)
        log_outer.grid(row=1, column=0, columnspan=2, sticky="ew")
        log_outer.grid_propagate(False)
        log_outer.grid_columnconfigure(0, weight=1)
        log_outer.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(log_outer, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=14, pady=(8, 0))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header, text="日志", font=FONT_SM, text_color=TEXT_MUTED, anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            header,
            text="复制详细日志",
            width=110,
            height=26,
            font=FONT_SM,
            fg_color=ELEVATED,
            hover_color=BORDER,
            command=self._copy_detailed_log,
        ).grid(row=0, column=1, sticky="e")

        self.log_box = ctk.CTkTextbox(log_outer, height=64, fg_color=CARD, text_color=TEXT_MUTED, font=FONT_MONO)
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=14, pady=(4, 10))
        self.log_box.configure(state="disabled")

    def _append_log(self, line: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self._log_history.append(f"[{stamp}] {line}")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _build_detailed_log_text(self) -> str:
        lines = [
            "PikPak Download 详细日志",
            f"导出时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "=== 环境 ===",
            f"登录状态: {'已登录' if self.logged_in else '未登录'}",
            f"保存目录: {self.out_var.get().strip() or '-'}",
            f"并行连接: {self.connection_combo.get()}",
            f"同时下载: {self.concurrent_combo.get()}",
            f"浏览路径: {self.browse.path}",
            f"会话文件: {self.session_path}",
        ]
        out = self.out_var.get().strip()
        if out:
            free = disk_free_gb(Path(out))
            if free is not None:
                lines.append(f"保存盘剩余空间: {free:.1f} GB")
        sess_free = disk_free_gb(self.session_path)
        if sess_free is not None:
            lines.append(f"系统盘(C:)剩余空间: {sess_free:.1f} GB  (会话/Token 写在此盘)")
        lines.extend([
            "",
            "=== 下载队列 ===",
        ])

        if not self._dl_rows:
            lines.append("(无任务)")
        else:
            for job_id, row in self._dl_rows.items():
                name = row.get("name", "-")
                st = row.get("status", "-")
                done = row.get("done", 0)
                total = row.get("total", 0)
                pct = f"{min(done * 100 // total, 100)}%" if total > 0 else "-"
                speed = row.get("speed_ema", 0.0)
                speed_txt = format_speed(speed) if speed > 0 else "-"
                lines.append(f"[{job_id}] {name}")
                lines.append(f"  状态: {STATUS_ZH.get(st, st)} ({st})")
                lines.append(f"  进度: {format_size(done)} / {format_size(total)} ({pct})")
                lines.append(f"  速度: {speed_txt}")
                lines.append(f"  用时: {self._format_job_time(row)}")
                job = self.orchestrator.jobs.get(job_id) if self.orchestrator else None
                if job:
                    lines.append(f"  file_id: {job.file_id}")
                    if job.error:
                        lines.append(f"  错误: {job.error}")
                lines.append("")

        lines.extend(["=== 日志记录 ==="])
        if self._log_history:
            lines.extend(self._log_history)
        else:
            visible = self.log_box.get("1.0", "end").strip()
            lines.append(visible or "(无日志)")

        return "\n".join(lines)

    def _copy_detailed_log(self) -> None:
        text = self._build_detailed_log_text()
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()
            self._log("详细日志已复制到剪贴板")
        except Exception as exc:
            messagebox.showerror("复制失败", str(exc), parent=self.root)

    # ── orchestrator ─────────────────────────────────────────────────────────

    def _ensure_orchestrator(self) -> DownloadOrchestrator:
        if not self.client:
            raise RuntimeError("未登录")
        dest = Path(self.out_var.get().strip() or str(Path.home() / "Downloads"))
        threads = min(parse_connection_count(self.connection_combo.get()), MAX_HTTP_CONCURRENCY)
        concurrent = int(self.concurrent_combo.get())

        if self.orchestrator is None:
            self.orchestrator = DownloadOrchestrator(
                self.client,
                str(self.session_path),
                dest,
                threads_per_file=threads,
                max_concurrent=concurrent,
                on_status=self._orch_status,
                on_progress=self._orch_progress,
                on_log=self._log,
            )
            self._run_async(self.orchestrator.start())
        else:
            self.orchestrator.set_dest_dir(dest)
            self.orchestrator.set_threads_per_file(threads)
            self.orchestrator.set_max_concurrent(concurrent)
            self._run_async(self.orchestrator.ensure_workers())

        return self.orchestrator

    def _orch_status(self, job_id: str, status: str) -> None:
        self.ui_queue.put(("dl_update", {"job_id": job_id, "status": status}))

    def _orch_progress(self, job_id: str, done: int, total: int, speed: float) -> None:
        self.ui_queue.put(("dl_update", {
            "job_id": job_id, "done": done, "total": total, "speed": speed,
        }))

    def _update_summary(self) -> None:
        if not self.orchestrator:
            active = sum(1 for r in self._dl_rows.values() if r.get("status") in ("linking", "downloading", "merging", "retrying"))
            queued = sum(1 for r in self._dl_rows.values() if r.get("status") == "queued")
            paused = sum(1 for r in self._dl_rows.values() if r.get("status") == "paused")
            done = sum(1 for r in self._dl_rows.values() if r.get("status") == "done")
            failed = sum(1 for r in self._dl_rows.values() if r.get("status") == "failed")
        else:
            active = self.orchestrator.active_count
            queued = self.orchestrator.queued_count
            paused = self.orchestrator.paused_count
            done = self.orchestrator.done_count
            failed = self.orchestrator.failed_count

        parts = []
        if active:
            parts.append(f"{active} 下载中")
        if queued:
            parts.append(f"{queued} 排队")
        if paused:
            parts.append(f"{paused} 已暂停")
        if done:
            parts.append(f"{done} 完成")
        if failed:
            parts.append(f"{failed} 失败")
        cancelled = (
            self.orchestrator.cancelled_count if self.orchestrator
            else sum(1 for r in self._dl_rows.values() if r.get("status") == "cancelled")
        )
        if cancelled:
            parts.append(f"{cancelled} 已取消")
        total_speed = sum(
            r.get("speed_ema", 0.0)
            for r in self._dl_rows.values()
            if r.get("status") == "downloading"
        )
        if total_speed > 0:
            parts.append(f"合计 {format_speed(total_speed)}")
        self.dl_summary.configure(text=" · ".join(parts) if parts else "无任务")

    # ── auth ─────────────────────────────────────────────────────────────────

    async def _try_restore(self) -> None:
        if not self.session_path.exists():
            self._log("无已保存会话，请先登录。")
            return
        try:
            self._log("正在恢复会话…")
            client = await load_session_async(str(self.session_path))
            await client.refresh_access_token()
            self.client = client
            self.ui_queue.put(("login_ok", {"restored": True}))
            await self._fetch_quota()
            await self._browse_path(self.browse.path)
        except Exception as exc:
            self._log(f"会话恢复失败: {exc}")

    def _on_login(self) -> None:
        u, p = self.user_var.get().strip(), self.pass_var.get()
        if not u or not p:
            messagebox.showwarning("提示", "请输入账号和密码", parent=self.root)
            return
        self.login_btn.configure(state="disabled")
        self.status_label.configure(text="登录中…")
        self._run_async(self._do_login(u, p))

    async def _do_login(self, username: str, password: str) -> None:
        try:
            client = PikPakApi(username=username, password=password, **get_client_kwargs())
            await client.login()
            await client.refresh_access_token()
            path = save_session(client, str(self.session_path))
            self.client = client
            self.orchestrator = None
            self._log(f"登录成功 → {path}")
            self.ui_queue.put(("login_ok", {"restored": False}))
            await self._fetch_quota()
            await self._browse_path("/")
        except PikpakException as exc:
            self.ui_queue.put(("login_fail", str(exc)))
        except Exception as exc:
            self.ui_queue.put(("login_fail", str(exc)))

    def _on_login_ok(self, payload: dict) -> None:
        self.logged_in = True
        self.pass_var.set("")
        uid = (self.client.user_id if self.client else "") or "—"
        short = uid[:14] + "…" if len(uid) > 14 else uid
        prefix = "会话已恢复" if payload.get("restored") else "已登录"
        self.status_label.configure(text=f"{prefix} · {short}", text_color=ACCENT)
        self._sync_login_state()
        self._warn_low_system_disk()

    def _warn_low_system_disk(self) -> None:
        free = disk_free_gb(self.session_path)
        if free is not None and free < 5:
            self._log(
                f"警告: 系统盘 (C:) 仅剩 {free:.1f} GB，"
                f"Token 会话保存在 {self.session_path}，空间不足可能导致刷新失败"
            )

    def _on_login_fail(self, msg: str) -> None:
        self.status_label.configure(text="登录失败", text_color=DANGER)
        self._log(f"登录失败: {msg}")
        self._sync_login_state()
        messagebox.showerror("登录失败", msg, parent=self.root)

    def _on_logout(self) -> None:
        if self.orchestrator and self.orchestrator.active_count > 0:
            if not messagebox.askyesno(
                "确认", "仍有下载进行中，退出将停止后续排队任务。确定退出？",
                parent=self.root,
            ):
                return
        if self.orchestrator:
            self._run_async(self.orchestrator.stop())
            self.orchestrator = None
        self.client = None
        self.logged_in = False
        self.browse = BrowseState()
        self.status_label.configure(text="未登录", text_color=TEXT_MUTED)
        self.quota_bar.set(0)
        self.quota_label.configure(text="—")
        for row in self.tree.get_children():
            self.tree.delete(row)
        self.count_label.configure(text="")
        self._sync_login_state()
        self._log("已退出")

    # ── browse ───────────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        if self.logged_in:
            self._run_async(self._browse_path(self.browse.path))

    def _go_up(self) -> None:
        if not self.logged_in or self.browse.path == "/":
            return
        parts = [p for p in self.browse.path.split("/") if p]
        if not parts:
            return
        parent = "/" + "/".join(parts[:-1]) if len(parts) > 1 else "/"
        self._run_async(self._browse_path(parent))

    def _on_double_click(self, _event) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        idx = self.tree.index(sel[0])
        if idx < 0 or idx >= len(self.browse.files):
            return
        item = self.browse.files[idx]
        if not is_folder(item):
            return
        base = self.browse.path.rstrip("/")
        name = item.get("name", "")
        new_path = f"{base}/{name}" if base else f"/{name}"
        self._run_async(self._browse_path(new_path))

    async def _browse_path(self, path: str) -> None:
        if not self.client:
            return
        self._list_loading = True
        self._sync_login_state()
        try:
            parent_id = None
            if path and path != "/":
                records = await retry_api_call(
                    lambda: self.client.path_to_id(path),
                    label="路径解析",
                )
                if not records:
                    self.ui_queue.put(("error", f"路径不存在: {path}"))
                    return
                last = records[-1]
                if last.get("file_type") != "folder":
                    self.ui_queue.put(("error", f"不是文件夹: {path}"))
                    return
                parent_id = last["id"]
            result = await retry_api_call(
                lambda: self.client.file_list(size=500, parent_id=parent_id),
                label="文件列表",
            )
            files = result.get("files") or []
            self.ui_queue.put(("browse", (path or "/", parent_id, files)))
        except Exception as exc:
            self._log(f"列表失败: {exc}")
            self.ui_queue.put(("error", str(exc)))
        finally:
            self._list_loading = False
            self.root.after(0, self._sync_login_state)

    def _apply_browse(self, payload) -> None:
        path, parent_id, files = payload
        self.browse = BrowseState(path=path, parent_id=parent_id, files=files)
        self.path_var.set(path)
        for row in self.tree.get_children():
            self.tree.delete(row)
        for f in files:
            kind = "📁 文件夹" if is_folder(f) else "📄 文件"
            sz = format_size(f.get("size")) if not is_folder(f) else (
                f"{f.get('file_count', '—')} 项" if f.get("file_count") else "文件夹"
            )
            self.tree.insert("", "end", values=(kind, f.get("name", ""), sz, f.get("id", "")))
        self.count_label.configure(text=f"共 {len(files)} 项")
        self._log(f"已加载 {len(files)} 项 @ {path}")

    async def _fetch_quota(self) -> None:
        if not self.client:
            return
        try:
            info = await retry_api_call(
                self.client.get_quota_info,
                label="空间信息",
            )
            quota = info.get("quota") or {}
            limit = int(quota.get("limit") or 0)
            usage = int(quota.get("usage") or 0)
            trash = int(quota.get("usage_in_trash") or 0)
            ratio = usage / limit if limit else 0
            text = f"已用 {format_size(usage)} / {format_size(limit)}\n回收站 {format_size(trash)}"
            self.ui_queue.put(("quota", (ratio, text)))
        except Exception as exc:
            self._log(f"配额获取失败: {exc}")

    def _set_quota(self, payload) -> None:
        ratio, text = payload
        self.quota_bar.set(min(max(ratio, 0), 1))
        self.quota_label.configure(text=text)

    def _pick_dir(self) -> None:
        path = filedialog.askdirectory(parent=self.root, title="选择保存目录")
        if path:
            self.out_var.set(path)
            if self.orchestrator:
                self.orchestrator.set_dest_dir(Path(path))

    # ── download queue ───────────────────────────────────────────────────────

    def _on_download(self) -> None:
        if not self.logged_in or not self.browse.files:
            return
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选中要下载的文件", parent=self.root)
            return
        out = self.out_var.get().strip()
        if not out:
            messagebox.showwarning("提示", "请选择保存目录", parent=self.root)
            return

        from .progress import validate_dest_dir
        try:
            validate_dest_dir(Path(out))
        except OSError as exc:
            messagebox.showerror(
                "保存目录不可用",
                f"无法写入目录：{out}\n\n{exc}\n\n请检查磁盘是否已连接、是否有剩余空间。",
                parent=self.root,
            )
            return

        jobs: list[DownloadJob] = []
        for iid in sel:
            idx = self.tree.index(iid)
            if idx < 0 or idx >= len(self.browse.files):
                continue
            item = self.browse.files[idx]
            if is_folder(item):
                messagebox.showwarning(
                    "提示", f"「{item.get('name')}」是文件夹，请进入后选择文件", parent=self.root,
                )
                return
            fid = item.get("id", "")
            if fid:
                jobs.append(DownloadJob(
                    file_id=fid,
                    name=item.get("name", fid),
                    total_size=int(item.get("size") or 0),
                ))

        if not jobs:
            return

        try:
            orch = self._ensure_orchestrator()
        except RuntimeError as exc:
            messagebox.showerror("错误", str(exc), parent=self.root)
            return

        for job in jobs:
            self.ui_queue.put(("dl_add", {
                "job_id": job.job_id,
                "name": job.name,
                "total": job.total_size,
                "queued_at": job.queued_at,
                "status": "queued",
            }))

        orch.enqueue(jobs)
        self._update_summary()
        c = int(self.concurrent_combo.get())
        if len(jobs) > 1 and c > 1:
            self._log(f"已加入 {len(jobs)} 个任务；同时 {c} 个会分摊带宽，大文件建议设为 1")
        else:
            self._log(f"已加入 {len(jobs)} 个下载任务")

    def _dl_add_row(self, payload: dict) -> None:
        job_id = payload["job_id"]
        if job_id in self._dl_rows:
            return
        iid = self.dl_tree.insert("", "end", values=(
            payload["name"],
            STATUS_ZH.get(payload.get("status", "queued"), "排队中"),
            "0%",
            "-",
            "等待 0秒",
        ))
        self._dl_rows[job_id] = {
            "iid": iid,
            "name": payload["name"],
            "status": payload.get("status", "queued"),
            "queued_at": payload.get("queued_at", time.time()),
            "started_at": None,
            "done": 0,
            "total": payload.get("total") or 0,
            "speed_ema": 0.0,
            "last_progress_at": time.time(),
        }
        self._dl_iid_to_job[iid] = job_id
        self._update_summary()

    def _cancel_selected(self) -> None:
        sel = self.dl_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先在下载队列中选中任务", parent=self.root)
            return
        cancelled = 0
        for iid in sel:
            job_id = self._dl_iid_to_job.get(iid)
            if not job_id:
                continue
            row = self._dl_rows.get(job_id)
            if not row or row.get("status") in ("done", "failed", "cancelled"):
                continue
            if self.orchestrator and self.orchestrator.cancel_job(job_id):
                cancelled += 1
            else:
                row["status"] = "cancelled"
                self.ui_queue.put(("dl_update", {"job_id": job_id, "status": "cancelled"}))
                cancelled += 1
        if cancelled:
            self._log(f"已取消 {cancelled} 个任务")
        self._update_summary()

    def _pause_selected(self) -> None:
        sel = self.dl_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先在下载队列中选中任务", parent=self.root)
            return
        paused = 0
        for iid in sel:
            job_id = self._dl_iid_to_job.get(iid)
            if not job_id:
                continue
            row = self._dl_rows.get(job_id)
            if not row or row.get("status") in ("done", "failed", "cancelled", "paused"):
                continue
            if self.orchestrator and self.orchestrator.pause_job(job_id):
                paused += 1
        if paused:
            self._log(f"已暂停 {paused} 个任务")
        self._update_summary()

    def _resume_selected(self) -> None:
        sel = self.dl_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先在下载队列中选中任务", parent=self.root)
            return
        resumed = 0
        for iid in sel:
            job_id = self._dl_iid_to_job.get(iid)
            if not job_id:
                continue
            row = self._dl_rows.get(job_id)
            if not row or row.get("status") != "paused":
                continue
            if self.orchestrator and self.orchestrator.resume_job(job_id):
                resumed += 1
        if resumed:
            self._log(f"已继续 {resumed} 个任务")
        self._update_summary()

    def _dl_update_row(self, payload: dict) -> None:
        job_id = payload["job_id"]
        row = self._dl_rows.get(job_id)
        if not row:
            return

        status_changed = False
        progress_changed = False
        if "status" in payload:
            st = payload["status"]
            if row.get("status") != st:
                status_changed = True
            row["status"] = st
            if st in ("linking", "downloading") and not row.get("started_at"):
                row["started_at"] = time.time()
                row["last_progress_at"] = time.time()
            if st == "done":
                row["finished_at"] = time.time()

        if "done" in payload:
            new_done = payload["done"]
            if new_done != row.get("done"):
                row["last_progress_at"] = time.time()
                progress_changed = True
            row["done"] = new_done
        if "total" in payload and payload["total"]:
            if payload["total"] != row.get("total"):
                progress_changed = True
            row["total"] = payload["total"]
        if "speed" in payload:
            new_speed = payload["speed"]
            if new_speed > 0:
                row["speed_ema"] = new_speed
                progress_changed = True

        if status_changed or progress_changed:
            self._refresh_dl_row(job_id)
        if status_changed or progress_changed:
            self._update_summary()

    def _format_download_speed(self, row: dict) -> str:
        st = row["status"]
        if st == "merging":
            return "写入文件…"
        if st not in ("downloading", "retrying"):
            return "-"
        if st == "retrying":
            return "刷新链接…"
        ema = row.get("speed_ema", 0.0)
        if ema > 0:
            return format_speed(ema)
        done = row.get("done", 0)
        idle = time.time() - row.get("last_progress_at", time.time())
        if done > 0:
            if idle > 60:
                return "等待重试…"
            if idle > 8:
                return "连接中…"
            return "续传中…"
        return "连接中…"

    def _refresh_dl_row(self, job_id: str) -> None:
        row = self._dl_rows.get(job_id)
        if not row:
            return
        st = row["status"]
        total = row["total"]
        done = row["done"]
        pct = f"{min(done * 100 // total, 100)}%" if total > 0 else "-"
        if st == "done":
            pct = "100%"
        elif st in ("downloading", "retrying", "merging") and total > 0:
            pct = f"{min(done * 100 // total, 99)}%"
        speed = self._format_download_speed(row)
        time_txt = self._format_job_time(row)
        self.dl_tree.item(row["iid"], values=(
            row["name"],
            STATUS_ZH.get(st, st),
            pct,
            speed,
            time_txt,
        ))

    def _format_job_time(self, row: dict) -> str:
        st = row["status"]
        now = time.time()
        if st == "queued":
            return f"等待 {format_duration(now - row['queued_at'])}"
        if st in ("linking", "downloading", "merging") and row.get("started_at"):
            return f"用时 {format_duration(now - row['started_at'])}"
        if st == "done" and row.get("started_at"):
            return f"共 {format_duration(row.get('finished_at', now) - row['started_at'])}"
        if st == "failed":
            return "失败"
        if st == "paused":
            return "已暂停"
        if st == "cancelled":
            return "已取消"
        return "-"

    def _tick_times(self) -> None:
        now = time.time()
        need_summary = False
        for job_id, row in list(self._dl_rows.items()):
            st = row["status"]
            if st in ("queued", "linking", "downloading", "merging", "retrying", "paused"):
                self._refresh_dl_row(job_id)
                need_summary = True
            elif st == "done" and not row.get("finished_at"):
                row["finished_at"] = now
                self._refresh_dl_row(job_id)
        if need_summary:
            self._update_summary()
        self.root.after(500, self._tick_times)

    def _clear_finished(self) -> None:
        to_remove = [
            jid for jid, r in self._dl_rows.items()
            if r["status"] in ("done", "failed", "cancelled")
        ]
        for jid in to_remove:
            iid = self._dl_rows[jid]["iid"]
            self.dl_tree.delete(iid)
            self._dl_iid_to_job.pop(iid, None)
            del self._dl_rows[jid]
        if self.orchestrator:
            for jid in to_remove:
                self.orchestrator.jobs.pop(jid, None)
        self._update_summary()

    def run(self) -> None:
        self.root.mainloop()
        if self.orchestrator:
            self._run_async(self.orchestrator.stop())
        self.loop.call_soon_threadsafe(self.loop.stop)


def run_gui() -> None:
    PikPakGui().run()


if __name__ == "__main__":
    run_gui()
