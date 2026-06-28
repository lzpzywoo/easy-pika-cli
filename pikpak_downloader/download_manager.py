"""Concurrent download orchestration with cancel support."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import httpx
from pikpakapi import PikPakApi

from .downloader import (
    DownloadCancelled,
    DownloadStalled,
    _pick_download_url,
    create_cdn_client,
    download_from_file_info,
    read_file_size,
    resolve_file_size,
    scrub_parts_dir,
)
from .progress import ProgressThrottler, resume_bytes_for_dest, validate_dest_dir
from .token_helpers import TokenManager, get_download_url_with_retry

OnStatus = Callable[[str, str], None]
OnProgress = Callable[[str, int, int, float], None]

MAX_JOB_ATTEMPTS = 12  # 整文件级：刷新 CDN 链接并重试的次数


@dataclass
class DownloadJob:
    file_id: str
    name: str
    total_size: int = 0
    rel_path: str | None = None
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    queued_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    done_bytes: int = 0
    status: str = "queued"
    error: Optional[str] = None
    pause_requested: bool = False
    paused_at: Optional[float] = None


class DownloadOrchestrator:
    """Background queue; shares one CDN HTTP client across all downloads."""

    def __init__(
        self,
        client: PikPakApi,
        session_path: Optional[str],
        dest_dir: Path,
        threads_per_file: int = 12,
        max_concurrent: int = 2,
        on_status: Optional[OnStatus] = None,
        on_progress: Optional[OnProgress] = None,
        on_log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.client = client
        self.session_path = session_path
        self.dest_dir = dest_dir
        self.threads_per_file = max(1, threads_per_file)
        self.max_concurrent = max(1, max_concurrent)
        self.on_status = on_status or (lambda _a, _b: None)
        self.on_progress = on_progress or (lambda _a, _b, _c, _d: None)
        self.on_log = on_log or (lambda _m: None)

        self.token_mgr = TokenManager(client, session_path)
        self.jobs: dict[str, DownloadJob] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._pause_dest: dict[str, Path] = {}
        self._queue: asyncio.Queue[DownloadJob | None] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._refresh_task: asyncio.Task | None = None
        self._running = False

    def set_max_concurrent(self, n: int) -> None:
        self.max_concurrent = max(1, n)

    async def ensure_workers(self) -> None:
        """Ensure enough worker tasks are running (safe to call after raising max_concurrent)."""
        if not self._running:
            return
        self._workers = [w for w in self._workers if not w.done()]
        while len(self._workers) < self.max_concurrent:
            self._workers.append(asyncio.create_task(self._worker_loop()))

    def set_threads_per_file(self, n: int) -> None:
        self.threads_per_file = max(1, n)

    def set_dest_dir(self, path: Path) -> None:
        self.dest_dir = path

    def enqueue(self, jobs: list[DownloadJob]) -> list[DownloadJob]:
        for job in jobs:
            job.status = "queued"
            self.jobs[job.job_id] = job
            self._queue.put_nowait(job)
            self.on_status(job.job_id, "queued")
        return jobs

    def cancel_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job or job.status in ("done", "failed", "cancelled"):
            return False
        job.pause_requested = False
        job.status = "cancelled"
        event = self._cancel_events.get(job_id)
        if event:
            event.set()
        self.on_status(job_id, "cancelled")
        self.on_log(f"已取消: {job.name}")
        return True

    def pause_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job or job.status in ("done", "failed", "cancelled", "paused"):
            return False
        if job.status == "queued":
            job.status = "paused"
            job.paused_at = time.time()
            self.on_status(job_id, "paused")
            self.on_log(f"已暂停: {job.name}")
            return True
        if job.status not in ("linking", "downloading", "merging", "retrying"):
            return False
        job.pause_requested = True
        event = self._cancel_events.get(job_id)
        if event:
            event.set()
        return True

    def resume_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job or job.status != "paused":
            return False
        job.status = "queued"
        job.paused_at = None
        job.pause_requested = False
        if job.started_at is not None:
            self._queue.put_nowait(job)
        self.on_status(job_id, "queued")
        self.on_log(f"已继续: {job.name}")
        return True

    def _apply_paused(self, job: DownloadJob, dest_path: Path | None = None) -> None:
        job.pause_requested = False
        job.status = "paused"
        job.paused_at = time.time()
        if dest_path is not None:
            total = job.total_size or read_file_size(dest_path, 0)
            if total > 0:
                job.done_bytes = resume_bytes_for_dest(dest_path, total)
                self.on_progress(job.job_id, job.done_bytes, total, 0.0)
        self.on_status(job.job_id, "paused")
        self.on_log(f"已暂停: {job.name}")

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.dest_dir.mkdir(parents=True, exist_ok=True)
        await self.ensure_workers()
        self._refresh_task = asyncio.create_task(self._token_refresh_loop())

    async def stop(self) -> None:
        self._running = False
        for job_id in list(self.jobs.keys()):
            if self.jobs[job_id].status in ("queued", "linking", "downloading"):
                self.cancel_job(job_id)
        for _ in self._workers:
            await self._queue.put(None)
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self._refresh_task = None

    async def _token_refresh_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(1800)
                await self.token_mgr.refresh(force=False)
                self.on_log("Token 已自动刷新")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.on_log(f"Token 刷新失败: {exc}")

    async def _worker_loop(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                if job is None:
                    break
                if job.status == "cancelled":
                    continue
                if job.status == "paused":
                    self._queue.put_nowait(job)
                    await asyncio.sleep(0.5)
                    continue
                try:
                    await self._run_job(job)
                except Exception as exc:
                    if job.status not in ("done", "failed", "cancelled"):
                        job.status = "failed"
                        job.error = str(exc)
                        self.on_status(job.job_id, "failed")
                        self.on_log(f"失败 ({job.name}): {exc}")
            finally:
                self._queue.task_done()

    async def _run_job(self, job: DownloadJob) -> None:
        if job.status == "cancelled":
            return

        cancel_event = asyncio.Event()
        self._cancel_events[job.job_id] = cancel_event
        tick_stop: asyncio.Event | None = None
        tick_task: asyncio.Task | None = None
        cdn_client: httpx.AsyncClient | None = None

        try:
            job.started_at = time.time()
            job.status = "linking"
            self.on_status(job.job_id, "linking")

            try:
                file_info = await get_download_url_with_retry(
                    self.client, job.file_id, self.token_mgr,
                    cancel_event=cancel_event,
                )
            except Exception as exc:
                job.status = "failed"
                job.error = str(exc)
                self.on_status(job.job_id, "failed")
                self.on_log(f"失败 ({job.name}): {exc}")
                return
            if job.status == "cancelled" or cancel_event.is_set():
                return

            job.status = "downloading"
            self.on_status(job.job_id, "downloading")

            api_size = job.total_size or int(file_info.get("size") or 0)
            if job.rel_path:
                dest_path = self.dest_dir / job.rel_path
                dest_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                dest_path = self.dest_dir / (file_info.get("name") or job.name)
            self._pause_dest[job.job_id] = dest_path
            phase_holder: dict = {"v": "download"}
            cdn_client = create_cdn_client(threads=self.threads_per_file)
            cdn_headers = self.token_mgr.get_headers()

            url, _ = _pick_download_url(file_info)
            cdn_size = await resolve_file_size(
                cdn_client, url, dest_path, api_size, cdn_headers,
                on_log=self.on_log,
            )
            total_holder = {"v": cdn_size if cdn_size > 0 else api_size}
            if total_holder["v"] > 0:
                job.total_size = total_holder["v"]
                self.on_progress(
                    job.job_id, job.done_bytes, total_holder["v"], 0.0,
                )

            def _on_total_known(size: int) -> None:
                if size > 0 and size != total_holder["v"]:
                    total_holder["v"] = size
                    job.total_size = size

            def _on_phase(phase: str) -> None:
                if phase == "merge":
                    self.on_status(job.job_id, "merging")
                elif phase == "download":
                    self.on_status(job.job_id, "downloading")

            try:
                validate_dest_dir(self.dest_dir)
            except OSError as exc:
                job.status = "failed"
                job.error = f"保存目录不可用 ({self.dest_dir}): {exc}"
                self.on_status(job.job_id, "failed")
                self.on_log(f"失败 ({job.name}): {job.error}")
                return

            resume = resume_bytes_for_dest(dest_path, total_holder["v"])
            if resume > 0:
                scrub_parts_dir(dest_path, total_holder["v"], on_log=self.on_log)
                resume = resume_bytes_for_dest(dest_path, total_holder["v"])
            if resume > 0:
                job.done_bytes = resume
                total = total_holder["v"]
                if total > 0:
                    self.on_log(
                        f"{job.name}: 从 {resume * 100 // total}% 续传"
                    )

            def _make_reporter(baseline: int) -> ProgressThrottler:
                total = total_holder["v"]
                if total > 0:
                    baseline = min(baseline, total)
                rep = ProgressThrottler(
                    job.job_id,
                    get_done=lambda: job.done_bytes,
                    set_done=lambda v: setattr(job, "done_bytes", v),
                    get_total=lambda: total_holder["v"],
                    on_emit=self.on_progress,
                    baseline_bytes=baseline,
                )
                if baseline > 0:
                    rep._emit(time.time())
                return rep

            reporter = _make_reporter(resume)
            tick_stop = asyncio.Event()
            tick_task = asyncio.create_task(self._progress_tick_loop(reporter, tick_stop))

            file_info_holder = {"info": file_info}
            if resume > 0:
                self.on_log(f"{job.name}: 续传前刷新下载链接…")
                file_info_holder["info"] = await get_download_url_with_retry(
                    self.client, job.file_id, self.token_mgr,
                    cancel_event=cancel_event,
                )

            last_error: Exception | None = None

            def _needs_url_refresh(exc: Exception) -> bool:
                if isinstance(exc, (DownloadStalled, RuntimeError)):
                    return True
                msg = str(exc).lower()
                return any(
                    k in msg for k in (
                        "403", "404", "expired", "forbidden", "invalid",
                        "卡死", "stall", "无数据", "无进度", "pool", "timeout",
                        "分块", "未完成", "http", "connection", "connect",
                        "416", "range", "保存目录", "winerror",
                    )
                )

            async def _refresh_and_resume(attempt: int, exc: Exception) -> None:
                nonlocal cdn_client, reporter
                self.on_log(
                    f"{job.name}: 下载异常，刷新链接并续传 "
                    f"({attempt + 1}/{MAX_JOB_ATTEMPTS - 1})…"
                )
                self.on_log(f"  原因: {exc}")
                self.on_status(job.job_id, "retrying")
                if cdn_client is not None:
                    await cdn_client.aclose()
                cdn_client = create_cdn_client(threads=self.threads_per_file)
                file_info_holder["info"] = await get_download_url_with_retry(
                    self.client, job.file_id, self.token_mgr,
                    cancel_event=cancel_event,
                )
                url, api_sz = _pick_download_url(file_info_holder["info"])
                total_holder["v"] = await resolve_file_size(
                    cdn_client, url, dest_path,
                    api_sz or total_holder["v"], cdn_headers,
                    on_log=self.on_log,
                ) or total_holder["v"]
                if total_holder["v"] > 0:
                    job.total_size = total_holder["v"]
                scrub_parts_dir(dest_path, total_holder["v"], on_log=self.on_log)
                resume2 = resume_bytes_for_dest(dest_path, total_holder["v"])
                reporter.sync_from_disk(resume2)
                job.done_bytes = resume2
                self.on_status(job.job_id, "downloading")
                await asyncio.sleep(min(2 ** attempt, 8))

            for attempt in range(MAX_JOB_ATTEMPTS):
                if job.pause_requested or job.status == "paused":
                    self._apply_paused(job, dest_path)
                    return
                if job.status == "cancelled" or cancel_event.is_set():
                    return
                attempt_abort = asyncio.Event()
                watch_task = asyncio.create_task(
                    self._watch_download_stall(
                        job, attempt_abort, cancel_event, tick_stop,
                        phase_holder=phase_holder,
                        dest_path=dest_path,
                        get_total=lambda: total_holder["v"],
                    )
                )
                try:
                    dest = await download_from_file_info(
                        file_info_holder["info"],
                        dest_path.parent,
                        threads=self.threads_per_file,
                        headers=self.token_mgr.get_headers(),
                        filename=dest_path.name,
                        on_progress=reporter.feed,
                        quiet=True,
                        cancel_event=cancel_event,
                        abort_event=attempt_abort,
                        http_client=cdn_client,
                        on_log=self.on_log,
                        on_total_known=_on_total_known,
                        phase_holder=phase_holder,
                        on_phase=_on_phase,
                    )
                    reporter.flush()
                    if total_holder["v"] > 0:
                        job.done_bytes = total_holder["v"]
                    job.status = "done"
                    self.on_status(job.job_id, "done")
                    self.on_log(f"完成: {dest.name}")
                    return
                except (DownloadCancelled, asyncio.CancelledError):
                    if job.pause_requested:
                        self._apply_paused(job, dest_path)
                        return
                    if cancel_event.is_set():
                        raise
                    last_error = DownloadStalled("无进度超时")
                    if attempt < MAX_JOB_ATTEMPTS - 1:
                        await _refresh_and_resume(attempt, last_error)
                        continue
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < MAX_JOB_ATTEMPTS - 1 and _needs_url_refresh(exc):
                        await _refresh_and_resume(attempt, exc)
                        continue
                    break
                finally:
                    watch_task.cancel()
                    try:
                        await watch_task
                    except asyncio.CancelledError:
                        pass
                    attempt_abort.set()

            if job.pause_requested or job.status == "paused":
                self._apply_paused(job, dest_path)
            elif job.status == "cancelled" or cancel_event.is_set():
                job.status = "cancelled"
                self.on_status(job.job_id, "cancelled")
            else:
                job.status = "failed"
                job.error = str(last_error)
                self.on_status(job.job_id, "failed")
                self.on_log(f"失败 ({job.name}): {last_error}")
            return
        except (DownloadCancelled, asyncio.CancelledError):
            if job.pause_requested:
                dest_path = self._pause_dest.get(job.job_id)
                self._apply_paused(job, dest_path)
            else:
                job.status = "cancelled"
                self.on_status(job.job_id, "cancelled")
        finally:
            if tick_stop is not None:
                tick_stop.set()
            if tick_task is not None:
                tick_task.cancel()
                try:
                    await tick_task
                except asyncio.CancelledError:
                    pass
            if cdn_client is not None:
                await cdn_client.aclose()
            self._cancel_events.pop(job.job_id, None)
            self._pause_dest.pop(job.job_id, None)

    async def _watch_download_stall(
        self,
        job: DownloadJob,
        abort_event: asyncio.Event,
        user_cancel: asyncio.Event,
        stop: asyncio.Event,
        idle_sec: float = 120.0,
        phase_holder: Optional[dict] = None,
        dest_path: Optional[Path] = None,
        get_total: Optional[Callable[[], int]] = None,
    ) -> None:
        """若长时间无新字节，触发 abort 以刷新 CDN 链接。"""
        last_done = job.done_bytes
        idle_since = time.time()
        while not stop.is_set() and not user_cancel.is_set():
            await asyncio.sleep(5)
            if stop.is_set() or user_cancel.is_set():
                return
            phase = phase_holder.get("v") if phase_holder else None
            if phase == "merge":
                idle_since = time.time()
                continue
            if dest_path is not None and get_total is not None and phase != "merge":
                total = get_total()
                if total > 0:
                    on_disk = resume_bytes_for_dest(dest_path, total)
                    if on_disk >= total:
                        idle_since = time.time()
                        continue
            if job.done_bytes > last_done:
                last_done = job.done_bytes
                idle_since = time.time()
            elif time.time() - idle_since >= idle_sec:
                abort_event.set()
                return

    async def _progress_tick_loop(
        self,
        reporter: ProgressThrottler,
        stop: asyncio.Event,
    ) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                reporter.tick()

    @property
    def active_count(self) -> int:
        return sum(
            1 for j in self.jobs.values()
            if j.status in ("linking", "downloading", "merging", "retrying")
        )

    @property
    def paused_count(self) -> int:
        return sum(1 for j in self.jobs.values() if j.status == "paused")

    @property
    def queued_count(self) -> int:
        return sum(1 for j in self.jobs.values() if j.status == "queued")

    @property
    def done_count(self) -> int:
        return sum(1 for j in self.jobs.values() if j.status == "done")

    @property
    def failed_count(self) -> int:
        return sum(1 for j in self.jobs.values() if j.status == "failed")

    @property
    def cancelled_count(self) -> int:
        return sum(1 for j in self.jobs.values() if j.status == "cancelled")
