import asyncio
from pathlib import Path
from typing import Callable, Dict, Optional

import httpx
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
    TransferSpeedColumn,
)

CHUNK_READ = 2 * 1024 * 1024  # 2MB
PROGRESS_BATCH = 512 * 1024
MULTIPART_SLICE = 128 * 1024 * 1024  # 128MB 大块（新下载）
LEGACY_MULTIPART_SLICE = 32 * 1024 * 1024  # 旧版 32MB，续传时自动识别
_SLICE_META = ".slice_size"
_FILE_SIZE_META = ".file_size"
MAX_RETRIES = 8
RETRY_BASE_SEC = 2.0
STALL_TIMEOUT = 30.0
CHUNK_REQUEST_TIMEOUT = 180.0  # 128MB 分块在慢速/波动网络下需更长时间
MAX_HTTP_CONCURRENCY = 8  # 并行连接数上限


def save_file_size(dest: Path, size: int) -> None:
    """持久化 CDN 校正后的文件大小，续传/重试时与 API 元数据解耦。"""
    if size <= 0:
        return
    part_dir = dest.with_suffix(dest.suffix + ".parts")
    part_dir.mkdir(parents=True, exist_ok=True)
    (part_dir / _FILE_SIZE_META).write_text(str(size), encoding="utf-8")


def read_file_size(dest: Path, fallback: int = 0) -> int:
    """优先读取本地已保存的 CDN 大小。"""
    meta = dest.with_suffix(dest.suffix + ".parts") / _FILE_SIZE_META
    if meta.exists():
        try:
            return int(meta.read_text().strip())
        except ValueError:
            pass
    return fallback


def multipart_slice_for(dest: Path, file_size: int) -> int:
    """读取或推断分块大小，兼容旧 32MB 缓存。"""
    part_dir = dest.with_suffix(dest.suffix + ".parts")
    meta = part_dir / _SLICE_META
    if meta.exists():
        try:
            return int(meta.read_text().strip())
        except ValueError:
            pass
    if part_dir.is_dir() and file_size > 0:
        n = len(list(part_dir.glob("part_*")))
        if n > 0:
            slices_128 = (file_size + MULTIPART_SLICE - 1) // MULTIPART_SLICE
            slice_sz = (
                LEGACY_MULTIPART_SLICE
                if n > slices_128 + 2
                else MULTIPART_SLICE
            )
            part_dir.mkdir(exist_ok=True)
            meta.write_text(str(slice_sz))
            return slice_sz
    slice_sz = MULTIPART_SLICE
    part_dir.mkdir(parents=True, exist_ok=True)
    meta.write_text(str(slice_sz))
    return slice_sz


def _part_ranges(
    dest: Path, file_size: int, slice_size: int,
) -> list[tuple[int, int, Path]]:
    part_dir = dest.with_suffix(dest.suffix + ".parts")
    ranges: list[tuple[int, int, Path]] = []
    start = 0
    idx = 0
    while start < file_size:
        end = min(start + slice_size - 1, file_size - 1)
        ranges.append((start, end, part_dir / f"part_{idx:06d}"))
        start = end + 1
        idx += 1
    return ranges


def reconcile_file_size(
    dest: Path,
    api_size: int,
    probed_size: int,
    *,
    on_log: Optional[Callable[[str], None]] = None,
) -> int:
    """以 CDN 探测大小为准，修正 API 元数据与本地分块布局。"""
    saved = read_file_size(dest, 0)
    if probed_size > 0:
        if api_size > 0 and abs(probed_size - api_size) > 4096:
            if on_log:
                on_log(
                    f"文件大小校正: API {api_size:,} 字节 → "
                    f"CDN {probed_size:,} 字节（以 CDN 为准）"
                )
        save_file_size(dest, probed_size)
        slice_size = multipart_slice_for(dest, probed_size)
        trim_orphan_parts(dest, probed_size, slice_size)
        return probed_size
    if saved > 0:
        return saved
    return api_size if api_size > 0 else probed_size


async def resolve_file_size(
    client: httpx.AsyncClient,
    url: str,
    dest: Path,
    api_size: int,
    headers: Optional[Dict[str, str]] = None,
    *,
    on_log: Optional[Callable[[str], None]] = None,
) -> int:
    """下载/续传前解析文件大小：CDN 探测 > 本地 .file_size > API。"""
    headers = _cdn_headers(headers)
    probed_size, _ = await _probe_range(client, url, headers)
    return reconcile_file_size(dest, api_size, probed_size, on_log=on_log)


def trim_orphan_parts(dest: Path, file_size: int, slice_size: int) -> None:
    """删除超出实际文件大小的分块文件。"""
    part_dir = dest.with_suffix(dest.suffix + ".parts")
    if not part_dir.is_dir() or file_size <= 0:
        return
    valid = {p for _, _, p in _part_ranges(dest, file_size, slice_size)}
    for p in part_dir.glob("part_*"):
        if p not in valid:
            p.unlink(missing_ok=True)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class DownloadCancelled(Exception):
    """Raised when user cancels an in-progress download."""


class DownloadStalled(Exception):
    """No data received within stall timeout — CDN connection likely hung."""


def _make_progress_callback(
    on_progress: Optional[Callable[[int], None]],
) -> Optional[Callable[[int], None]]:
    if not on_progress:
        return None
    pending = 0

    def cb(n: int) -> None:
        nonlocal pending
        pending += n
        if pending >= PROGRESS_BATCH:
            on_progress(pending)
            pending = 0

    cb.flush = lambda: on_progress(pending) if pending else None  # type: ignore[attr-defined]
    return cb


def _check_cancel(
    cancel_event: asyncio.Event | None,
    abort_event: asyncio.Event | None = None,
) -> None:
    if cancel_event and cancel_event.is_set():
        raise DownloadCancelled("下载已取消")
    if abort_event and abort_event.is_set():
        raise DownloadStalled("无进度，中断以刷新链接")


async def _iter_bytes_with_stall(
    resp: httpx.Response,
    *,
    chunk_size: int = CHUNK_READ,
    stall_timeout: float = STALL_TIMEOUT,
    cancel_event: asyncio.Event | None = None,
    abort_event: asyncio.Event | None = None,
):
    """逐块读取；若长时间无数据则抛出 DownloadStalled（避免 70% 假死）。"""
    aiter = resp.aiter_bytes(chunk_size=chunk_size)
    while True:
        _check_cancel(cancel_event, abort_event)
        try:
            chunk = await asyncio.wait_for(aiter.__anext__(), timeout=stall_timeout)
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError as exc:
            raise DownloadStalled(
                f"超过 {int(stall_timeout)} 秒无数据，连接可能已卡死"
            ) from exc
        yield chunk


def _pick_download_url(file_info: dict) -> tuple[str, int]:
    """从文件信息中提取下载 URL 和文件大小。"""
    size = int(file_info.get("size") or 0)

    medias = file_info.get("medias") or []
    if medias:
        link = (medias[0].get("link") or {}).get("url")
        if link:
            return link, size

    web_link = file_info.get("web_content_link")
    if web_link:
        return web_link, size

    raise ValueError("无法获取下载链接，文件可能尚未完成转存或需要验证码")


def _cdn_headers(headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """CDN 直链只需浏览器头，勿带 API Authorization / Captcha 等。"""
    ua = (headers or {}).get("User-Agent") or DEFAULT_UA
    return {
        "User-Agent": ua,
        "Referer": "https://mypikpak.com/",
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Connection": "close",
    }


def _make_client() -> httpx.AsyncClient:
    return create_cdn_client()


def create_cdn_client(*, threads: int = 12) -> httpx.AsyncClient:
    """CDN client — one per download job; no keepalive (CDN Range 兼容更好)。"""
    pool_size = max(min(threads, MAX_HTTP_CONCURRENCY) + 2, 8)
    timeout = httpx.Timeout(connect=30.0, read=STALL_TIMEOUT + 30, write=60.0, pool=30.0)
    limits = httpx.Limits(
        max_connections=pool_size,
        max_keepalive_connections=0,
        keepalive_expiry=0,
    )
    return httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        follow_redirects=True,
    )


async def _probe_range(client: httpx.AsyncClient, url: str, headers: Dict[str, str]) -> tuple[int, bool]:
    """返回 (文件大小, 是否支持 Range)。"""
    file_size = 0
    accept_ranges = False

    try:
        head = await client.head(url, headers=headers)
        if head.status_code < 400:
            file_size = int(head.headers.get("content-length") or 0)
            accept_ranges = head.headers.get("accept-ranges", "").lower() == "bytes"
    except httpx.HTTPError:
        pass

    if not accept_ranges:
        probe = {**headers, "Range": "bytes=0-0"}
        try:
            async with client.stream("GET", url, headers=probe) as resp:
                if resp.status_code == 206:
                    accept_ranges = True
                    cr = resp.headers.get("content-range", "")
                    if "/" in cr:
                        total = cr.split("/")[-1].strip()
                        if total.isdigit():
                            file_size = int(total)
                elif resp.status_code == 200 and not file_size:
                    file_size = int(resp.headers.get("content-length") or 0)
                # drain body so the connection returns to the pool
                await resp.aread()
        except httpx.HTTPError:
            pass

    return file_size, accept_ranges


async def _stream_to_file(
    client: httpx.AsyncClient,
    url: str,
    headers: Dict[str, str],
    dest_path: Path,
    *,
    expected_bytes: int = 0,
    on_progress: Optional[Callable[[int], None]] = None,
    cancel_event: asyncio.Event | None = None,
    abort_event: asyncio.Event | None = None,
) -> None:
    """带重试与断点续传的流式写入。"""
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(MAX_RETRIES):
        _check_cancel(cancel_event, abort_event)
        existing = dest_path.stat().st_size if dest_path.exists() else 0
        if expected_bytes > 0 and existing >= expected_bytes:
            return

        req_headers = dict(headers)
        if existing > 0:
            req_headers["Range"] = f"bytes={existing}-"

        try:
            async with client.stream("GET", url, headers=req_headers) as resp:
                if resp.status_code == 416:
                    if existing >= expected_bytes > 0:
                        return
                    if dest_path.exists():
                        dest_path.unlink(missing_ok=True)
                    raise httpx.HTTPError("416 Range 无效，将重新下载")
                if existing > 0 and resp.status_code == 200:
                    raise httpx.HTTPError("服务器未返回 Range 续传 (200)")
                if resp.status_code not in (200, 206):
                    resp.raise_for_status()

                mode = "ab" if existing > 0 else "wb"
                with open(dest_path, mode) as f:
                    async for data in _iter_bytes_with_stall(
                        resp, cancel_event=cancel_event, abort_event=abort_event,
                    ):
                        f.write(data)
                        if on_progress:
                            on_progress(len(data))

            if expected_bytes <= 0 or dest_path.stat().st_size >= expected_bytes:
                return

        except DownloadCancelled:
            raise
        except DownloadStalled as exc:
            if abort_event and abort_event.is_set():
                raise
            if attempt >= MAX_RETRIES - 1:
                raise RuntimeError(
                    f"下载失败（已重试 {MAX_RETRIES} 次）: {exc}"
                ) from exc
            await asyncio.sleep(RETRY_BASE_SEC * (2**attempt))
        except (httpx.HTTPError, httpx.TransportError, OSError) as exc:
            if attempt >= MAX_RETRIES - 1:
                raise RuntimeError(
                    f"下载失败（已重试 {MAX_RETRIES} 次）: {exc}"
                ) from exc
            await asyncio.sleep(RETRY_BASE_SEC * (2**attempt))


async def _download_chunk(
    client: httpx.AsyncClient,
    url: str,
    headers: Dict[str, str],
    start: int,
    end: int,
    part_path: Path,
    on_progress: Optional[Callable[[int], None]] = None,
    cancel_event: asyncio.Event | None = None,
    abort_event: asyncio.Event | None = None,
    file_size: int = 0,
) -> None:
    if file_size > 0 and start >= file_size:
        return
    if file_size > 0:
        end = min(end, file_size - 1)
    expected = end - start + 1
    if expected <= 0:
        return
    range_headers = dict(headers)

    for attempt in range(MAX_RETRIES):
        _check_cancel(cancel_event, abort_event)
        existing = part_path.stat().st_size if part_path.exists() else 0
        if existing > expected:
            part_path.unlink(missing_ok=True)
            existing = 0
        if existing >= expected:
            return

        range_start = start + existing
        if file_size > 0 and range_start >= file_size:
            return
        chunk_end = min(end, file_size - 1) if file_size > 0 else end
        range_headers["Range"] = f"bytes={range_start}-{chunk_end}"

        async def _fetch_once() -> str | None:
            async with client.stream("GET", url, headers=range_headers) as resp:
                if resp.status_code == 416:
                    return "416"
                if existing > 0 and resp.status_code == 200:
                    part_path.unlink(missing_ok=True)
                    raise httpx.HTTPError("服务器未返回 Range 续传 (200)")
                if resp.status_code not in (200, 206):
                    resp.raise_for_status()

                mode = "ab" if existing > 0 else "wb"
                with open(part_path, "wb" if existing == 0 else "ab") as f:
                    async for data in _iter_bytes_with_stall(
                        resp, cancel_event=cancel_event, abort_event=abort_event,
                    ):
                        f.write(data)
                        if on_progress:
                            on_progress(len(data))
            return None

        try:
            err = await asyncio.wait_for(_fetch_once(), timeout=CHUNK_REQUEST_TIMEOUT)
            if err == "416":
                part_path.unlink(missing_ok=True)
                if file_size > 0 and start >= file_size:
                    return
                await asyncio.sleep(RETRY_BASE_SEC * (2**attempt))
                continue
            if part_path.stat().st_size >= expected:
                return

        except DownloadCancelled:
            raise
        except asyncio.TimeoutError as exc:
            stall = DownloadStalled(
                f"分块 {start}-{end} 超过 {int(CHUNK_REQUEST_TIMEOUT)} 秒无响应"
            )
            if abort_event and abort_event.is_set():
                raise stall from exc
            if attempt >= MAX_RETRIES - 1:
                raise RuntimeError(
                    f"分块 {start}-{end} 下载失败（已重试 {MAX_RETRIES} 次）: {stall}"
                ) from exc
            await asyncio.sleep(RETRY_BASE_SEC * (2**attempt))
        except DownloadStalled as exc:
            if abort_event and abort_event.is_set():
                raise
            if attempt >= MAX_RETRIES - 1:
                raise RuntimeError(
                    f"分块 {start}-{end} 下载失败（已重试 {MAX_RETRIES} 次）: {exc}"
                ) from exc
            await asyncio.sleep(RETRY_BASE_SEC * (2**attempt))
        except (httpx.HTTPError, httpx.TransportError, OSError) as exc:
            msg = str(exc).strip() or repr(exc)
            if attempt >= MAX_RETRIES - 1:
                raise RuntimeError(
                    f"分块 {start}-{end} 下载失败（已重试 {MAX_RETRIES} 次）: {msg}"
                ) from exc
            await asyncio.sleep(RETRY_BASE_SEC * (2**attempt))


async def _download_multipart(
    client: httpx.AsyncClient,
    url: str,
    headers: Dict[str, str],
    dest: Path,
    file_size: int,
    threads: int,
    on_progress: Optional[Callable[[int], None]] = None,
    quiet: bool = False,
    cancel_event: asyncio.Event | None = None,
    abort_event: asyncio.Event | None = None,
    on_log: Optional[Callable[[str], None]] = None,
    phase_holder: Optional[dict] = None,
    on_phase: Optional[Callable[[str], None]] = None,
) -> Path:
    """
    将文件切成 16MB 小块，N 个 worker 从队列持续取块下载，
    避免「4 线程 = 4 大块 → 完成一半后只剩 2 连接」导致的 50% 降速。
    """
    part_dir = dest.with_suffix(dest.suffix + ".parts")
    part_path = dest.with_suffix(dest.suffix + ".part")
    if part_dir.is_dir() and part_path.exists():
        part_path.unlink(missing_ok=True)
    part_dir.mkdir(exist_ok=True)

    scrub_parts_dir(dest, file_size, on_log=on_log)

    slice_size = multipart_slice_for(dest, file_size)
    trim_orphan_parts(dest, file_size, slice_size)
    ranges = _part_ranges(dest, file_size, slice_size)

    work: asyncio.Queue[tuple[int, int, Path] | None] = asyncio.Queue()
    pending = 0
    for item in ranges:
        s, e, p = item
        expected = e - s + 1
        if p.exists() and p.stat().st_size >= expected:
            continue
        work.put_nowait(item)
        pending += 1

    if on_log:
        on_log(f"分块下载: {pending}/{len(ranges)} 个分块待下载 (并发 {min(threads, MAX_HTTP_CONCURRENCY, max(pending, 1))})")

    def advance(n: int) -> None:
        if on_progress:
            on_progress(n)

    http_sem = asyncio.Semaphore(
        min(MAX_HTTP_CONCURRENCY, max(pending, 1)),
    )

    async def pool_worker() -> None:
        while True:
            _check_cancel(cancel_event, abort_event)
            item = await work.get()
            if item is None:
                work.task_done()
                break
            s, e, p = item
            try:
                async with http_sem:
                    await _download_chunk(
                        client, url, headers, s, e, p,
                        on_progress=advance,
                        cancel_event=cancel_event,
                        abort_event=abort_event,
                        file_size=file_size,
                    )
            finally:
                work.task_done()

    if pending > 0:
        worker_count = min(threads, MAX_HTTP_CONCURRENCY, pending)
        workers = [asyncio.create_task(pool_worker()) for _ in range(worker_count)]
        for _ in range(worker_count):
            work.put_nowait(None)
        await asyncio.gather(*workers)

    for s, e, p in ranges:
        expected = e - s + 1
        if not p.exists() or p.stat().st_size < expected:
            raise RuntimeError(f"分块未完成: {p.name}")

    if on_log:
        on_log("正在合并分块…")
    if phase_holder is not None:
        phase_holder["v"] = "merge"
    if on_phase:
        on_phase("merge")

    def _merge_parts() -> None:
        with open(dest, "wb") as out:
            for _, _, part_path in ranges:
                with open(part_path, "rb") as part:
                    while True:
                        data = part.read(1024 * 1024)
                        if not data:
                            break
                        out.write(data)
                part_path.unlink(missing_ok=True)

    try:
        await asyncio.to_thread(_merge_parts)
    finally:
        if phase_holder is not None:
            phase_holder["v"] = "download"
        if on_phase:
            on_phase("download")

    try:
        part_dir.rmdir()
    except OSError:
        pass

    return dest


def scrub_parts_dir(
    dest: Path,
    file_size: int,
    *,
    on_log: Optional[Callable[[str], None]] = None,
) -> tuple[int, int, int]:
    """
    清理超大/损坏的分块文件。
    返回 (续传字节数, 已完成分块数, 待下载分块数)。
    """
    part_dir = dest.with_suffix(dest.suffix + ".parts")
    file_size = read_file_size(dest, file_size)
    if not part_dir.is_dir() or file_size <= 0:
        return 0, 0, 0

    slice_size = multipart_slice_for(dest, file_size)
    ranges = _part_ranges(dest, file_size, slice_size)
    fixed = 0
    for s, e, p in ranges:
        if not p.exists():
            continue
        expected = e - s + 1
        if p.stat().st_size > expected:
            p.unlink(missing_ok=True)
            fixed += 1

    complete = incomplete = 0
    resume = 0
    for s, e, p in ranges:
        expected = e - s + 1
        if p.exists():
            size = min(p.stat().st_size, expected)
            resume += size
            if size >= expected:
                complete += 1
            else:
                incomplete += 1
        else:
            incomplete += 1

    resume = min(resume, file_size)
    if on_log:
        on_log(
            f"分块状态: 已完成 {complete}/{len(ranges)}，"
            f"待下载 {incomplete}"
            + (f"，已修复 {fixed} 个异常分块" if fixed else "")
        )
    return resume, complete, incomplete


def merge_partial_parts_to_part_file(
    dest: Path,
    file_size: int,
) -> int:
    """将未完成的 .parts 合并到 .part，供单线程续传；返回已合并字节数。"""
    part_dir = dest.with_suffix(dest.suffix + ".parts")
    part_path = dest.with_suffix(dest.suffix + ".part")
    if not part_dir.is_dir():
        return part_path.stat().st_size if part_path.exists() else 0

    slice_size = multipart_slice_for(dest, file_size)
    ranges = _part_ranges(dest, file_size, slice_size)

    merged = 0
    with open(part_path, "wb") as out:
        for s, e, p in ranges:
            if not p.exists():
                break
            expected = e - s + 1
            size = p.stat().st_size
            if size < expected:
                with open(p, "rb") as f:
                    out.write(f.read())
                merged += size
                break
            with open(p, "rb") as f:
                while True:
                    data = f.read(1024 * 1024)
                    if not data:
                        break
                    out.write(data)
            merged += size

    return merged


async def _download_single(
    client: httpx.AsyncClient,
    url: str,
    dest: Path,
    headers: Dict[str, str],
    file_size: int,
    on_progress: Optional[Callable[[int], None]] = None,
    quiet: bool = False,
    cancel_event: asyncio.Event | None = None,
    abort_event: asyncio.Event | None = None,
) -> Path:
    part_path = dest.with_suffix(dest.suffix + ".part")

    if quiet:
        await _stream_to_file(
            client,
            url,
            headers,
            part_path,
            expected_bytes=file_size,
            on_progress=on_progress,
            cancel_event=cancel_event,
            abort_event=abort_event,
        )
    else:
        with Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TaskProgressColumn(),
        ) as progress:
            task_id = progress.add_task(dest.name, total=file_size or None)
            initial = part_path.stat().st_size if part_path.exists() else 0
            if initial:
                progress.advance(task_id, initial)

            def _cb(n: int) -> None:
                progress.advance(task_id, n)
                if on_progress:
                    on_progress(n)

            await _stream_to_file(
                client,
                url,
                headers,
                part_path,
                expected_bytes=file_size,
                on_progress=_cb,
                cancel_event=cancel_event,
                abort_event=abort_event,
            )

    part_path.replace(dest)
    return dest


async def download_file(
    url: str,
    dest: Path,
    *,
    file_size: int = 0,
    threads: int = 4,
    headers: Optional[Dict[str, str]] = None,
    on_progress: Optional[Callable[[int], None]] = None,
    quiet: bool = False,
    cancel_event: asyncio.Event | None = None,
    http_client: Optional[httpx.AsyncClient] = None,
    on_log: Optional[Callable[[str], None]] = None,
    abort_event: asyncio.Event | None = None,
    on_total_known: Optional[Callable[[int], None]] = None,
    phase_holder: Optional[dict] = None,
    on_phase: Optional[Callable[[str], None]] = None,
) -> Path:
    """
    多线程分块下载；不支持 Range 时回退单线程断点续传。
    多线程失败时不静默回退，由上层刷新链接后重试分块续传。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = _cdn_headers(headers)
    threads = max(1, threads)
    progress_cb = _make_progress_callback(on_progress)

    async def _run(client: httpx.AsyncClient) -> Path:
        fs = file_size
        probed_size, accept_ranges = await _probe_range(client, url, headers)
        fs = reconcile_file_size(dest, fs, probed_size, on_log=on_log)
        if fs <= 0:
            fs = probed_size or file_size
        if fs > 0 and on_total_known:
            on_total_known(fs)

        part_dir = dest.with_suffix(dest.suffix + ".parts")
        part_path = dest.with_suffix(dest.suffix + ".part")
        has_parts = part_dir.is_dir() and any(part_dir.glob("part_*"))
        has_part = part_path.exists() and part_path.stat().st_size > 0

        # 仅有 .part → 单连接续传；有 .parts 或全新下载 → 多连接大块
        if has_part and not has_parts:
            use_multipart = False
        else:
            use_multipart = accept_ranges and fs > 0 and threads > 1

        if use_multipart:
            if on_log:
                mb = multipart_slice_for(dest, fs) // (1024 * 1024)
                on_log(f"多连接下载: {threads} 路并行，每块 {mb}MB")
            result = await _download_multipart(
                client, url, headers, dest, fs, threads,
                on_progress=progress_cb, quiet=quiet,
                cancel_event=cancel_event, abort_event=abort_event,
                on_log=on_log, phase_holder=phase_holder, on_phase=on_phase,
            )
            if progress_cb and hasattr(progress_cb, "flush"):
                progress_cb.flush()
            return result

        result = await _download_single(
            client, url, dest, headers, fs,
            on_progress=progress_cb, quiet=quiet,
            cancel_event=cancel_event, abort_event=abort_event,
        )
        if on_log and fs > 0 and not use_multipart:
            on_log("单连接续传下载")
        if progress_cb and hasattr(progress_cb, "flush"):
            progress_cb.flush()
        return result

    if http_client is not None:
        return await _run(http_client)

    async with _make_client() as client:
        return await _run(client)


async def download_from_file_info(
    file_info: dict,
    dest_dir: Path,
    *,
    threads: int = 4,
    headers: Optional[Dict[str, str]] = None,
    filename: Optional[str] = None,
    on_progress: Optional[Callable[[int], None]] = None,
    quiet: bool = False,
    cancel_event: asyncio.Event | None = None,
    http_client: Optional[httpx.AsyncClient] = None,
    on_log: Optional[Callable[[str], None]] = None,
    abort_event: asyncio.Event | None = None,
    on_total_known: Optional[Callable[[int], None]] = None,
    phase_holder: Optional[dict] = None,
    on_phase: Optional[Callable[[str], None]] = None,
) -> Path:
    url, size = _pick_download_url(file_info)
    name = filename or file_info.get("name") or "download"
    dest = dest_dir / name
    return await download_file(
        url, dest, file_size=size, threads=threads, headers=headers,
        on_progress=on_progress, quiet=quiet, cancel_event=cancel_event,
        http_client=http_client, on_log=on_log, abort_event=abort_event,
        on_total_known=on_total_known, phase_holder=phase_holder,
        on_phase=on_phase,
    )
