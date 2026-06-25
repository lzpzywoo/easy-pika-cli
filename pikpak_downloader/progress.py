"""Progress reporting with throttled UI updates and accurate speed measurement."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable


class ProgressThrottler:
    """
    Speed is measured only from bytes received during the current session window,
    excluding resume/baseline bytes already on disk.
    """

    EMIT_INTERVAL = 0.4
    SPEED_WINDOW = 1.0
    MIN_INSTANT_WINDOW = 0.35

    def __init__(
        self,
        job_id: str,
        get_done: Callable[[], int],
        set_done: Callable[[int], None],
        get_total: Callable[[], int],
        on_emit: Callable[[str, int, int, float], None],
        baseline_bytes: int = 0,
    ) -> None:
        self.job_id = job_id
        self._get_done = get_done
        self._set_done = set_done
        self._get_total = get_total
        self.on_emit = on_emit
        self._last_emit = 0.0
        self._speed_t = time.time()
        self._speed_anchor = baseline_bytes
        self.ema_speed = 0.0

        if baseline_bytes > 0:
            self._set_done(baseline_bytes)

    def _update_ema(self, now: float) -> None:
        dt = now - self._speed_t
        if dt < self.SPEED_WINDOW:
            return
        done = self._get_done()
        new_bytes = done - self._speed_anchor
        instant = new_bytes / dt if dt > 0 and new_bytes > 0 else 0.0
        if instant > 0:
            self.ema_speed = (
                instant if self.ema_speed <= 0
                else 0.25 * instant + 0.75 * self.ema_speed
            )
        self._speed_t = now
        self._speed_anchor = done

    def _display_speed(self, now: float) -> float:
        if self.ema_speed > 0:
            return self.ema_speed
        dt = now - self._speed_t
        if dt >= self.MIN_INSTANT_WINDOW:
            new_bytes = self._get_done() - self._speed_anchor
            if new_bytes > 0:
                return new_bytes / dt
        return 0.0

    def feed(self, nbytes: int) -> None:
        if nbytes <= 0:
            return

        total = self._get_total()
        new_done = self._get_done() + nbytes
        if total > 0:
            new_done = min(new_done, total)
        self._set_done(new_done)
        now = time.time()
        self._update_ema(now)

        if now - self._last_emit >= self.EMIT_INTERVAL:
            self._emit(now)

    def sync_from_disk(self, nbytes: int) -> None:
        """Reset progress to on-disk bytes (e.g. after retry); excludes merge double-count."""
        total = self._get_total()
        nbytes = min(nbytes, total) if total > 0 else nbytes
        self._set_done(nbytes)
        self._speed_anchor = nbytes
        self._speed_t = time.time()
        self._emit(time.time())

    def flush(self) -> None:
        now = time.time()
        self._update_ema(now)
        self._emit(now)

    def tick(self) -> None:
        """Emit current progress/speed even when no new bytes (keeps UI alive)."""
        now = time.time()
        if now - self._last_emit >= self.EMIT_INTERVAL:
            self._emit(now)

    def _emit(self, now: float) -> None:
        self._last_emit = now
        done = self._get_done()
        total = self._get_total()
        if total > 0 and done > total:
            done = total
            self._set_done(done)
        self.on_emit(self.job_id, done, total, self._display_speed(now))


def resume_bytes_for_dest(dest: Path, file_size: int = 0) -> int:
    """Bytes already on disk (.part or .parts), capped per slice and file size."""
    from .downloader import _part_ranges, multipart_slice_for, read_file_size

    file_size = read_file_size(dest, file_size)
    part_dir = dest.with_suffix(dest.suffix + ".parts")
    if part_dir.is_dir():
        parts = sorted(part_dir.glob("part_*"))
        if file_size <= 0:
            return sum(p.stat().st_size for p in parts)

        slice_size = multipart_slice_for(dest, file_size)
        ranges = _part_ranges(dest, file_size, slice_size)
        nbytes = 0
        for s, e, p in ranges:
            if not p.exists():
                break
            expected = e - s + 1
            size = min(p.stat().st_size, expected)
            nbytes += size
            if size < expected:
                break
        return min(nbytes, file_size)

    part = dest.with_suffix(dest.suffix + ".part")
    if part.exists():
        size = part.stat().st_size
        return min(size, file_size) if file_size > 0 else size
    return 0


def validate_dest_dir(path: Path) -> None:
    """Raise OSError if save directory is missing or not writable."""
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".pikpak_write_test"
    try:
        probe.write_text("", encoding="utf-8")
    except OSError as exc:
        free = disk_free_gb(path)
        free_hint = f"（该盘剩余约 {free:.1f} GB）" if free is not None else ""
        raise OSError(
            f"无法写入保存目录 {path} {free_hint}: {exc}"
        ) from exc
    finally:
        probe.unlink(missing_ok=True)


def disk_free_gb(path: Path) -> float | None:
    from .session import disk_free_gb as _disk_free_gb
    return _disk_free_gb(path)
