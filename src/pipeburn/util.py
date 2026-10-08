"""Small formatting and rate-tracking helpers (standard library only)."""

from __future__ import annotations

import time
from collections import deque
from typing import Callable, Optional

_UNITS = ("B", "KiB", "MiB", "GiB", "TiB")


def human_bytes(n: float) -> str:
    """1536 -> '1.5 KiB'."""
    value = float(n)
    for unit in _UNITS:
        if abs(value) < 1024 or unit == _UNITS[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def human_duration(seconds: Optional[float]) -> str:
    """75 -> '01:15', 3700 -> '1:01:40', None -> '--:--'."""
    if seconds is None or seconds < 0:
        return "--:--"
    whole = int(round(seconds))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class RateMeter:
    """Moving-window throughput estimate from cumulative byte counts."""

    def __init__(self, window: float = 5.0, clock: Callable[[], float] = time.monotonic):
        self._window = window
        self._clock = clock
        self._samples: deque[tuple[float, int]] = deque()

    def add(self, total_bytes: int) -> None:
        now = self._clock()
        self._samples.append((now, total_bytes))
        cutoff = now - self._window
        while len(self._samples) > 2 and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def speed(self) -> float:
        """Bytes per second over the window (0.0 until two samples exist)."""
        if len(self._samples) < 2:
            return 0.0
        (t0, b0), (t1, b1) = self._samples[0], self._samples[-1]
        elapsed = t1 - t0
        return (b1 - b0) / elapsed if elapsed > 0 else 0.0

    def eta(self, done: int, total: Optional[int]) -> Optional[float]:
        rate = self.speed()
        if not total or rate <= 0:
            return None
        return max(total - done, 0) / rate
